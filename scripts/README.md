# Repository automation

Use `make help` from the repository root to find the normal entry points.
The Makefile provides defaults and puts managed tools from `.tools/bin` on
`PATH`. Scripts remain directly callable for their documented arguments.

## Find the right script

| Task | Entry points |
| --- | --- |
| Local cluster lifecycle | [bootstrap.sh](bootstrap.sh), [quickstart.sh](quickstart.sh), [local-up.sh](local-up.sh), [sync.sh](sync.sh), [local-down.sh](local-down.sh) |
| Service environments and tests | [bootstrap-python.sh](bootstrap-python.sh), [test-gateway.sh](test-gateway.sh), [test-rag.sh](test-rag.sh) |
| Repository validation | [validate.sh](validate.sh), [quality.sh](quality.sh), [repo-hygiene.py](repo-hygiene.py), [paths.py](paths.py) |
| Production configuration checks | [production-check.py](production-check.py), with checks grouped in [production_checks/](production_checks/) |
| Generated contracts and documentation | [api-contract.py](api-contract.py), [config-contract.py](config-contract.py), [chart-docs.py](chart-docs.py), [dashboard-check.py](dashboard-check.py), [docs-build.sh](docs-build.sh) |
| Tenant and customer configuration | [tenant-onboard.py](tenant-onboard.py), [tenant-offboard.py](tenant-offboard.py), [configure-customer-overlay.py](configure-customer-overlay.py) |
| Evaluation and load tests | [eval-local.sh](eval-local.sh), [eval-suite.py](eval-suite.py), [rag-eval.py](rag-eval.py), [loadtest-local.sh](loadtest-local.sh) |
| Evidence and releases | [evidence-pack.py](evidence-pack.py), [release-gate.py](release-gate.py), [supply-chain-evidence.py](supply-chain-evidence.py) |
| Audit verification | [audit-verify.py](audit-verify.py), [audit-anchor.py](audit-anchor.py) |
| Model inventories and provenance | [model_artifacts.py](model_artifacts.py), [model-provenance.py](model-provenance.py), [model-catalog.py](model-catalog.py) |
| Tool installation and diagnosis | [install-validation-tools.sh](install-validation-tools.sh), [toolchain-doctor.py](toolchain-doctor.py) |

The [runbook index](../runbooks/README.md) covers operation-specific commands.
The [developer workflow](../docs/development.md) explains environments and validation.
The [repository map](../docs/repository-map.md) identifies generated output and its sources.

## Writing automation

- Resolve paths from the repository root instead of relying on the caller's working
  directory. Shell scripts can use [common.sh](common.sh) for root discovery,
  tool lookup, logging, and prerequisite checks.
- Keep validation separate from report generation or deployment. Where a command
  offers `--check`, document whether it validates configuration, current evidence,
  or both. State cluster and network side effects in its runbook.
- Make failures actionable: identify the file, setting, or missing prerequisite.
  Exit nonzero when a required check cannot run.
- Keep generated output in the established ignored locations and retain hashed
  dependency installation. Do not install packages into the system interpreter.
- Expose a new user-facing command in the Makefile and its help text. Document
  prerequisites, expected results, and recovery in the relevant guide.
- Root-level `scripts/*.py` and `scripts/*.sh` files must be executable in Git.
  Imported helpers and test modules in subdirectories do not need executable mode.

## Tooling tests

```bash
make test-scripts
```

The suite in [tests/](tests/) uses Python's standard-library `unittest` and Git.
It runs as part of `make test` and `make validate`, before service environment setup
in the validation gate.

Use temporary directories and repositories for filesystem and Git behavior. Test
observable outcomes such as a broken-link diagnostic or excluded generated file.
Service-specific regression tests remain beside their service.

The hygiene checker discovers tracked and new, unignored Markdown through Git.
It includes service READMEs and skips ignored virtual environments, generated site
files, and the runbook mirror. Inline local links and image paths must resolve
inside the repository. Remote URLs, reference-style links, and heading anchors are
outside this check's scope.
