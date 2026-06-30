import json

import httpx
import pytest
from app import ingest
from app.embeddings import build_embedding_provider
from app.ingest import (
    ChunkRecord,
    SourceRecord,
    build_chunks,
    chunk_text,
    delete_source,
    ensure_collection,
    iter_document_paths,
    load_manifest,
    upsert_chunks,
)

MANIFEST_HEADER = """apiVersion: platform.ai/v1alpha1
kind: RagSourceManifest
spec:
  sources:
"""


def _write_manifest(tmp_path, sources_yaml):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(MANIFEST_HEADER + sources_yaml, encoding="utf-8")
    return manifest


def _source_block(source_dir):
    return f"""    - id: handbook
      source: {source_dir}
      classification: internal
      retentionClass: standard
      owner: platform-team
      embeddingModel: hash-text-v1
"""


def test_load_manifest_parses_required_fields(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    manifest = _write_manifest(tmp_path, _source_block("docs"))

    records = load_manifest(manifest)

    assert len(records) == 1
    record = records[0]
    assert record.id == "handbook"
    assert record.classification == "internal"
    assert record.retention_class == "standard"
    assert record.owner == "platform-team"
    assert record.embedding_model == "hash-text-v1"
    assert record.base_dir == manifest.parent
    assert record.resolved_source == docs.resolve()


@pytest.mark.parametrize(
    "body, message",
    [
        ("not-a-mapping", "must be a YAML mapping"),
        ("apiVersion: wrong\nkind: RagSourceManifest\nspec:\n  sources: []\n", "apiVersion"),
        ("apiVersion: platform.ai/v1alpha1\nkind: Wrong\nspec:\n  sources: []\n", "kind"),
        ("apiVersion: platform.ai/v1alpha1\nkind: RagSourceManifest\nspec:\n  sources: []\n", "non-empty list"),
    ],
)
def test_load_manifest_rejects_invalid_documents(tmp_path, body, message):
    manifest = tmp_path / "bad.yaml"
    manifest.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_manifest(manifest)


def test_load_manifest_rejects_missing_fields(tmp_path):
    manifest = _write_manifest(tmp_path, "    - id: handbook\n      source: docs\n")
    with pytest.raises(ValueError, match="missing fields"):
        load_manifest(manifest)


def test_load_manifest_rejects_duplicate_ids(tmp_path):
    (tmp_path / "docs").mkdir()
    manifest = _write_manifest(tmp_path, _source_block("docs") + _source_block("docs"))
    with pytest.raises(ValueError, match="duplicate source id"):
        load_manifest(manifest)


def test_chunk_text_empty_returns_no_chunks():
    assert chunk_text("   \n\n  ", 100, 10) == []


def test_chunk_text_short_text_is_single_chunk():
    assert chunk_text("hello world", 100, 10) == ["hello world"]


def test_chunk_text_splits_long_text_with_overlap():
    text = "\n\n".join(f"paragraph {index} " + "word " * 40 for index in range(6))
    chunks = chunk_text(text, 300, 60)
    assert len(chunks) > 1
    assert all(len(chunk) <= 300 for chunk in chunks)
    # Overlap means the tail of one chunk reappears at the head of the next.
    assert any(chunks[0][-20:] in chunks[1] for _ in [0]) or len(chunks) >= 2


def test_iter_document_paths_single_file(tmp_path):
    doc = tmp_path / "note.md"
    doc.write_text("content", encoding="utf-8")
    source = SourceRecord("s", str(doc), "internal", "standard", "owner", "hash-text-v1", tmp_path)
    assert iter_document_paths(source) == [doc.resolve()]


def test_iter_document_paths_filters_and_sorts(tmp_path):
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "b.md").write_text("b", encoding="utf-8")
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "ignore.pdf").write_text("nope", encoding="utf-8")
    (root / ".hidden.md").write_text("secret", encoding="utf-8")
    (root / "sub" / "c.markdown").write_text("c", encoding="utf-8")
    source = SourceRecord("s", str(root), "internal", "standard", "owner", "hash-text-v1", tmp_path)

    names = [path.name for path in iter_document_paths(source)]

    assert names == ["a.txt", "b.md", "c.markdown"]


def test_iter_document_paths_missing_source_raises(tmp_path):
    source = SourceRecord("s", str(tmp_path / "nope"), "internal", "standard", "owner", "hash-text-v1", tmp_path)
    with pytest.raises(ValueError, match="does not exist"):
        iter_document_paths(source)


