"""Minimal S3/MinIO object-store backend for the batch subsystem (ADR 0011).

A small in-tree S3 client speaking AWS Signature Version 4 over the already-present ``httpx``
and stdlib ``hashlib``/``hmac`` — no heavyweight cloud SDK. It implements the ``ObjectStore``
protocol (put/get/delete/exists/list_keys) with path-style addressing so it works against
MinIO and S3 alike. The synchronous client is deliberate: callers make only a handful of
object calls per batch (read the input file, write the output/error files), so blocking is
negligible next to the per-item HTTP replay.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from app.objectstore import ObjectNotFound

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


class S3ObjectStore:
    """S3/MinIO-backed object store (path-style, SigV4-signed)."""

    def __init__(self, settings: Any, client: httpx.Client | None = None) -> None:
        if not settings.batch_s3_bucket:
            raise ValueError("batch_s3_bucket must be set for the s3 object-store backend")
        self._endpoint = (settings.batch_s3_endpoint_url or "https://s3.amazonaws.com").rstrip("/")
        self._bucket = settings.batch_s3_bucket
        self._region = settings.batch_s3_region or "us-east-1"
        self._access_key = settings.batch_s3_access_key_id
        self._secret_key = settings.batch_s3_secret_access_key
        self._client = client or httpx.Client(timeout=30.0)

    def _url(self, key: str = "") -> str:
        # Path-style: <endpoint>/<bucket>/<key>. Each key segment is percent-encoded but the
        # slashes separating them are preserved so nested keys map to nested objects.
        encoded = "/".join(quote(segment, safe="") for segment in key.split("/")) if key else ""
        return f"{self._endpoint}/{self._bucket}/{encoded}" if encoded else f"{self._endpoint}/{self._bucket}"

    def put(self, key: str, data: bytes) -> None:
        response = self._request("PUT", self._url(key), body=data)
        if response.status_code >= 300:
            raise OSError(f"s3 put failed ({response.status_code}) for {key}")

    def get(self, key: str) -> bytes:
        response = self._request("GET", self._url(key))
        if response.status_code == 404:
            raise ObjectNotFound(key)
        if response.status_code >= 300:
            raise OSError(f"s3 get failed ({response.status_code}) for {key}")
        return response.content

    def delete(self, key: str) -> None:
        response = self._request("DELETE", self._url(key))
        # 204 (deleted) and 404 (already gone) are both success for idempotent delete.
        if response.status_code not in (200, 204, 404):
            raise OSError(f"s3 delete failed ({response.status_code}) for {key}")

    def exists(self, key: str) -> bool:
        response = self._request("HEAD", self._url(key))
        if response.status_code == 404:
            return False
        if response.status_code >= 300:
            raise OSError(f"s3 head failed ({response.status_code}) for {key}")
        return True

    def list_keys(self, prefix: str = "") -> list[str]:
        url = f"{self._endpoint}/{self._bucket}?list-type=2&prefix={quote(prefix, safe='')}"
        response = self._request("GET", url)
        if response.status_code >= 300:
            raise OSError(f"s3 list failed ({response.status_code})")
        root = ElementTree.fromstring(response.content)
        keys = [node.text for node in root.iter(f"{_S3_NS}Key") if node.text]
        return sorted(keys)

    # --- SigV4 request signing ---
    def _request(self, method: str, url: str, body: bytes = b"") -> httpx.Response:
        headers = self._signed_headers(method, url, body)
        return self._client.request(method, url, content=body if body else None, headers=headers)

    def _signed_headers(self, method: str, url: str, body: bytes) -> dict[str, str]:
        request = httpx.Request(method, url)
        host = request.url.netloc.decode("ascii")
        now = datetime.now(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest() if body else _EMPTY_SHA256

        canonical_uri = request.url.raw_path.decode("ascii").split("?", 1)[0]
        canonical_query = request.url.query.decode("ascii")
        canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            [method, canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash]
        )

        scope = f"{date_stamp}/{self._region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = _signing_key(self._secret_key, date_stamp, self._region, "s3")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            f"AWS4-HMAC-SHA256 Credential={self._access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return {
            "Authorization": authorization,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
        }


def _signing_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _hmac(f"AWS4{secret}".encode(), date_stamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, "aws4_request")
