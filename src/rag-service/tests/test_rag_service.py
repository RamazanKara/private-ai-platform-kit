import asyncio
import hashlib
import json
import logging

import httpx
import pytest
from app.embeddings import HashEmbeddingProvider, OpenAICompatibleEmbeddingProvider
from app.ingest import build_chunks, load_manifest
from app.main import create_app
from app.retriever import QdrantRetriever
from app.settings import Settings
from fastapi.testclient import TestClient


def write_doc(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_healthz_reports_loaded_documents(tmp_path):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")
    client = TestClient(create_app(Settings(document_dir=tmp_path)))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "documents": 1,
        "retrieval_backend": "lexical",
        "vector_store_configured": False,
        "source_manifest_configured": False,
    }


def test_healthz_reports_qdrant_backend_without_network_call(tmp_path):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")
    settings = Settings(
        document_dir=tmp_path,
        retrieval_backend="qdrant",
        vector_store_url="http://qdrant-vector-store.vector.svc.cluster.local:6333",
        vector_collection="customer-platform-knowledge",
    )
    client = TestClient(create_app(settings))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["retrieval_backend"] == "qdrant"
    assert response.json()["vector_store_configured"] is True
    assert response.json()["vector_store"]["collection"] == "customer-platform-knowledge"
    assert response.json()["vector_store"]["collection_version"] == "v1"
    assert response.json()["vector_store"]["vector_dimensions"] == 384
    assert response.json()["vector_store"]["embedding_provider"] == "hash"


def test_settings_load_qdrant_environment_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DOCUMENT_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_RETRIEVAL_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant-vector-store.vector.svc.cluster.local:6333")
    monkeypatch.setenv("QDRANT_COLLECTION", "customer-platform-knowledge")
    monkeypatch.setenv("QDRANT_COLLECTION_VERSION", "v2026-06")
    monkeypatch.setenv("QDRANT_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("QDRANT_VECTOR_DIMENSIONS", "512")
    monkeypatch.setenv("QDRANT_BOOTSTRAP_FROM_KNOWLEDGE", "false")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "openai-compatible")
    monkeypatch.setenv("RAG_EMBEDDING_BASE_URL", "http://embeddings.local:8080")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "bge-small-private")
    monkeypatch.setenv("RAG_SOURCE_MANIFEST", str(tmp_path / "sources.yaml"))

    settings = Settings.from_env()

    assert settings.retrieval_backend == "qdrant"
    assert settings.vector_store_url.endswith(":6333")
    assert settings.vector_collection == "customer-platform-knowledge"
    assert settings.vector_collection_version == "v2026-06"
    assert settings.vector_timeout_seconds == 2.5
    assert settings.vector_dimensions == 512
    assert settings.vector_bootstrap_enabled is False
    assert settings.embedding_provider == "openai-compatible"
    assert settings.embedding_base_url == "http://embeddings.local:8080"
    assert settings.embedding_model == "bge-small-private"
    assert settings.rag_source_manifest == tmp_path / "sources.yaml"


def test_hash_embedding_provider_is_deterministic():
    provider = HashEmbeddingProvider(dimensions=16)

    first = provider.embed("gateway budget controls")
    second = provider.embed("gateway budget controls")

    assert first == second
    assert len(first) == 16
    assert any(value != 0 for value in first)


def test_openai_compatible_embedding_provider_validates_dimensions(monkeypatch):
    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def post(self, url, json):
            assert url == "http://embeddings.local/v1/embeddings"
            assert json["model"] == "private-embedding"
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json={"data": [{"embedding": [0.1, 0.2, 0.3]}]},
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    provider = OpenAICompatibleEmbeddingProvider(
        "http://embeddings.local",
        "private-embedding",
        dimensions=3,
        timeout_seconds=1,
    )

    assert provider.embed("hello") == [0.1, 0.2, 0.3]


