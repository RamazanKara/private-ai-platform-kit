#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
import yaml


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    passed: bool
    latency_ms: float
    checks: list[str]
    failures: list[str]
    response_text: str


def load_suite(path: Path) -> dict[str, Any]:
    suite = yaml.safe_load(path.read_text())
    if not isinstance(suite, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return suite


def validate_suite(suite: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if suite.get("apiVersion") != "platform.ai/v1alpha1":
        errors.append("apiVersion must be platform.ai/v1alpha1")
    if suite.get("kind") != "EvalSuite":
        errors.append("kind must be EvalSuite")
    spec = suite.get("spec")
    if not isinstance(spec, dict):
        errors.append("spec must be a mapping")
        return errors
    if not spec.get("model"):
        errors.append("spec.model is required")
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
        messages = case.get("messages")
        if not isinstance(messages, list) or not messages:
            errors.append(f"case {case_id or index} must define messages")
        else:
            for message_index, message in enumerate(messages):
                if not isinstance(message, dict):
                    errors.append(f"case {case_id or index} message {message_index} must be a mapping")
                    continue
                if message.get("role") not in {"system", "user", "assistant", "tool"}:
                    errors.append(f"case {case_id or index} message {message_index} has invalid role")
                if not isinstance(message.get("content"), str) or not message.get("content"):
                    errors.append(f"case {case_id or index} message {message_index} must define content")
        checks = case.get("checks", {})
        if checks is not None and not isinstance(checks, dict):
            errors.append(f"case {case_id or index} checks must be a mapping")
        if isinstance(checks, dict) and "containsAny" in checks:
            contains_any = checks["containsAny"]
            if not isinstance(contains_any, list) or not all(isinstance(item, str) for item in contains_any):
                errors.append(f"case {case_id or index} checks.containsAny must be a list of strings")
        if isinstance(checks, dict) and "forbiddenAny" in checks:
            forbidden_any = checks["forbiddenAny"]
            if not isinstance(forbidden_any, list) or not all(isinstance(item, str) for item in forbidden_any):
                errors.append(f"case {case_id or index} checks.forbiddenAny must be a list of strings")
        if isinstance(checks, dict) and "maxChars" in checks:
            max_chars = checks["maxChars"]
            if not isinstance(max_chars, int) or max_chars <= 0:
                errors.append(f"case {case_id or index} checks.maxChars must be a positive integer")
    return errors


def response_text(payload: dict[str, Any]) -> str:
    try:
        return str(payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("gateway response did not contain choices[0].message.content") from exc


def evaluate_case(
    client: httpx.Client,
    gateway_url: str,
    suite_name: str,
    model: str,
    defaults: dict[str, Any],
    case: dict[str, Any],
    api_key: str | None,
) -> EvalResult:
    case_id = str(case["id"])
    sandbox_id = str(case.get("sandboxId") or defaults.get("sandboxId") or "eval-lab")
    max_tokens = int(case.get("maxTokens") or defaults.get("maxTokens") or 128)
    temperature = float(case.get("temperature") if case.get("temperature") is not None else defaults.get("temperature", 0))
    max_latency_ms = float(case.get("maxLatencyMs") or defaults.get("maxLatencyMs") or 30000)
    checks = case.get("checks") or {}
    request_id = f"eval-{suite_name}-{case_id}"
    start = perf_counter()
    try:
        headers = {
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
            "X-Sandbox-ID": sandbox_id,
        }
        if api_key:
            headers["X-API-Key"] = api_key
        response = client.post(
            f"{gateway_url.rstrip('/')}/v1/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": case["messages"],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        latency_ms = round((perf_counter() - start) * 1000, 2)
        response.raise_for_status()
        text = response_text(response.json())
    except Exception as exc:
        latency_ms = round((perf_counter() - start) * 1000, 2)
        return EvalResult(
            case_id=case_id,
            passed=False,
            latency_ms=latency_ms,
            checks=["httpStatus"],
            failures=[str(exc)],
            response_text="",
        )

    failures: list[str] = []
    executed_checks = ["httpStatus"]
    min_chars = int(checks.get("minChars", 1))
    executed_checks.append("minChars")
    if len(text.strip()) < min_chars:
        failures.append(f"response length {len(text.strip())} is below minChars {min_chars}")
    max_chars = checks.get("maxChars")
    if max_chars:
        executed_checks.append("maxChars")
        if len(text.strip()) > int(max_chars):
            failures.append(f"response length {len(text.strip())} is above maxChars {max_chars}")
    if latency_ms > max_latency_ms:
        executed_checks.append("maxLatencyMs")
        failures.append(f"latency {latency_ms}ms exceeded maxLatencyMs {max_latency_ms}ms")
    contains_any = checks.get("containsAny")
    if contains_any:
        executed_checks.append("containsAny")
        lower_text = text.lower()
        if not any(expected.lower() in lower_text for expected in contains_any):
            failures.append(f"response did not contain any expected text: {contains_any}")
    forbidden_any = checks.get("forbiddenAny")
    if forbidden_any:
        executed_checks.append("forbiddenAny")
        lower_text = text.lower()
        leaked = [item for item in forbidden_any if item.lower() in lower_text]
        if leaked:
            failures.append(f"response contained forbidden text: {leaked}")

    return EvalResult(
        case_id=case_id,
        passed=not failures,
        latency_ms=latency_ms,
        checks=executed_checks,
        failures=failures,
        response_text=text,
    )


def write_markdown(path: Path, suite_name: str, gateway_url: str, results: list[EvalResult]) -> None:
    passed = sum(1 for result in results if result.passed)
    lines = [
        f"# Evaluation Summary: {suite_name}",
        "",
        f"Gateway: `{gateway_url}`",
        "",
        "| Case | Status | Latency ms | Checks |",
        "| --- | --- | ---: | --- |",
    ]
    for result in results:
        status = "pass" if result.passed else "fail"
        checks = ", ".join(result.checks)
        lines.append(f"| {result.case_id} | {status} | {result.latency_ms:.2f} | {checks} |")
    lines.extend(["", f"Overall: {passed} passed, {len(results) - passed} failed."])
    failures = [result for result in results if not result.passed]
    if failures:
        lines.append("")
        lines.append("## Failures")
        lines.append("")
        for result in failures:
            lines.append(f"- {result.case_id}: {'; '.join(result.failures)}")
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or validate an AI Platform Ops Lab eval suite.")
    parser.add_argument("--suite", default="evals/smoke-suite.yaml")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:18082")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--api-key")
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()

    suite_path = Path(args.suite)
    suite = load_suite(suite_path)
    errors = validate_suite(suite)
    if errors:
        raise SystemExit("\n".join(errors))
    if args.check_config:
        print(f"eval suite OK: {suite_path} ({len(suite['spec']['cases'])} case(s))")
        return 0

    suite_name = suite.get("metadata", {}).get("name", suite_path.stem)
    spec = suite["spec"]
    defaults = spec.get("defaults") or {}
    with httpx.Client(timeout=60) as client:
        results = [
            evaluate_case(client, args.gateway_url, suite_name, spec["model"], defaults, case, args.api_key)
            for case in spec["cases"]
        ]

    payload = {
        "suite": suite_name,
        "gateway_url": args.gateway_url,
        "generated_at": datetime.now(UTC).isoformat(),
        "results": [
            {
                "case_id": result.case_id,
                "passed": result.passed,
                "latency_ms": result.latency_ms,
                "checks": result.checks,
                "failures": result.failures,
                "response_chars": len(result.response_text),
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
        write_markdown(output_md, suite_name, args.gateway_url, results)

    failed = [result for result in results if not result.passed]
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.case_id} {result.latency_ms:.2f}ms")
        for failure in result.failures:
            print(f"  - {failure}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
