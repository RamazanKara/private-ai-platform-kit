#!/usr/bin/env python3
"""MkDocs build hooks.

Runbook pages are mirrored from the repo-root runbooks/ tree into docs/runbooks/ at
build time (see scripts/docs-build.sh), so their default edit link would point at the
git-ignored docs/runbooks/ copy and 404 on GitHub. Rewrite it to the real source file
so the edit pencil on runbook pages works.
"""

from __future__ import annotations

from typing import Any


def on_page_context(context: dict[str, Any], page: Any, config: Any, nav: Any) -> dict[str, Any]:
    src = page.file.src_uri
    if src.startswith("runbooks/"):
        page.edit_url = f"{config['repo_url']}/edit/main/{src}"
    return context