def test_rag_source_manifest_builds_metadata_chunks(tmp_path):
    source_dir = tmp_path / "docs"
    source_dir.mkdir()
    write_doc(source_dir, "controls.md", "# Controls\nBudgets and routing are enforced.")
    manifest = tmp_path / "sources.yaml"
    manifest.write_text(
        json.dumps(
            {
                "apiVersion": "platform.ai/v1alpha1",
                "kind": "RagSourceManifest",
                "metadata": {"name": "local-docs"},
                "spec": {
                    "sources": [
                        {
                            "id": "controls",
                            "source": "docs",
                            "classification": "confidential",
                            "retentionClass": "platform-docs",
                            "owner": "platform-team",
                            "embeddingModel": "hash-text-v1",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    sources = load_manifest(manifest)
    chunks = build_chunks(sources, chunk_chars=80, overlap_chars=10)

    assert len(sources) == 1
    assert len(chunks) == 1
    assert chunks[0].payload()["classification"] == "confidential"
    assert chunks[0].payload()["retentionClass"] == "platform-docs"
    assert chunks[0].payload()["collection_version"] == "v1"


def test_qdrant_retriever_bootstraps_and_queries_with_rest_api(tmp_path, monkeypatch):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/collections/lab":
            return httpx.Response(404)
        if request.method == "PUT" and request.url.path == "/collections/lab":
            body = json.loads(request.content)
            assert body["vectors"]["size"] == 16
            assert body["vectors"]["distance"] == "Cosine"
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "PUT" and request.url.path == "/collections/lab/points":
            body = json.loads(request.content)
            assert body["points"][0]["payload"]["document_id"] == "agents"
            assert body["points"][0]["payload"]["collection_version"] == "v1"
            assert len(body["points"][0]["vector"]) == 16
            return httpx.Response(200, json={"status": "ok", "result": {"status": "acknowledged"}})
        if request.method == "POST" and request.url.path == "/collections/lab/points/query":
            body = json.loads(request.content)
            # Over-fetch candidates (top_k * candidate_multiplier) for the hybrid rerank.
            assert body["limit"] == 4
            assert body["with_payload"] is True
            assert len(body["query"]) == 16
            assert body["filter"]["must"][0]["key"] == "collection_version"
            assert body["filter"]["must"][0]["match"]["value"] == "v1"
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "result": {
                        "points": [
                            {
                                "id": "fb5c1e4e-c28a-5959-b033-62d1b7edda61",
                                "score": 0.87,
                                "payload": {
                                    "document_id": "agents",
                                    "title": "Coding Agents",
                                    "source": "agents.md",
                                    "content": "Coding agents should use the gateway.",
                                },
                            }
                        ]
                    },
                },
            )
        return httpx.Response(500, json={"unexpected": request.url.path})

    retriever = QdrantRetriever.from_directory(
        tmp_path,
        "http://qdrant.local:6333",
        "lab",
        "v1",
        timeout_seconds=1.0,
        vector_dimensions=16,
        bootstrap_from_knowledge=True,
    )
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(retriever, "_client", lambda: httpx.AsyncClient(transport=transport))

    results = asyncio.run(retriever.query("gateway", top_k=1, max_context_chars=200))

    assert results[0].document.id == "agents"
    # Hybrid score: 0.5 * dense(0.87) + 0.5 * lexical_overlap(1.0) = 0.935.
    assert results[0].score == pytest.approx(0.935)
    assert ("PUT", "/collections/lab/points") in calls
    assert ("POST", "/collections/lab/points/query") in calls


def test_qdrant_hybrid_rerank_promotes_lexically_relevant_doc(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/collections/lab/points/query":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "points": [
                            {
                                "id": "11111111-1111-5111-8111-111111111111",
                                "score": 0.90,
                                "payload": {"document_id": "unrelated", "content": "weather and cooking tips"},
                            },
                            {
                                "id": "22222222-2222-5222-8222-222222222222",
                                "score": 0.70,
                                "payload": {
                                    "document_id": "gateway-doc",
                                    "content": "the inference gateway routes traffic",
                                },
                            },
                        ]
                    }
                },
            )
        return httpx.Response(500)

    retriever = QdrantRetriever.from_directory(
        tmp_path,
        "http://qdrant.local:6333",
        "lab",
        "v1",
        timeout_seconds=1.0,
        vector_dimensions=16,
        bootstrap_from_knowledge=False,
    )
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(retriever, "_client", lambda: httpx.AsyncClient(transport=transport))

    results = asyncio.run(retriever.query("inference gateway", top_k=2, max_context_chars=200))

    # Dense ranks "unrelated" (0.90) first, but it has zero query-term overlap; the
    # hybrid rerank promotes the lexically-relevant "gateway-doc".
    assert results[0].document.id == "gateway-doc"


def test_qdrant_classification_filter_scopes_retrieval(tmp_path, monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/collections/lab/points/query":
            captured["filter"] = json.loads(request.content)["filter"]
            return httpx.Response(200, json={"result": {"points": []}})
        return httpx.Response(500)

    retriever = QdrantRetriever.from_directory(
        tmp_path,
        "http://qdrant.local:6333",
        "lab",
        "v1",
        timeout_seconds=1.0,
        vector_dimensions=16,
        bootstrap_from_knowledge=False,
        allowed_classifications=("internal", "public"),
    )
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(retriever, "_client", lambda: httpx.AsyncClient(transport=transport))

    asyncio.run(retriever.query("anything", top_k=1, max_context_chars=200))

    conditions = captured["filter"]["must"]
    classification = next(c for c in conditions if c["key"] == "classification")
    assert classification["match"]["any"] == ["internal", "public"]


def test_qdrant_tenant_isolation_scopes_retrieval_to_caller(tmp_path, monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/collections/lab/points/query":
            captured["filter"] = json.loads(request.content)["filter"]
            return httpx.Response(200, json={"result": {"points": []}})
        return httpx.Response(500)

    retriever = QdrantRetriever.from_directory(
        tmp_path,
        "http://qdrant.local:6333",
        "lab",
        "v1",
        timeout_seconds=1.0,
        vector_dimensions=16,
        bootstrap_from_knowledge=False,
        tenant_isolation_enabled=True,
        tenant_field="owner",
    )
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(retriever, "_client", lambda: httpx.AsyncClient(transport=transport))

    asyncio.run(retriever.query("anything", top_k=1, max_context_chars=200, tenant="team-a"))

    conditions = captured["filter"]["must"]
    owner = next(c for c in conditions if c["key"] == "owner")
    assert owner["match"]["value"] == "team-a"


def test_qdrant_tenant_isolation_disabled_omits_owner_filter(tmp_path, monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/collections/lab/points/query":
            captured["filter"] = json.loads(request.content)["filter"]
            return httpx.Response(200, json={"result": {"points": []}})
        return httpx.Response(500)

    retriever = QdrantRetriever.from_directory(
        tmp_path,
        "http://qdrant.local:6333",
        "lab",
        "v1",
        timeout_seconds=1.0,
        vector_dimensions=16,
        bootstrap_from_knowledge=False,
    )
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(retriever, "_client", lambda: httpx.AsyncClient(transport=transport))

    asyncio.run(retriever.query("anything", top_k=1, max_context_chars=200, tenant="team-a"))

    keys = {c["key"] for c in captured["filter"]["must"]}
    assert "owner" not in keys


def _two_point_handler():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/collections/lab/points/query":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "points": [
                            {"id": "a", "score": 0.95, "payload": {"document_id": "dense-top", "content": "alpha"}},
                            {"id": "b", "score": 0.60, "payload": {"document_id": "rerank-top", "content": "beta"}},
                        ]
                    }
                },
            )
        return httpx.Response(500)

    return handler


