"""Environment parsing helpers for gateway configuration."""

from __future__ import annotations

import os
import re
from pathlib import Path

from app.admission import BUILT_IN_SECRET_PATTERNS, DEFAULT_SECRET_PATTERNS, OUTPUT_DEFAULT_PATTERNS


def _float_from_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _int_from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be zero or greater")
    return value


def _positive_int_from_env(name: str, default: int) -> int:
    value = _int_from_env(name, default)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _positive_float_from_env(name: str, default: float) -> float:
    value = _float_from_env(name, default)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _bool_from_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def parse_completion_window(value: str) -> int:
    """Return the batch completion window in seconds, or raise ValueError on a bad format.

    Accepts a positive integer with an ``h`` (hours), ``m`` (minutes), or ``s`` (seconds)
    suffix, e.g. ``24h``. OpenAI only documents ``24h``; the wider grammar lets local test
    windows be short without a special case. The window is honored as an expiry bound.
    """
    normalized = value.strip()
    # Bound and parse directly: this value is caller controlled on the Batch API,
    # so it must not enter a repetition-bearing regular expression or an
    # unbounded integer conversion.
    if len(normalized) < 2 or len(normalized) > 13:
        raise ValueError("batch_completion_window must look like '24h', '30m', or '90s'")
    amount_text, suffix = normalized[:-1], normalized[-1]
    if suffix not in {"h", "m", "s"} or not amount_text.isascii() or not amount_text.isdigit():
        raise ValueError("batch_completion_window must look like '24h', '30m', or '90s'")
    amount = int(amount_text)
    if amount <= 0:
        raise ValueError("batch_completion_window must be greater than zero")
    return amount * {"h": 3600, "m": 60, "s": 1}[suffix]


def _csv_from_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _sha256s_from_env(name: str) -> tuple[str, ...]:
    hashes = _csv_from_env(name, ())
    for item in hashes:
        if not re.fullmatch(r"[\da-fA-F]{64}", item):
            raise ValueError(f"{name} must contain comma-separated SHA-256 hex digests")
    return tuple(item.lower() for item in hashes)


def _secret_pattern_names_from_env(name: str) -> tuple[str, ...]:
    names = _csv_from_env(name, DEFAULT_SECRET_PATTERNS)
    unknown = sorted(set(names) - set(BUILT_IN_SECRET_PATTERNS))
    if unknown:
        raise ValueError(f"{name} contains unknown built-in secret patterns: {unknown}")
    return names


def _output_pattern_names_from_env(name: str) -> tuple[str, ...]:
    names = _csv_from_env(name, OUTPUT_DEFAULT_PATTERNS)
    unknown = sorted(set(names) - set(BUILT_IN_SECRET_PATTERNS))
    if unknown:
        raise ValueError(f"{name} contains unknown built-in secret patterns: {unknown}")
    return names


def _path_from_env(name: str) -> Path | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return Path(raw.strip())
