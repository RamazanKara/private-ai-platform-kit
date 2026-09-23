"""Regression tests for documentation discovery and repository link checks.

Use temporary Git repositories so ignored output and newly written documentation
exercise the same file-selection rules as a contributor's checkout.
"""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("repo_hygiene", Path(__file__).resolve().parents[1] / "repo-hygiene.py")
assert SPEC is not None and SPEC.loader is not None
hygiene = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hygiene)


class RepositoryFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="repo-hygiene-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.git("init", "--quiet")
        self.root_patch = patch.object(hygiene, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def write(self, name: str, content: str = "") -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def discover(self) -> list[Path]:
        errors: list[str] = []
        files = hygiene.markdown_files(errors)
        self.assertEqual(errors, [])
        return files


class MarkdownDiscoveryTests(RepositoryFixture):
    def test_includes_tracked_and_new_docs_in_services_and_hidden_directories(self) -> None:
        tracked = self.write("README.md")
        self.git("add", "README.md")
        new_paths = [
            self.write("src/inference-gateway/README.md"),
            self.write("src/rag-service/README.md"),
            self.write("sdk/README.md"),
            self.write(".github/SUPPORT.md"),
            self.write("docs/developer notes.md"),
            self.write("docs/überblick.md"),
        ]
        self.assertEqual(self.discover(), sorted([tracked, *new_paths]))

    def test_excludes_ignored_output_and_dependencies(self) -> None:
        self.write(".gitignore", ".venv*/\nsite/\n.out/\ndocs/runbooks/\n")
        source = self.write("runbooks/README.md")
        for name in (
            ".venv-docs/package/README.md",
            ".venv-quality/package/README.md",
            "src/inference-gateway/.venv/package/README.md",
            "site/README.md",
            ".out/generated.md",
            "docs/runbooks/README.md",
        ):
            self.write(name, "[generated link](not-a-source-file.md)")
        self.assertEqual(self.discover(), [source])

    def test_keeps_tracked_samples_that_match_ignore_rules(self) -> None:
        self.write(".gitignore", "results/\n")
        sample = self.write("results/sample-summary.md")
        self.git("add", "-f", "results/sample-summary.md")
        self.write("results/current-summary.md")
        self.assertEqual(self.discover(), [sample])

    def test_deleted_tracked_docs_do_not_crash_discovery(self) -> None:
        deleted = self.write("docs/removed.md")
        self.git("add", "docs/removed.md")
        deleted.unlink()
        self.assertEqual(self.discover(), [])

    def test_git_failure_is_reported_instead_of_silently_passing(self) -> None:
        for failure in (FileNotFoundError("git"), subprocess.CalledProcessError(128, ["git", "ls-files"])):
            with self.subTest(failure=failure), patch.object(hygiene.subprocess, "run", side_effect=failure):
                errors: list[str] = []
                self.assertEqual(hygiene.markdown_files(errors), [])
                self.assertEqual(len(errors), 1)
                self.assertIn("failed to discover source documentation", errors[0])


class MarkdownLinkTests(RepositoryFixture):
    def test_broken_service_links_and_images_are_checked(self) -> None:
        self.write(
            "src/inference-gateway/README.md",
            "[Guide](missing.md)\n![Architecture](missing.svg)\n![](empty-alt.png)\n",
        )
        errors: list[str] = []
        hygiene.check_markdown_links(errors, self.discover())
        self.assertEqual(len(errors), 3)
        for target in ("missing.md", "missing.svg", "empty-alt.png"):
            self.assertTrue(any(f"has broken link: {target}" in error for error in errors), errors)

    def test_accepts_encoded_paths_angle_brackets_queries_and_titles(self) -> None:
        self.write("docs/developer notes.md")
        self.write("docs/architecture.svg")
        self.write(
            "README.md",
            "[Guide](docs/developer%20notes.md#setup)\n"
            '[Guide](<docs/developer notes.md> "Developer guide")\n'
            '[Guide](docs/developer%20notes.md?plain=1#setup "Guide")\n'
            '![Architecture](docs/architecture.svg "Architecture")\n',
        )
        errors: list[str] = []
        hygiene.check_markdown_links(errors, self.discover())
        self.assertEqual(errors, [])

    def test_external_links_and_page_anchors_are_not_files(self) -> None:
        for target in (
            "https://example.com/docs",
            "http://example.com",
            "//example.com/docs",
            "mailto:maintainer@example.com",
            "#setup",
            "?plain=1#setup",
            "",
        ):
            with self.subTest(target=target):
                self.assertEqual(hygiene.link_target(target), "")

    def test_links_must_stay_inside_the_repository(self) -> None:
        self.write("README.md", "[Outside](../outside.md)\n")
        errors: list[str] = []
        hygiene.check_markdown_links(errors, self.discover())
        self.assertEqual(errors, ["README.md links outside repo: ../outside.md"])

    def test_relative_links_resolve_from_the_document_directory(self) -> None:
        self.write("README.md")
        self.write("src/rag-service/README.md", "[Repository](../../README.md)\n")
        errors: list[str] = []
        hygiene.check_markdown_links(errors, self.discover())
        self.assertEqual(errors, [])


class MakeReferenceTests(RepositoryFixture):
    def test_service_docs_must_reference_existing_targets(self) -> None:
        self.write("Makefile", "test:\n\ttrue\n")
        self.write("src/rag-service/README.md", "`make test`\n\n```bash\nmake missing-target\n```\n")
        errors: list[str] = []
        hygiene.check_make_target_references(errors, self.discover())
        self.assertEqual(
            errors,
            ["src/rag-service/README.md:4 references unknown make target: make missing-target"],
        )

    def test_historical_commands_in_adrs_and_changelog_are_exempt(self) -> None:
        self.write("Makefile", "test:\n\ttrue\n")
        self.write("CHANGELOG.md", "`make retired-command`\n")
        self.write("docs/adr/0001-example.md", "`make retired-command`\n")
        errors: list[str] = []
        hygiene.check_make_target_references(errors, self.discover())
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