def test_qdrant_reranker_reorders_candidates(tmp_path, monkeypatch):
    class FakeReranker:
        name = "openai-compatible"
        model = "x"

        async def rerank_async(self, query, documents):
            # Promote the second (dense-lower) candidate: score by "beta" presence.
            return [1.0 if "beta" in doc else 0.0 for doc in documents]

    retriever = QdrantRetriever.from_directory(
        tmp_path,
        "http://qdrant.local:6333",
        "lab",
        "v1",
        timeout_seconds=1.0,
        vector_dimensions=16,
        bootstrap_from_knowledge=False,
        lexical_weight=0.0,
        reranker_provider=FakeReranker(),
    )
    transport = httpx.MockTransport(_two_point_handler())
    monkeypatch.setattr(retriever, "_client", lambda: httpx.AsyncClient(transport=transport))

    results = asyncio.run(retriever.query("query", top_k=2, max_context_chars=200))
    assert results[0].document.id == "rerank-top"


def test_qdrant_reranker_failure_falls_back_to_hybrid_order(tmp_path, monkeypatch):
    class BrokenReranker:
        name = "openai-compatible"
        model = "x"

        async def rerank_async(self, query, documents):
            raise httpx.ConnectError("reranker down")

    retriever = QdrantRetriever.from_directory(
        tmp_path,
        "http://qdrant.local:6333",
        "lab",
        "v1",
        timeout_seconds=1.0,
        vector_dimensions=16,
        bootstrap_from_knowledge=False,
        lexical_weight=0.0,
        reranker_provider=BrokenReranker(),
    )
    transport = httpx.MockTransport(_two_point_handler())
    monkeypatch.setattr(retriever, "_client", lambda: httpx.AsyncClient(transport=transport))

    # Reranker outage must not fail the query; the hybrid (dense) order is kept.
    results = asyncio.run(retriever.query("query", top_k=2, max_context_chars=200))
    assert results[0].document.id == "dense-top"


