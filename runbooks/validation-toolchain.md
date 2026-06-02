# Validation Toolchain Runbook

Use this runbook when preparing a local lab, customer handoff, release review, or full production-readiness validation.

## Profiles

The validation toolchain is declared in `tools/validation-toolchain.yaml`.

- `validate`: minimum tools required for `make validate`; strict tools are reported and skipped when absent.
- `local`: tools expected for operating the complete local `kind` lab and rebuilding local images.
- `strict`: tools required for `make validate-full` and customer handoff reviews.

## Check The Current Workstation

Run the default profile:

    make toolchain-doctor

Check the local lab profile:

    make toolchain-doctor TOOLCHAIN_PROFILE=local

Check the strict customer-handoff profile:

    make toolchain-doctor TOOLCHAIN_PROFILE=strict

The command exits non-zero only when a required tool for the selected profile is missing.

## Install Strict Tools

On Linux, WSL, or CI runners, install the pinned strict validation tools locally:

    make toolchain-install

The installer downloads pinned GitHub release assets, verifies published SHA-256 asset digests when available, and installs binaries under `.tools/bin` by default. Make targets and repo scripts automatically prefer that managed directory over globally installed tools. Override versions with the environment variables listed in `tools/validation-toolchain.yaml` only after reviewing the release.

## Generate Evidence

Run:

    make toolchain-report

The command writes JSON and Markdown reports under `results/toolchain/`. Attach the Markdown report to customer handoff notes when strict validation cannot be run directly on the customer's workstation.

## Full Validation

Run:

    make validate-full

This first checks the `strict` tool profile, then runs `make validate` with `REQUIRE_FULL_TOOLCHAIN=1`. Missing strict tools should be fixed before customer release or production-readiness sign-off.

## Customer Notes

Customers can keep their own Kubernetes distribution, registry, secret backend, and observability stack. The required validation tools are client-side controls used to prove the portable charts, policies, supply-chain workflow, restore drill, and load-test path before handoff.
