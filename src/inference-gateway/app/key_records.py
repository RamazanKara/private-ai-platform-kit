"""Optional richer API-key records: scopes, expiry, sandbox binding, and budget overrides.

The gateway's baseline key model is a flat allowlist of SHA-256 digests
(``API_KEY_SHA256S``): a matching hash authenticates as an unbound, unexpiring,
unscoped principal. This module adds an *optional* second source of key material
loaded from a JSON or YAML file (``API_KEY_RECORDS_PATH``), mirroring how
``SANDBOX_POLICY_PATH`` / ``MODEL_ROUTING_POLICY_PATH`` load governance files, so
each issued key can additionally carry:

* ``sandbox`` - binds the key to one sandbox id, enforced exactly like the JWT
  tenant-claim binding (a mismatched ``X-Sandbox-ID`` is rejected; a missing one
  adopts the bound sandbox);
* ``scopes`` - recorded on the audit principal for downstream attribution;
* ``expires_at`` - epoch seconds or ISO-8601; a presented-but-expired key is a 401;
* ``budget`` - per-key overrides of the sandbox request / prompt-char / estimated-token
  budgets, applied to the request's effective settings via the same mechanism as
  the sandbox policy set.

A malformed records file fails closed at load time (raises), never silently
disabling auth: a key store the operator cannot parse must stop the gateway from
starting rather than fall back to accepting every request.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from app.settings import validate_sandbox_id

_SHA256_HEX = re.compile(r"[\da-fA-F]{64}")


class KeyRecordError(ValueError):
    """Raised when the API-key records file is malformed (fails closed at startup)."""


def _parse_expires_at(raw: Any, key_label: str) -> float | None:
    """Return the record's expiry as epoch seconds, or None when unset.

    Accepts an int/float (epoch seconds) or an ISO-8601 string (``Z`` or explicit
    offset accepted; a naive string is treated as UTC). Raises :class:`KeyRecordError`
    on an unparseable value so a typo in the key store fails closed rather than
    minting a key that never expires by accident.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):  # bool is an int subclass; reject explicitly
        raise KeyRecordError(f"key record '{key_label}' expires_at must be epoch seconds or ISO-8601")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        # datetime.fromisoformat handles offset-aware and naive strings; normalize a
        # trailing 'Z' (not accepted before Python 3.11) to +00:00 first.
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise KeyRecordError(f"key record '{key_label}' expires_at must be epoch seconds or ISO-8601") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    raise KeyRecordError(f"key record '{key_label}' expires_at must be epoch seconds or ISO-8601")


def _optional_non_negative_int(raw: Any, field: str, key_label: str) -> int | None:
    """Return a non-negative int budget override, or None when unset."""
    if raw is None:
        return None
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise KeyRecordError(f"key record '{key_label}' budget.{field} must be a non-negative integer")
    return raw


@dataclass(frozen=True)
class KeyRecord:
    """A single richer API-key record matched by its SHA-256 digest.

    ``key_id`` is a short stable label recorded on the audit principal (the record's
    ``name`` when set, else the 12-char digest prefix). Budget overrides are ``None``
    when unset, meaning "inherit the sandbox/effective budget".
    """

    sha256: str
    key_id: str
    sandbox: str | None = None
    scopes: tuple[str, ...] = ()
    expires_at: float | None = None
    request_budget: int | None = None
    prompt_char_budget: int | None = None
    estimated_token_budget: int | None = None

    def is_expired(self, now: float) -> bool:
        """Return whether the key's expiry (if any) is at or before ``now`` (epoch seconds)."""
        return self.expires_at is not None and now >= self.expires_at

    def has_budget_override(self) -> bool:
        """Return whether the record overrides any sandbox budget dimension."""
        return (
            self.request_budget is not None
            or self.prompt_char_budget is not None
            or self.estimated_token_budget is not None
        )


