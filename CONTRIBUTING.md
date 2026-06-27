# Contributing

Private AI Platform Kit is maintained as an operational platform, not a collection of examples. Changes should keep local, customer, and release-review paths aligned.

## Working Locally

For a first local run, use the guided quickstart:

```bash
make quickstart
```

Start normal development with the default validation gate:

```bash
make help
make validate
```

Use focused targets while iterating:

```bash
make test-gateway
make test-rag
make production-check
make repo-security-scan
make dependency-lock-check
make repo-hygiene
make api-contract
make config-contract
```

Run image scanning before changing Dockerfiles, dependencies, or release workflows:

```bash
make dependency-lock-check
make image-scan
make repo-security-scan
```

## Change Standards

- Keep runtime dependencies separate from test-only dependencies.
- Keep `requirements.lock` and `requirements-dev.lock` regenerated with hashes whenever Python requirements change.
- Keep Docker base images pinned by digest.
- Keep `.github/dependabot.yml` aligned with runtime package managers, Dockerfiles, and GitHub Actions.
- Keep generated evidence under `results/` ignored unless it is an intentional `sample-*` artifact.
- Keep every tracked `scripts/*.py` and `scripts/*.sh` file executable in git; use `git update-index --chmod=+x <path>` when a mode-only fix is needed.
- Update `api-contracts/` with `make api-contract-update` when changing customer-facing service routes or request schemas.
- Update `config-contracts/` with `make config-contract-update` when changing service settings, Helm env vars, or chart defaults.
- Keep customer-facing commands documented in README, docs, or runbooks.
- Add or update a validation check when adding a new operational invariant.
- Do not commit secrets, raw prompts, private customer context, local kubeconfigs, or generated tenant output.

## Release Readiness

Use the strict path before demos, releases, restore reviews, or production-readiness handoff:

```bash
make validate-full
make image-scan
make evidence LIVE=1
make release-gate-strict
make release-report-strict
```

The non-strict `make release-gate` target can use checked-in sample evidence and is only for local configuration checks.

## Review Focus

Reviewers should check:

- API, chart, and GitOps behavior stay compatible across local and customer profiles.
- Security controls remain enforceable by tests, policy, or validation scripts.
- New runbooks name the owner action, validation command, and rollback or escalation path.
- Customer-facing values avoid cloud-specific assumptions unless they are behind a named profile.

## Community Workflow

- Use the issue templates for bugs, feature requests, and questions.
- Open larger design changes as issues before implementing them.
- Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), [GOVERNANCE.md](GOVERNANCE.md), and [ROADMAP.md](ROADMAP.md).
- Keep public issues free of secrets, raw prompts, customer data, and private context.
