# Sample SLO And Error Budget Report

Generated: `2026-05-31T00:00:00Z`

Config: `slo/objectives.yaml`

Summary: 5 passed, 0 failed, 0 config errors.

| Objective | Service | Status | Summary |
| --- | --- | --- | --- |
| inference-availability | inference-gateway | pass | 60 requests, error rate 0.0000 |
| inference-latency | inference-gateway | pass | p95 1280.00ms, p99 2400.00ms |
| eval-quality-smoke | inference-gateway | pass | 2/2 eval cases passed |
| restore-verification | restore-drill | pass | 1/1 restore drills passed |
| agent-platform-readiness | coding-agent-platform | pass | evidence pack has 23 passed and 0 failed controls |

Use `make slo-check` to check current evidence and `make slo-report` to write a fresh JSON/Markdown report.
