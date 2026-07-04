"""Tests for the S3/MinIO object-store backend and its SigV4 signing (ADR 0011)."""

from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace

import httpx
import pytest
from app.objectstore import ObjectNotFound
from app.objectstore_s3 import S3ObjectStore, _signing_key


def test_signing_key_matches_aws_get_vanilla_vector():
    # AWS SigV4 test suite "GET-vanilla": a fixed request with a documented signature. This
    # exercises the HMAC key-derivation chain (the crux of SigV4) against a known-good value.
    secret = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
    canonical_request = (
        "GET\n/\n\nhost:example.amazonaws.com\nx-amz-date:20150830T123600Z\n\n"
        "host;x-amz-date\n" + hashlib.sha256(b"").hexdigest()
    )
    string_to_sign = (
        "AWS4-HMAC-SHA256\n20150830T123600Z\n20150830/us-east-1/service/aws4_request\n"
        + hashlib.sha256(canonical_request.encode()).hexdigest()
    )
    key = _signing_key(secret, "20150830", "us-east-1", "service")
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    assert signature == "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"


def _store(handler):
    settings = SimpleNamespace(
        batch_s3_endpoint_url="http://minio:9000",
        batch_s3_bucket="batches",
        batch_s3_region="us-east-1",
        batch_s3_access_key_id="AKID",
        batch_s3_secret_access_key="SECRET",
    )
    return S3ObjectStore(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_requires_bucket():
    with pytest.raises(ValueError):
        S3ObjectStore(
            SimpleNamespace(
                batch_s3_endpoint_url="",
                batch_s3_bucket="",
                batch_s3_region="us-east-1",
                batch_s3_access_key_id="",
                batch_s3_secret_access_key="",
            )
        )


def test_put_signs_and_targets_path_style_url():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization", "")
        seen["sha"] = request.headers.get("x-amz-content-sha256", "")
        return httpx.Response(200)

    _store(handler).put("tenant-a/file-1", b"hello")
    assert seen["method"] == "PUT"
    assert seen["path"] == "/batches/tenant-a/file-1"
    assert seen["auth"].startswith("AWS4-HMAC-SHA256 Credential=AKID/")
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date" in seen["auth"]
    assert seen["sha"] == hashlib.sha256(b"hello").hexdigest()


def test_get_returns_body_and_maps_404():
    body = _store(lambda r: httpx.Response(200, content=b"data")).get("k")
    assert body == b"data"
    with pytest.raises(ObjectNotFound):
        _store(lambda r: httpx.Response(404)).get("missing")


def test_get_other_error_raises_oserror():
    with pytest.raises(OSError):
        _store(lambda r: httpx.Response(500)).get("k")


def test_delete_is_idempotent_on_204_and_404():
    _store(lambda r: httpx.Response(204)).delete("k")  # no raise
    _store(lambda r: httpx.Response(404)).delete("k")  # no raise
    with pytest.raises(OSError):
        _store(lambda r: httpx.Response(500)).delete("k")


def test_exists_reads_head_status():
    assert _store(lambda r: httpx.Response(200)).exists("k") is True
    assert _store(lambda r: httpx.Response(404)).exists("k") is False


def test_list_keys_parses_xml_and_sorts():
    xml = (
        b'<?xml version="1.0"?>'
        b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        b"<Contents><Key>tA/b</Key></Contents>"
        b"<Contents><Key>tA/a</Key></Contents>"
        b"</ListBucketResult>"
    )

    def handler(request):
        assert "list-type=2" in str(request.url)
        return httpx.Response(200, content=xml)

    assert _store(handler).list_keys("tA/") == ["tA/a", "tA/b"]


def test_put_failure_raises():
    with pytest.raises(OSError):
        _store(lambda r: httpx.Response(403)).put("k", b"x")
