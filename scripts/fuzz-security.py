#!/usr/bin/env python3
"""Bounded mutation fuzzing for security-critical caller-controlled parsers."""

from __future__ import annotations

import argparse
import random
import string
from typing import Any

from app.admission import iter_payload_strings, payload_string_chars, validate_sandbox_id
from app.env_config import parse_completion_window

ALPHABET = string.ascii_letters + string.digits + string.punctuation + " \t\n\x00é中"


def random_string(rng: random.Random, maximum: int = 256) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(rng.randrange(maximum + 1)))


def json_value(rng: random.Random, depth: int = 0) -> Any:
    if depth >= 5:
        return rng.choice([None, True, False, rng.randrange(-1000, 1001), random_string(rng, 80)])
    kind = rng.randrange(7)
    if kind == 0:
        return random_string(rng, 200)
    if kind == 1:
        return [json_value(rng, depth + 1) for _ in range(rng.randrange(6))]
    if kind == 2:
        return {random_string(rng, 30): json_value(rng, depth + 1) for _ in range(rng.randrange(6))}
    return rng.choice([None, True, False, rng.randrange(-1_000_000, 1_000_001)])


def reference_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in reference_strings(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in reference_strings(item)]
    return []


def fuzz(iterations: int, seed: int) -> None:
    rng = random.Random(seed)
    suffix_scale = {"h": 3600, "m": 60, "s": 1}
    for index in range(iterations):
        if index % 97 == 0:
            window = "9" * 100_000 + "h"
        elif index % 11 == 0:
            amount = rng.randrange(1, 10**10)
            suffix = rng.choice(tuple(suffix_scale))
            window = f"{amount}{suffix}"
        else:
            window = random_string(rng)
        try:
            parsed = parse_completion_window(window)
        except ValueError:
            parsed = None
        stripped = window.strip()
        syntactically_valid = (
            2 <= len(stripped) <= 13
            and stripped[-1:] in suffix_scale
            and stripped[:-1].isascii()
            and stripped[:-1].isdigit()
            and int(stripped[:-1]) > 0
        )
        if syntactically_valid:
            expected = int(stripped[:-1]) * suffix_scale[stripped[-1]]
            if parsed != expected:
                raise RuntimeError(f"completion-window mismatch at iteration {index}")
        elif parsed is not None:
            raise RuntimeError(f"invalid completion window accepted at iteration {index}")

        payload = json_value(rng)
        expected_strings = reference_strings(payload)
        actual_strings = list(iter_payload_strings(payload))
        if actual_strings != expected_strings:
            raise RuntimeError(f"payload traversal mismatch at iteration {index}")
        if payload_string_chars(payload) != sum(map(len, expected_strings)):
            raise RuntimeError(f"payload character count mismatch at iteration {index}")

        sandbox = random_string(rng, 100)
        try:
            normalized = validate_sandbox_id(sandbox)
        except ValueError:
            continue
        if not (
            1 <= len(normalized) <= 63
            and all(char.isascii() and (char.islower() or char.isdigit() or char == "-") for char in normalized)
        ):
            raise RuntimeError(f"invalid sandbox id accepted at iteration {index}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=0x5041494B)
    args = parser.parse_args()
    if args.iterations <= 0:
        raise SystemExit("--iterations must be positive")
    fuzz(args.iterations, args.seed)
    print(f"security parser fuzzing ok: iterations={args.iterations} seed={args.seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
