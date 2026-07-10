# GitHub repository settings

Repository-host controls are declared in `.github/repository-settings.json`. Audit them with an
authenticated GitHub CLI session:

```bash
python3 scripts/github-settings.py
```

The command is read-only by default and exits non-zero on drift. A repository owner can apply the
declared settings explicitly:

```bash
python3 scripts/github-settings.py --apply
```

The apply mode enables private vulnerability reporting, vulnerability alerts, automated security
updates, secret scanning and push protection; enables Discussions; normalizes merge behavior; and
protects `main` with required CI checks and review/conversation rules, publishes project topics and
the docs homepage, and creates a reviewer-gated `pypi` environment restricted to `v*` tags. GitHub plan or organization
policies can reject features unavailable to the repository; do not weaken the declaration to hide
that failure. Record the platform limitation and remediate it at the account level.

Run the audit after renaming CI jobs because required-check context names are exact. Never run
`--apply` against a fork or mirror without first reviewing the resolved repository printed by
`gh repo view`.