@dataclass(frozen=True)
class KeyRecordSet:
    """A collection of :class:`KeyRecord` keyed by SHA-256 digest.

    Lookup is constant-time over the digest set (the presented key's digest is
    compared against each record with :func:`hmac.compare_digest`), so matching a
    key never leaks which record - or whether any matched - through timing.
    """

    records: tuple[KeyRecord, ...]

    @classmethod
    def empty(cls) -> KeyRecordSet:
        """Return a record set with no records (baseline flat-hash behavior)."""
        return cls(())

    @classmethod
    def from_path(cls, path: Path | None) -> KeyRecordSet:
        """Load and validate an API-key records file (JSON or YAML), failing closed.

        The file must be a mapping with a ``records`` list, or a bare list of records.
        Each record needs a 64-hex ``sha256``; ``name``/``sandbox``/``scopes``/
        ``expires_at``/``budget`` are optional. A duplicate digest, a bad shape, or an
        unparseable expiry raises :class:`KeyRecordError` so an unreadable key store
        stops startup instead of silently disabling per-key controls.
        """
        if not path:
            return cls.empty()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise KeyRecordError(f"API-key records file could not be read: {path}") from exc
        # YAML is a superset of JSON, so safe_load parses both a .json and a .yaml file.
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise KeyRecordError(f"API-key records file {path} is not valid JSON/YAML") from exc
        if isinstance(data, dict):
            raw_records = data.get("records", [])
        elif isinstance(data, list):
            raw_records = data
        else:
            raise KeyRecordError(f"API-key records file {path} must be a mapping with 'records' or a list")
        if not isinstance(raw_records, list):
            raise KeyRecordError(f"API-key records file {path} 'records' must be a list")
        records: list[KeyRecord] = []
        seen: set[str] = set()
        for index, item in enumerate(raw_records):
            record = _parse_record(item, index)
            if record.sha256 in seen:
                raise KeyRecordError(f"API-key records file {path} has a duplicate sha256 digest")
            seen.add(record.sha256)
            records.append(record)
        return cls(tuple(records))

    def match(self, digest: str) -> KeyRecord | None:
        """Return the record whose digest matches ``digest`` in constant time, or None.

        ``digest`` is the lowercase hex SHA-256 of the presented key. Every record is
        compared (no early return on the first mismatch) so the lookup does not reveal
        via timing how many records precede a match or whether any matched at all.
        """
        found: KeyRecord | None = None
        for record in self.records:
            if hmac.compare_digest(record.sha256, digest):
                found = record
        return found


def _parse_record(item: Any, index: int) -> KeyRecord:
    """Validate one raw record mapping into a :class:`KeyRecord`, failing closed."""
    if not isinstance(item, dict):
        raise KeyRecordError(f"API-key record [{index}] must be a mapping")
    raw_sha = item.get("sha256")
    if not isinstance(raw_sha, str) or not _SHA256_HEX.fullmatch(raw_sha):
        raise KeyRecordError(f"API-key record [{index}] sha256 must be a 64-character hex digest")
    sha256 = raw_sha.lower()
    name = item.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise KeyRecordError(f"API-key record [{index}] name must be a non-empty string when set")
    key_id = name.strip() if isinstance(name, str) and name.strip() else sha256[:12]

    raw_sandbox = item.get("sandbox")
    sandbox: str | None = None
    if raw_sandbox is not None:
        if not isinstance(raw_sandbox, str):
            raise KeyRecordError(f"API-key record '{key_id}' sandbox must be a string when set")
        try:
            sandbox = validate_sandbox_id(raw_sandbox)
        except ValueError as exc:
            raise KeyRecordError(f"API-key record '{key_id}' sandbox is not a valid sandbox id") from exc

    raw_scopes = item.get("scopes", [])
    if not isinstance(raw_scopes, list):
        raise KeyRecordError(f"API-key record '{key_id}' scopes must be a list when set")
    scopes = tuple(str(scope).strip() for scope in raw_scopes if str(scope).strip())

    expires_at = _parse_expires_at(item.get("expires_at"), key_id)

    raw_budget = item.get("budget", {})
    if raw_budget is None:
        raw_budget = {}
    if not isinstance(raw_budget, dict):
        raise KeyRecordError(f"API-key record '{key_id}' budget must be a mapping when set")

    return KeyRecord(
        sha256=sha256,
        key_id=key_id,
        sandbox=sandbox,
        scopes=scopes,
        expires_at=expires_at,
        request_budget=_optional_non_negative_int(raw_budget.get("requestLimit"), "requestLimit", key_id),
        prompt_char_budget=_optional_non_negative_int(raw_budget.get("promptCharLimit"), "promptCharLimit", key_id),
        estimated_token_budget=_optional_non_negative_int(
            raw_budget.get("estimatedTokenLimit"), "estimatedTokenLimit", key_id
        ),
    )


def key_record_effective_budget_updates(record: KeyRecord) -> dict[str, Any]:
    """Return the Settings field overrides implied by a record's budget block.

    Maps the record's per-key budget overrides onto the same Settings fields the
    sandbox policy set uses, so the request path can apply them with
    ``dataclasses.replace`` and reuse the existing budget-reservation mechanism.
    """
    updates: dict[str, Any] = {}
    if record.request_budget is not None:
        updates["sandbox_request_budget"] = record.request_budget
    if record.prompt_char_budget is not None:
        updates["sandbox_prompt_char_budget"] = record.prompt_char_budget
    if record.estimated_token_budget is not None:
        updates["sandbox_estimated_token_budget"] = record.estimated_token_budget
    return updates
