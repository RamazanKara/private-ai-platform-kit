# API Contracts

This directory stores the versioned OpenAPI snapshots for customer-facing services.

Regenerate snapshots after intentional API changes:

```bash
make api-contract-update
```

Validate that generated schemas still match the committed contract:

```bash
make api-contract
```

The validator also checks route coverage, stable operation IDs, auth declarations, and required request schemas so accidental public API drift fails local validation and CI.
