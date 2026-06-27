# Quickstart Proof Assets

This directory is reserved for release proof captures:

- `quickstart-success.txt` or `quickstart.cast`
- `argocd-apps.txt`
- `grafana-dashboard.txt`
- `agent-smoke.txt`
- `evidence-report.txt`

Generate them from a live local lab:

```bash
RUN_LIVE=1 scripts/capture-proof-assets.sh
```

Do not commit customer data, private hostnames, tokens, or screenshots from a real customer cluster. Public release assets should use the local lab or a scrubbed demo environment.
