# Sample Release Gate Summary

Generated: `2026-05-31T00:00:00Z`

Summary: 10 passed, 0 failed.

| Gate | Status | Summary |
| --- | --- | --- |
| eval | pass | 2/2 eval cases passed |
| load | pass | 60 requests, p95 1280.00ms, error rate 0.0000 |
| restore | pass | 1/1 restore drills passed |
| toolchain | pass | strict toolchain has no missing required tools |
| egress | pass | 1 external egress references checked, 0 errors |
| retention | pass | 5 retention classes checked, 0 errors |
| slo | pass | 5/5 SLO objectives passed, 0 config errors |
| quota | pass | 3 quota plans and 4 chargeback labels checked |
| modelProvenance | pass | 2 model provenance artifacts checked |
| evidencePack | pass | evidence pack has 28 passed and 0 failed controls |

Use `make release-gate` to check current evidence and `make release-report` to write a fresh JSON/Markdown report.
