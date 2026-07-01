#!/usr/bin/env python3
"""Retrieval-quality evaluation for the RAG service.

The chat eval harness (scripts/eval-suite.py) only exercises /v1/chat/completions, so an
embedding or chunking change could silently destroy retrieval quality and still pass every
release gate. This runner scores the RAG service's /v1/rag/query against a labeled golden
set using recall@k, MRR, and nDCG@k, plus a grounding rate (the fraction of queries whose
top-k contains at least one relevant document), and fails when aggregate metrics fall below
the suite's thresholds.

Modes:
  --check-config   Validate the suite file offline (no live service). Used by `make validate`.
  --selftest       Run the metric functions against a fixed example and assert known values.
  (default)        Query a live RAG service and score the suite, writing JSON/MD evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.:/-]*")


def _tokens(text: str) -> set[str]:
    """Return the lowercased alphanumeric token set of the text."""
    return set(_TOKEN_PATTERN.findall(text.lower()))


def recall_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    """Fraction of the relevant documents that appear in the top-k retrieved ids."""
    relevant_set = set(relevant)
    if not relevant_set:
        return 1.0
    hits = relevant_set & set(retrieved[:k])
    return len(hits) / len(relevant_set)


def context_precision_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    """Fraction of the top-k retrieved documents that are relevant (precision@k).

    Complements recall@k: recall asks "did we find the relevant docs", precision asks
    "how much of what we returned was on-topic" — a RAGAS-style context-precision proxy.
    """
    top = retrieved[:k]
    if not top:
        return 0.0
    relevant_set = set(relevant)
    return sum(1 for doc_id in top if doc_id in relevant_set) / len(top)


def answer_support(answer: str, context: str) -> float:
    """Lexical groundedness proxy: fraction of answer tokens present in the retrieved context.

    A deterministic, offline stand-in for a RAGAS faithfulness score: it measures how much of
    a ground-truth answer is actually supported by the retrieved context, so an embedding,
    chunking, or prompt change that stops surfacing the supporting passages is caught. The
    scorer is intentionally isolated so an LLM-judge or NLI model can replace it without
    changing the runner or the suite schema.
    """
    answer_tokens = _tokens(answer)
    if not answer_tokens:
        return 1.0
    context_tokens = _tokens(context)
    return len(answer_tokens & context_tokens) / len(answer_tokens)


def reciprocal_rank(retrieved: list[str], relevant: list[str], k: int) -> float:
    """Reciprocal of the rank (1-based) of the first relevant document in the top-k."""
    relevant_set = set(relevant)
    for index, doc_id in enumerate(retrieved[:k], start=1):
        if doc_id in relevant_set:
            return 1.0 / index
    return 0.0


def ndcg_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    """Binary-relevance nDCG@k of the retrieved ranking against the relevant set."""
    relevant_set = set(relevant)
    dcg = sum(
        1.0 / math.log2(index + 1) for index, doc_id in enumerate(retrieved[:k], start=1) if doc_id in relevant_set
    )
    ideal_hits = min(len(relevant_set), k)
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    query: str
    relevant: list[str]
    retrieved: list[str]
    recall: float
    mrr: float
    ndcg: float
    grounded: bool
    context_precision: float = 0.0
    faithfulness: float | None = None
    error: str | None = None


def validate_suite(suite: dict[str, Any]) -> list[str]:
    """Return a list of structural errors in the RAG eval suite (empty when valid)."""
    errors: list[str] = []
    if suite.get("apiVersion") != "platform.ai/v1alpha1":
        errors.append("apiVersion must be platform.ai/v1alpha1")
    if suite.get("kind") != "RagEvalSuite":
        errors.append("kind must be RagEvalSuite")
    spec = suite.get("spec")
    if not isinstance(spec, dict):
        errors.append("spec must be a mapping")
        return errors
    thresholds = spec.get("thresholds", {})
    if not isinstance(thresholds, dict):
        errors.append("spec.thresholds must be a mapping")
    cases = spec.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("spec.cases must be a non-empty list")
        return errors
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f"case {index} must be a mapping")
            continue
        case_id = case.get("id")
        if not case_id:
            errors.append(f"case {index} must define id")
        elif case_id in seen:
            errors.append(f"duplicate case id {case_id}")
        else:
            seen.add(case_id)
        if not isinstance(case.get("query"), str) or not case.get("query"):
            errors.append(f"case {case_id or index} must define a non-empty query")
        relevant = case.get("relevant")
        if not isinstance(relevant, list) or not relevant or not all(isinstance(item, str) for item in relevant):
            errors.append(f"case {case_id or index} must define relevant as a non-empty list of document ids")
        if "answer" in case and not isinstance(case.get("answer"), str):
            errors.append(f"case {case_id or index} answer must be a string when present")
    return errors


def evaluate_case(
    client: httpx.Client,
    rag_url: str,
    top_k: int,
    sandbox_id: str,
    api_key: str | None,
    case: dict[str, Any],
) -> CaseResult:
    """Query the live RAG service for one case and score it against its relevant ids."""
    case_id = str(case["id"])
    query = str(case["query"])
    relevant = [str(item) for item in case["relevant"]]
    expected_answer = case.get("answer")
    headers = {
        "Content-Type": "application/json",
        "X-Request-ID": f"rag-eval-{case_id}",
        "X-Sandbox-ID": sandbox_id,
    }
    if api_key:
        headers["X-API-Key"] = api_key
    try:
        response = client.post(
            f"{rag_url.rstrip('/')}/v1/rag/query",
            headers=headers,
            json={"query": query, "top_k": top_k, "include_context": True, "include_messages": False},
        )
        response.raise_for_status()
        body = response.json()
        results = body.get("results", [])
        retrieved = [str(item.get("id")) for item in results if isinstance(item, dict)]
        context = str(body.get("context") or "")
        if not context:
            context = " ".join(str(item.get("excerpt") or "") for item in results if isinstance(item, dict))
    except Exception as exc:
        # Any failure (HTTP error, bad JSON) scores the case zero with the reason recorded.
        return CaseResult(case_id, query, relevant, [], 0.0, 0.0, 0.0, False, error=str(exc))
    # Faithfulness/groundedness is only defined when the case ships a ground-truth answer.
    faithfulness = answer_support(str(expected_answer), context) if isinstance(expected_answer, str) else None
    return CaseResult(
        case_id=case_id,
        query=query,
        relevant=relevant,
        retrieved=retrieved,
        recall=recall_at_k(retrieved, relevant, top_k),
        mrr=reciprocal_rank(retrieved, relevant, top_k),
        ndcg=ndcg_at_k(retrieved, relevant, top_k),
        grounded=bool(set(retrieved[:top_k]) & set(relevant)),
        context_precision=context_precision_at_k(retrieved, relevant, top_k),
        faithfulness=faithfulness,
    )


def aggregate(results: list[CaseResult]) -> dict[str, float]:
    """Return mean recall/MRR/nDCG, retrieval hit rate, context precision, and faithfulness.

    ``retrieval_hit_rate`` (formerly the misnamed "grounding_rate") is a pure retrieval
    metric: the fraction of queries whose top-k contains a relevant document. Generation
    ``faithfulness`` is averaged only over cases that ship a ground-truth answer; when no
    case does, it reports 1.0 (not applicable) so it never falsely fails a gate.
    """
    count = len(results) or 1
    graded = [result.faithfulness for result in results if result.faithfulness is not None]
    return {
        "recall_at_k": sum(result.recall for result in results) / count,
        "mrr": sum(result.mrr for result in results) / count,
        "ndcg_at_k": sum(result.ndcg for result in results) / count,
        "retrieval_hit_rate": sum(1 for result in results if result.grounded) / count,
        "context_precision": sum(result.context_precision for result in results) / count,
        "faithfulness": (sum(graded) / len(graded)) if graded else 1.0,
    }


def check_thresholds(metrics: dict[str, float], thresholds: dict[str, Any]) -> list[str]:
    """Return a list of threshold failures (empty when all aggregate metrics pass)."""
    mapping = {
        "minRecallAtK": "recall_at_k",
        "minMrr": "mrr",
        "minNdcgAtK": "ndcg_at_k",
        # minGroundingRate kept as a back-compatible alias for the renamed metric.
        "minGroundingRate": "retrieval_hit_rate",
        "minRetrievalHitRate": "retrieval_hit_rate",
        "minContextPrecision": "context_precision",
        "minFaithfulness": "faithfulness",
    }
    failures: list[str] = []
    for key, metric in mapping.items():
        if key in thresholds:
            floor = float(thresholds[key])
            if metrics[metric] + 1e-9 < floor:
                failures.append(f"{metric} {metrics[metric]:.3f} below {key} {floor:.3f}")
    return failures


def write_markdown(path: Path, suite_name: str, metrics: dict[str, float], results: list[CaseResult]) -> None:
    lines = [
        f"# RAG Retrieval Evaluation: {suite_name}",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| recall@k | {metrics['recall_at_k']:.3f} |",
        f"| MRR | {metrics['mrr']:.3f} |",
        f"| nDCG@k | {metrics['ndcg_at_k']:.3f} |",
        f"| retrieval hit rate | {metrics['retrieval_hit_rate']:.3f} |",
        f"| context precision | {metrics['context_precision']:.3f} |",
        f"| faithfulness | {metrics['faithfulness']:.3f} |",
        "",
        "| Case | Recall | MRR | nDCG | Precision | Faithfulness |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        faith = f"{result.faithfulness:.3f}" if result.faithfulness is not None else "n/a"
        lines.append(
            f"| {result.case_id} | {result.recall:.3f} | {result.mrr:.3f} | "
            f"{result.ndcg:.3f} | {result.context_precision:.3f} | {faith} |"
        )
    path.write_text("\n".join(lines) + "\n")


def selftest() -> int:
    """Assert the metric functions on a fixed example; return non-zero on mismatch."""
    retrieved = ["accelerators", "controls", "coding-agents"]
    relevant = ["controls"]
    assert recall_at_k(retrieved, relevant, 3) == 1.0
    assert recall_at_k(retrieved, relevant, 1) == 0.0
    assert abs(reciprocal_rank(retrieved, relevant, 3) - 0.5) < 1e-9
    assert abs(ndcg_at_k(retrieved, relevant, 3) - (1.0 / math.log2(3))) < 1e-9
    assert recall_at_k(["x"], [], 3) == 1.0
    assert context_precision_at_k(retrieved, relevant, 3) == 1.0 / 3
    assert context_precision_at_k([], relevant, 3) == 0.0
    assert answer_support("inference gateway routes", "the inference gateway routes traffic") == 1.0
    assert abs(answer_support("alpha beta", "only alpha here") - 0.5) < 1e-9
    assert answer_support("", "anything") == 1.0
    print("rag-eval selftest OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Score RAG retrieval quality against a golden set.")
    parser.add_argument("--suite", default="platform/evals/rag-retrieval-suite.yaml")
    parser.add_argument("--rag-url", default="http://127.0.0.1:18083")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--api-key")
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    suite_path = Path(args.suite)
    suite = yaml.safe_load(suite_path.read_text())
    if not isinstance(suite, dict):
        raise SystemExit(f"{suite_path} must contain a YAML mapping")
    errors = validate_suite(suite)
    if errors:
        raise SystemExit("\n".join(errors))
    if args.check_config:
        print(f"rag eval suite OK: {suite_path} ({len(suite['spec']['cases'])} case(s))")
        return 0

    spec = suite["spec"]
    suite_name = suite.get("metadata", {}).get("name", suite_path.stem)
    defaults = spec.get("defaults") or {}
    top_k = int(defaults.get("topK", 3))
    sandbox_id = str(defaults.get("sandboxId", "eval-lab"))
    thresholds = spec.get("thresholds") or {}

    with httpx.Client(timeout=60) as client:
        results = [evaluate_case(client, args.rag_url, top_k, sandbox_id, args.api_key, case) for case in spec["cases"]]
    metrics = aggregate(results)
    failures = check_thresholds(metrics, thresholds)

    payload = {
        "suite": suite_name,
        "rag_url": args.rag_url,
        "generated_at": datetime.now(UTC).isoformat(),
        "metrics": metrics,
        "thresholds": thresholds,
        "results": [
            {
                "case_id": result.case_id,
                "recall_at_k": result.recall,
                "mrr": result.mrr,
                "ndcg_at_k": result.ndcg,
                "grounded": result.grounded,
                "context_precision": result.context_precision,
                "faithfulness": result.faithfulness,
                "retrieved": result.retrieved,
                "error": result.error,
            }
            for result in results
        ],
    }
    if args.output_json:
        output_json = Path(args.output_json)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    if args.output_md:
        output_md = Path(args.output_md)
        output_md.parent.mkdir(parents=True, exist_ok=True)
        write_markdown(output_md, suite_name, metrics, results)

    print(
        f"recall@k={metrics['recall_at_k']:.3f} mrr={metrics['mrr']:.3f} "
        f"ndcg@k={metrics['ndcg_at_k']:.3f} hit_rate={metrics['retrieval_hit_rate']:.3f} "
        f"precision={metrics['context_precision']:.3f} faithfulness={metrics['faithfulness']:.3f}"
    )
    for failure in failures:
        print(f"  - {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