def test_build_reranker_provider_and_parse():
    from app.reranker import NoopReranker, OpenAICompatibleReranker, build_reranker_provider

    assert isinstance(build_reranker_provider("none", "", "", 2.0), NoopReranker)
    provider = build_reranker_provider("openai-compatible", "http://rerank:8080", "bge-reranker", 2.0)
    assert isinstance(provider, OpenAICompatibleReranker)
    scores = provider._parse({"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "score": 0.2}]}, 2)
    assert scores == [0.2, 0.9]
    with pytest.raises(ValueError):
        build_reranker_provider("openai-compatible", "", "m", 2.0)
    with pytest.raises(ValueError):
        provider._parse({"no": "results"}, 2)


def test_rag_query_returns_grounded_messages_and_trace_headers(tmp_path):
    write_doc(
        tmp_path,
        "coding-agents.md",
        "# Coding Agents\nCoding agents must send X-Request-ID and X-Sandbox-ID to the inference gateway.",
    )
    write_doc(tmp_path, "restore.md", "# Restore\nrestore-drill validates backups.")
    app = create_app(Settings(document_dir=tmp_path, default_sandbox_id="local-lab"))
    client = TestClient(app)
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    response = client.post(
        "/v1/rag/query",
        headers={
            "X-Request-ID": "rag-req-1",
            "X-Sandbox-ID": "agent-lab",
            "traceparent": traceparent,
        },
        json={"query": "How should coding agents call the gateway?", "top_k": 1},
    )

    body = response.json()
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "rag-req-1"
    assert response.headers["X-Sandbox-ID"] == "agent-lab"
    assert response.headers["traceparent"] == traceparent
    assert body["sandbox_id"] == "agent-lab"
    assert body["retrieval_backend"] == "lexical"
    assert body["results"][0]["id"] == "coding-agents"
    assert "X-Request-ID" in body["context"]
    assert body["grounded_messages"][0]["role"] == "system"
    assert body["grounded_messages"][-1]["content"] == "How should coding agents call the gateway?"


def test_rag_query_rejects_invalid_sandbox_id(tmp_path):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")
    client = TestClient(create_app(Settings(document_dir=tmp_path)))

    response = client.post(
        "/v1/rag/query",
        headers={"X-Sandbox-ID": "bad sandbox"},
        json={"query": "gateway"},
    )

    assert response.status_code == 400
    assert "sandbox id" in response.json()["detail"]


def test_rag_query_requires_api_key_when_auth_is_enabled(tmp_path):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")
    settings = Settings(
        document_dir=tmp_path,
        api_key_auth_enabled=True,
        api_key_sha256s=(hashlib.sha256(b"secret-key").hexdigest(),),
    )
    client = TestClient(create_app(settings))

    missing = client.post(
        "/v1/rag/query",
        headers={"X-Request-ID": "rag-auth-missing", "X-Sandbox-ID": "agent-lab"},
        json={"query": "gateway"},
    )
    wrong = client.post(
        "/v1/rag/query",
        headers={"X-API-Key": "wrong"},
        json={"query": "gateway"},
    )
    valid = client.post(
        "/v1/rag/query",
        headers={"X-API-Key": "secret-key"},
        json={"query": "gateway"},
    )

    assert missing.status_code == 401
    assert missing.headers["X-Request-ID"] == "rag-auth-missing"
    assert missing.headers["X-Sandbox-ID"] == "agent-lab"
    assert missing.json()["detail"]["reason"] == "invalid_or_missing_api_key"
    assert wrong.status_code == 401
    assert valid.status_code == 200
    assert valid.json()["results"]


def test_rag_query_rejects_oversized_query(tmp_path):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")
    client = TestClient(create_app(Settings(document_dir=tmp_path, max_query_chars=5)))

    response = client.post("/v1/rag/query", json={"query": "too long"})

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "query_too_large"


def test_rag_query_rejects_blank_query_after_trimming(tmp_path):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")
    client = TestClient(create_app(Settings(document_dir=tmp_path)))

    response = client.post("/v1/rag/query", json={"query": "   \n\t  "})

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "empty_query"


def test_rag_query_rejects_explicit_zero_limits(tmp_path):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")
    client = TestClient(create_app(Settings(document_dir=tmp_path)))

    invalid_top_k = client.post("/v1/rag/query", json={"query": "gateway", "top_k": 0})
    invalid_context = client.post(
        "/v1/rag/query",
        json={"query": "gateway", "max_context_chars": 0},
    )

    assert invalid_top_k.status_code == 400
    assert invalid_top_k.json()["detail"]["reason"] == "invalid_top_k"
    assert invalid_context.status_code == 400
    assert invalid_context.json()["detail"]["reason"] == "invalid_max_context_chars"


def test_rag_audit_log_redacts_query_content(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="ai_platform_ops_lab.rag.audit")
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")
    client = TestClient(create_app(Settings(document_dir=tmp_path)))

    response = client.post(
        "/v1/rag/query",
        headers={"X-Request-ID": "rag-audit-1"},
        json={"query": "secret customer repo path"},
    )

    assert response.status_code == 200
    assert "rag-audit-1" in caplog.text
    assert "query_sha256" in caplog.text
    assert "secret customer repo path" not in caplog.text


def _qdrant_settings(tmp_path):
    return Settings(
        document_dir=tmp_path,
        retrieval_backend="qdrant",
        vector_store_url="http://qdrant.local:6333",
        vector_collection="lab",
        vector_dimensions=16,
    )


def test_readyz_lexical_backend_is_always_ready(tmp_path):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")
    client = TestClient(create_app(Settings(document_dir=tmp_path)))

    response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["retrieval_backend"] == "lexical"
    assert body["vector_store"]["status"] == "ok"


def test_readyz_qdrant_reports_ready_when_vector_store_is_reachable(tmp_path, monkeypatch):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")

    def handler(request: httpx.Request) -> httpx.Response:
        # /readyz probes the collection endpoint; bootstrap during startup uses the same client.
        return httpx.Response(200, json={"status": "ok", "result": {"status": "green"}})

    transport = httpx.MockTransport(handler)
    app = create_app(_qdrant_settings(tmp_path))
    monkeypatch.setattr(app.state.retriever, "_client", lambda: httpx.AsyncClient(transport=transport))

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["retrieval_backend"] == "qdrant"
    assert body["vector_store"]["status"] == "ok"


def test_readyz_qdrant_returns_503_when_vector_store_is_unreachable(tmp_path, monkeypatch):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("vector store down")

    transport = httpx.MockTransport(handler)
    app = create_app(_qdrant_settings(tmp_path))
    monkeypatch.setattr(app.state.retriever, "_client", lambda: httpx.AsyncClient(transport=transport))

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["vector_store"]["status"] == "unavailable"
    # The internal failure detail must not leak into the readiness body.
    assert "vector store down" not in response.text


def test_readyz_does_not_require_auth(tmp_path):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")
    settings = Settings(
        document_dir=tmp_path,
        api_key_auth_enabled=True,
        api_key_sha256s=(hashlib.sha256(b"secret-key").hexdigest(),),
    )
    client = TestClient(create_app(settings))

    response = client.get("/readyz")

    assert response.status_code == 200


def test_lifespan_eagerly_bootstraps_qdrant_collection(tmp_path, monkeypatch):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/collections/lab":
            return httpx.Response(404)
        if request.method == "PUT" and request.url.path == "/collections/lab":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "PUT" and request.url.path == "/collections/lab/points":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)
    app = create_app(_qdrant_settings(tmp_path))
    monkeypatch.setattr(app.state.retriever, "_client", lambda: httpx.AsyncClient(transport=transport))

    # Entering the TestClient context manager runs the lifespan, which eagerly bootstraps.
    with TestClient(app):
        pass

    assert app.state.retriever._bootstrapped is True
    assert ("PUT", "/collections/lab/points") in calls
    assert app.state.retriever.status()["last_sync_status"] == "synced"


def test_lifespan_bootstrap_failure_does_not_crash_startup(tmp_path, monkeypatch):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("vector store down")

    transport = httpx.MockTransport(handler)
    app = create_app(_qdrant_settings(tmp_path))
    monkeypatch.setattr(app.state.retriever, "_client", lambda: httpx.AsyncClient(transport=transport))

    # Startup must not raise even when the vector store is unreachable; readiness reports it.
    with TestClient(app) as client:
        ready = client.get("/readyz")

    assert app.state.retriever._bootstrapped is False
    assert ready.status_code == 503


def test_rag_query_returns_503_when_vector_store_bootstrap_fails(tmp_path, monkeypatch):
    write_doc(tmp_path, "agents.md", "# Coding Agents\nUse the gateway.")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("vector store down")

    transport = httpx.MockTransport(handler)
    app = create_app(_qdrant_settings(tmp_path))
    monkeypatch.setattr(app.state.retriever, "_client", lambda: httpx.AsyncClient(transport=transport))

    with TestClient(app) as client:
        response = client.post("/v1/rag/query", json={"query": "gateway"})

    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "vector_store_unavailable"


def test_async_http_embedding_provider_uses_async_client(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: real_async_client(transport=transport),
    )
    provider = OpenAICompatibleEmbeddingProvider(
        "http://embeddings.local",
        "private-embedding",
        dimensions=3,
        timeout_seconds=1,
    )

    vector = asyncio.run(provider.embed_async("hello"))

    assert vector == [0.1, 0.2, 0.3]
    assert captured["url"] == "http://embeddings.local/v1/embeddings"
    assert captured["body"]["model"] == "private-embedding"