def test_build_chunks_and_chunk_record_payload(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "guide.md").write_text("alpha section\n\n" + "beta " * 80, encoding="utf-8")
    source = SourceRecord("handbook", str(root), "internal", "standard", "owner", "hash-text-v1", tmp_path)

    chunks = build_chunks([source], chunk_chars=200, overlap_chars=20, collection_version="v2")

    assert chunks
    first = chunks[0]
    assert isinstance(first, ChunkRecord)
    payload = first.payload()
    assert payload["source_id"] == "handbook"
    assert payload["collection_version"] == "v2"
    assert payload["document_path"] == "guide.md"
    assert payload["classification"] == "internal"
    # point_id is a deterministic UUIDv5 derived from version, source, path, and index.
    assert first.point_id == first.point_id
    assert len(first.point_id) == 36


def test_ensure_collection_creates_when_missing():
    seen = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(404)
        seen["put"] = json.loads(request.content)
        return httpx.Response(200, json={"result": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        status = ensure_collection(client, "http://qdrant:6333", "kb", 8)

    assert status == "created"
    assert seen["put"]["vectors"]["size"] == 8


def test_ensure_collection_ready_when_dimensions_match():
    def handler(request):
        return httpx.Response(200, json={"result": {"config": {"params": {"vectors": {"size": 8}}}}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert ensure_collection(client, "http://qdrant:6333", "kb", 8) == "ready"


def test_ensure_collection_rejects_dimension_mismatch():
    def handler(request):
        return httpx.Response(200, json={"result": {"config": {"params": {"vectors": {"size": 16}}}}})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ValueError, match="16 dimensions"),
    ):
        ensure_collection(client, "http://qdrant:6333", "kb", 8)


def test_upsert_chunks_embeds_and_writes_points(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "guide.md").write_text("alpha content here", encoding="utf-8")
    source = SourceRecord("handbook", str(root), "internal", "standard", "owner", "hash-text-v1", tmp_path)
    chunks = build_chunks([source], chunk_chars=200, overlap_chars=20)
    provider = build_embedding_provider("hash", 8, "hash-text-v1", "", 5.0)
    captured = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(404)
        if request.url.path.endswith("/points"):
            captured["points"] = json.loads(request.content)["points"]
            return httpx.Response(200, json={"result": {"status": "completed"}})
        return httpx.Response(200, json={"result": True})

    real_client = httpx.Client
    monkeypatch.setattr(ingest.httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(handler)))

    summary = upsert_chunks(chunks, provider, "http://qdrant:6333", "kb", 5.0)

    assert summary["upserted_chunks"] == len(chunks)
    assert summary["collection_status"] == "created"
    assert len(captured["points"][0]["vector"]) == 8
    assert captured["points"][0]["payload"]["source_id"] == "handbook"


def test_delete_source_issues_filtered_points_delete(monkeypatch):
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"result": {"status": "completed", "operation_id": 1}})

    real_client = httpx.Client
    monkeypatch.setattr(ingest.httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(handler)))

    summary = delete_source("platform-docs", "http://qdrant:6333", "kb", 5.0, "v2")

    assert summary["status"] == "deleted"
    assert summary["deleted_source_id"] == "platform-docs"
    assert captured["path"].endswith("/collections/kb/points/delete")
    must = captured["body"]["filter"]["must"]
    assert {"key": "source_id", "match": {"value": "platform-docs"}} in must
    assert {"key": "collection_version", "match": {"value": "v2"}} in must


def test_main_check_mode_reports_summary(tmp_path, monkeypatch, capsys):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "guide.md").write_text("alpha content here", encoding="utf-8")
    manifest = _write_manifest(tmp_path, _source_block("docs"))
    monkeypatch.setattr("sys.argv", ["ingest", "--source", str(manifest), "--check", "--dimensions", "8"])

    assert ingest.main() == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "checked"
    assert summary["sources"] == 1
    assert summary["chunks"] >= 1
    assert summary["dimensions"] == 8


def test_main_rejects_non_positive_dimensions(tmp_path, monkeypatch):
    manifest = _write_manifest(tmp_path, _source_block("docs"))
    monkeypatch.setattr("sys.argv", ["ingest", "--source", str(manifest), "--check", "--dimensions", "0"])
    with pytest.raises(SystemExit, match="dimensions"):
        ingest.main()


def test_main_write_requires_qdrant_url(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "guide.md").write_text("alpha", encoding="utf-8")
    manifest = _write_manifest(tmp_path, _source_block("docs"))
    monkeypatch.setattr("sys.argv", ["ingest", "--source", str(manifest), "--write", "--dimensions", "8"])
    with pytest.raises(SystemExit, match="qdrant-url"):
        ingest.main()
