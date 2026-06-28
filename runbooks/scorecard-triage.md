# OpenSSF Scorecard Triage And Remediation

The [Scorecard workflow](../.github/workflows/scorecard.yml) runs weekly and on pushes to `main`,
uploading SARIF to GitHub code scanning (`publish_results: false`, so results stay private to the
repo). Use this runbook to read the findings and decide what to fix.

## Where To Read Findings

- **Security tab → Code scanning**, filtered to the `scorecard` tool. Each alert maps to one
  Scorecard check with a 0-10 score and a remediation hint.
- Re-run on demand from **Actions → OpenSSF Scorecard → Run workflow**, or:
  ```bash
  gh workflow run scorecard.yml
  ```

## Triage Order

Score each finding by exploitability, not just the raw number. Work top-down:

1. **Critical / high-risk checks first** — `Dangerous-Workflow`, `Token-Permissions`,
   `Branch-Protection`, `Binary-Artifacts`. A failing `Dangerous-Workflow` or broad
   `Token-Permissions` is a real, fixable supply-chain risk and should be handled same-day.
2. **Build-integrity checks** — `Pinned-Dependencies`, `Signed-Releases`, `Vulnerabilities`,
   `Dependency-Update-Tool`. This repo already pins GitHub Actions by tag/digest, pins Python with
   hashed lockfiles, signs images with Cosign, and gates on Trivy, so these should stay green;
   investigate any regression as a pinning or lockfile drift.
3. **Process checks** — `Code-Review`, `Maintained`, `CI-Tests`, `Fuzzing`, `SAST`,
   `Security-Policy`. `SAST` is satisfied by the [CodeQL workflow](../.github/workflows/codeql.yml);
   `Security-Policy` by [SECURITY.md](../SECURITY.md).

## Remediation Patterns

| Failing check | Typical fix in this repo |
| --- | --- |
| `Token-Permissions` | Add least-privilege `permissions:` to the workflow or job; default to `contents: read`. |
| `Pinned-Dependencies` | Pin the action to a full-length commit SHA or release tag; regenerate hashed Python locks with `pip-compile`. |
| `Dangerous-Workflow` | Remove `pull_request_target` + untrusted checkout patterns; never interpolate untrusted input into `run:`. |
| `Branch-Protection` | Enable required reviews and required status checks on `main` in repo settings. |
| `Vulnerabilities` | Bump the offending dependency and regenerate locks; confirm `make image-scan` is clean. |
| `Signed-Releases` | Already handled by Cosign signing in [ci.yml](../.github/workflows/ci.yml); confirm the signing step ran. |

## Accepting A Finding

Some checks (for example `Fuzzing`, `Branch-Protection` on a solo-maintained repo) may be
intentionally out of scope. Record the decision and rationale in the pull request that touches the
related area, and dismiss the code-scanning alert with the matching reason so it does not re-surface
as actionable. Do not silence a finding without a written rationale.

## Verification

After remediation, re-run the workflow and confirm the alert clears in the Security tab. For
pinning or permissions changes, also run `make validate` and `make repo-hygiene` so contract and
hygiene gates stay green.
