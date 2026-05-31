import hashlib
import json
import logging

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.retriever import QdrantRetriever
from app.settings import Settings


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


def test_settings_load_qdrant_environment_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DOCUMENT_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_RETRIEVAL_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant-vector-store.vector.svc.cluster.local:6333")
    monkeypatch.setenv("QDRANT_COLLECTION", "customer-platform-knowledge")
    monkeypatch.setenv("QDRANT_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("QDRANT_VECTOR_DIMENSIONS", "512")
    monkeypatch.setenv("QDRANT_BOOTSTRAP_FROM_KNOWLEDGE", "false")

    settings = Settings.from_env()

    assert settings.retrieval_backend == "qdrant"
    assert settings.vector_store_url.endswith(":6333")
    assert settings.vector_collection == "customer-platform-knowledge"
    assert settings.vector_timeout_seconds == 2.5
    assert settings.vector_dimensions == 512
    assert settings.vector_bootstrap_enabled is False


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
            assert len(body["points"][0]["vector"]) == 16
            return httpx.Response(200, json={"status": "ok", "result": {"status": "acknowledged"}})
        if request.method == "POST" and request.url.path == "/collections/lab/points/query":
            body = json.loads(request.content)
            assert body["limit"] == 1
            assert body["with_payload"] is True
            assert len(body["query"]) == 16
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
        timeout_seconds=1.0,
        vector_dimensions=16,
        bootstrap_from_knowledge=True,
    )
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(retriever, "_client", lambda: httpx.Client(transport=transport))

    results = retriever.query("gateway", top_k=1, max_context_chars=200)

    assert results[0].document.id == "agents"
    assert results[0].score == 0.87
    assert ("PUT", "/collections/lab/points") in calls
    assert ("POST", "/collections/lab/points/query") in calls


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
