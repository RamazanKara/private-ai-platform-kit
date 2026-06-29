# Configuration Contracts

This directory stores versioned runtime configuration snapshots for customer-facing services.

Regenerate snapshots after intentional changes to service settings, Helm environment variables, or chart defaults:

```bash
make config-contract-update
```

Validate that service code, Helm templates, chart defaults, and committed snapshots still agree:

```bash
make config-contract
```

The validator catches configuration drift before customer overlays or release evidence depend on stale runtime assumptions.
