# AI Governance Crosswalk

This document maps the controls that already exist in the Private AI Platform Kit to three external
AI-governance frameworks: the [NIST AI Risk Management Framework 1.0](https://www.nist.gov/itl/ai-risk-management-framework),
the [EU AI Act](https://eur-lex.europa.eu/eli/reg/2024/1689/oj) (Regulation (EU) 2024/1689), and
[ISO/IEC 42001:2023](https://www.iso.org/standard/81230.html). It also defines the `riskTier`
semantics used across the model catalog and provenance records.

The machine-readable companion is
[`platform/governance/control-framework-map.yaml`](../platform/governance/control-framework-map.yaml).
That file carries the per-control framework citations and the risk-tier-to-control mapping; this
document is the human-readable explanation.

## What this is and is not

The kit ships mechanisms, not certifications. Each row below points at a control that exists in the
repo and the framework obligation it contributes evidence toward. A framework citation means "this
control helps you meet that obligation," not "this deployment is conformant." Conformity is a
property of your deployment, your use case, and your evidence — not of the kit alone. In particular,
whether your deployment is an EU AI Act high-risk system depends on your use case (Annex III), which
the kit cannot determine for you. See [docs/threat-model.md](threat-model.md) for the residual risks
that none of these controls remove and [docs/production-readiness.md](production-readiness.md) for the
control matrix and validation commands.

The kit also delegates several obligations to the operator. Where a framework expects something the
kit does not implement — fundamental-rights impact assessment, conformity assessment, registration,
post-market incident reporting to authorities — that work is yours. Those gaps are called out in the
[Operator responsibilities](#operator-responsibilities) section.

## Risk-tier semantics

`riskTier` is an existing field on every model in
[`platform/model-catalog/models.yaml`](../platform/model-catalog/models.yaml) and every artifact in
[`platform/governance/model-provenance.yaml`](../platform/governance/model-provenance.yaml). The enum
`low | medium | high` is validated by `VALID_RISK_TIERS` in
[`scripts/model-catalog.py`](../scripts/model-catalog.py) and re-checked in
[`scripts/model-provenance.py`](../scripts/model-provenance.py). Until now the field was validated but
not defined. This section gives it meaning: what each tier represents and which controls it mandates.

`riskTier` is the model artifact's inherent risk — its capability, autonomy, and blast radius if it
misbehaves. It is distinct from two neighbouring concepts:

- `dataClassification` (`public | internal | confidential | restricted`) is the sensitivity of the
  data the model is pointed at, not the model's own risk. A low-tier model can still handle
  confidential data; the two fields are set independently.
- An EU AI Act "high-risk system" is a property of the deployment use case, not the model artifact.
  A `riskTier: high` model is the one most likely to make a deployment high-risk, but the operator
  makes that determination.

Higher tiers inherit every control of the tiers below them.

### low

Small, bounded, non-autonomous models with low blast radius: laptop smoke models, single-purpose
embedding models, CPU demo models. A failure degrades a demo or a retrieval result, not a customer
decision or an autonomous action. The shipped catalog rates `qwen2.5:0.5b`, `qwen3.5:0.8b`, and
`BAAI/bge-small-en-v1.5` as low.

Mandated controls: governed provenance (`C-PROV`), promotion request with separation of duties
(`C-PROMO`), gateway approved-only allowlist (`C-ALLOW`), admission limits and sandbox budgets
(`C-ADMIT`), and redacted hash-chained audit (`C-AUDIT`).

### medium

Capable generation or coding-agent models that influence developer or analyst output and may drive
tool use inside a sandboxed workspace. A failure or injection can produce wrong code, leak context
within the sandbox, or waste capacity. This is the default tier for coding-agent serving models. The
shipped catalog rates `Qwen/Qwen3-Coder-Next` (approved) and the proposed `Qwen/Qwen3.6-35B-A3B`,
`zai-org/GLM-5.2`, and `deepseek-ai/DeepSeek-V4-Flash` as medium.

Mandated controls: everything in low, plus egress governance on the serving namespace (`C-EGRESS`),
prompt secret detection enabled (`C-SECRET`), and an eval suite that exercises the promoted model or
a declared, justified proxy (`C-EVAL`). The customer also replaces the source-reference digest with a
pinned model-store digest before production, and the coding-agent eval suite
([`platform/evals/coding-agent-suite.yaml`](../platform/evals/coding-agent-suite.yaml)) includes
`forbiddenAny` secret-leak checks.

### high

Models the operator designates as decision-supporting, customer-facing, or otherwise consequential —
where an error, bias, or injection can affect a person or a regulated decision. No model in the
shipped catalog is rated high; this tier is reserved for the operator to apply when a deployment's use
case warrants it. It is the tier most likely to make the deployment EU AI Act high-risk.

Mandated controls: everything in medium, plus explicit SLO and burn-rate alert coverage (`C-SLO`),
documented retention and data-classification review (`C-RETAIN`), and reviewed RBAC with a named
human-oversight owner (`C-RBAC`). High-tier promotion expects:

- Two named approvers on the promotion request. Separation of duties (requester is not an approver)
  is already enforced by [`scripts/model-catalog.py`](../scripts/model-catalog.py); high tier adds a
  documented human-oversight owner.
- `dataClassification` reviewed and recorded; `restricted` data requires explicit sign-off in the
  customer handoff.
- `make release-gate-strict` run against current evidence before promotion.
- The operator confirms whether the deployment is EU AI Act high-risk and applies the Article 9-15
  obligations accordingly.

## Control crosswalk

Each control below maps to the kit files that implement it, its validation command, and the framework
obligations it contributes evidence toward. Control IDs match
[`platform/governance/control-framework-map.yaml`](../platform/governance/control-framework-map.yaml).

| Control | Kit implementation | Validation | NIST AI RMF | EU AI Act | ISO/IEC 42001 |
| --- | --- | --- | --- | --- | --- |
| `C-PROV` Model artifact provenance | `platform/governance/model-provenance.yaml`, `scripts/model-provenance.py` | `make model-provenance-check` | Map, Manage | Art. 10, 11 | 8.3, data/resource mgmt |
| `C-PROMO` Promotion with separation of duties | `platform/model-catalog/models.yaml`, `promotion-requests/`, `scripts/model-catalog.py` | `make model-check` | Govern, Manage | Art. 9, 17 | 6.1, 8.1 |
| `C-ALLOW` Gateway approved-only allowlist | `deploy/clusters/*/values/inference-gateway.yaml`, `src/inference-gateway/app/policy.py` | `make model-check`, `test_chat_completion_rejects_disallowed_model` | Manage | Art. 9, 15 | 8.1 |
| `C-ADMIT` Admission limits and sandbox budgets | `src/inference-gateway/app/policy.py`, `budget.py`, `platform/governance/quota-plans.yaml` | `make quota-check`, gateway admission/budget tests | Measure, Manage | Art. 15 | 8.1, 9.1 |
| `C-AUDIT` Tamper-evident redacted audit chain | `src/inference-gateway/app/main.py`, `platform/governance/data-retention.yaml` | `test_audit_log_redacts_prompt_content` | Measure, Manage | Art. 12, 13 | 9.1, logging |
| `C-EGRESS` Egress governance | `platform/network/egress-catalog.yaml`, `deploy/policies/kyverno/policies.yaml` | `make egress-check`, `make policy-test` | Manage | Art. 15 | 8.1, security |
| `C-SECRET` Prompt secret detection | `src/inference-gateway/app/policy.py`, `runbooks/guardrails.md` | `make production-check` | Manage | Art. 10, 15 | data/security mgmt |
| `C-EVAL` Evaluation evidence on promotion | `platform/evals/*.yaml`, `scripts/eval-suite.py` | `make eval-local`, `scripts/eval-suite.py --check-config` | Measure | Art. 15, 17 | 8.3, 9.1 |
| `C-SLO` SLOs and error-budget alerting | `platform/slo/objectives.yaml`, `release-gates.yaml` | `make slo-check` | Measure, Manage | Art. 15, 72 | 9.1, 10.1 |
| `C-RETAIN` Data retention and classification | `platform/governance/data-retention.yaml`, `scripts/retention-check.py` | `make retention-check` | Govern, Manage | Art. 10, 12 | 7.5, data mgmt |
| `C-RBAC` RBAC, isolation, human oversight | `deploy/charts/agent-workspace/`, `deploy/policies/kyverno/policies.yaml` | `make agent-smoke`, `make policy-test` | Govern, Manage | Art. 14, 15 | 5.3, access mgmt |
| `C-SUPPLY` Supply-chain integrity | `.github/workflows/ci.yml`, `deploy/policies/kyverno/policies.yaml` | `make supply-chain-check`, `make image-scan` | Map, Manage | Art. 11, 15 | 8.1, supplier mgmt |

For the exact category, article, and clause text behind each citation, see the `controls` list in
[`platform/governance/control-framework-map.yaml`](../platform/governance/control-framework-map.yaml).

## NIST AI RMF function coverage

The four NIST AI RMF functions map to the kit as follows.

- **Govern.** Lifecycle policy and accountability: promotion with separation of duties (`C-PROMO`),
  retention and classification policy (`C-RETAIN`), and named-owner RBAC (`C-RBAC`).
- **Map.** Context and provenance of AI components: model provenance (`C-PROV`) and supply-chain
  integrity (`C-SUPPLY`).
- **Measure.** Evaluation and monitoring: eval evidence on promotion (`C-EVAL`), audit logging
  (`C-AUDIT`), admission/budget measurement (`C-ADMIT`), and SLOs (`C-SLO`).
- **Manage.** Containment and response: approved-only serving (`C-ALLOW`), egress governance
  (`C-EGRESS`), prompt secret detection (`C-SECRET`), budgets (`C-ADMIT`), and signed-image admission
  (`C-SUPPLY`).

## EU AI Act technical-obligation coverage

For deployments the operator determines are high-risk, the kit contributes mechanisms toward the
Chapter III, Section 2 technical obligations:

- **Article 9 (risk-management system).** Promotion review (`C-PROMO`) and approved-only serving
  (`C-ALLOW`) gate what reaches production; `riskTier` records inherent risk per model.
- **Article 10 (data and data governance).** Provenance (`C-PROV`), prompt secret detection
  (`C-SECRET`), and retention/classification (`C-RETAIN`).
- **Article 11 (technical documentation).** Provenance records and supply-chain artifacts (`C-PROV`,
  `C-SUPPLY`), plus the evidence pack (`make evidence`).
- **Article 12 (record-keeping / automatic logging).** The tamper-evident audit chain (`C-AUDIT`) and
  retention classes (`C-RETAIN`).
- **Article 13 (transparency).** Traceable request/response records with `X-Request-ID`,
  `X-Sandbox-ID`, and `traceparent` propagation (`C-AUDIT`).
- **Article 14 (human oversight).** Named-owner RBAC and scoped workspace access (`C-RBAC`); the
  promotion approval gate keeps a human in the deployment loop (`C-PROMO`).
- **Article 15 (accuracy, robustness, cybersecurity).** Eval evidence (`C-EVAL`), admission limits and
  budgets (`C-ADMIT`), SLOs (`C-SLO`), egress containment (`C-EGRESS`), and signed-image admission
  (`C-SUPPLY`).
- **Article 72 (post-market monitoring).** SLO burn-rate alerting and the audit/metrics surface
  (`C-SLO`, `C-AUDIT`) feed operational monitoring; reporting to authorities remains the operator's.

## ISO/IEC 42001 clause coverage

The kit supports an AI management system rather than being one. It maps onto the standard at the
operational-control and lifecycle clauses:

- **Clause 5.3 / 6.1 (roles, risk treatment).** Named owners and quota plans (`C-RBAC`), promotion
  risk review (`C-PROMO`).
- **Clause 7.5 (documented information).** Retention and evidence-path policy (`C-RETAIN`).
- **Clause 8 (operation).** Approved-only serving, admission, egress, secret detection, and
  supply-chain controls (`C-ALLOW`, `C-ADMIT`, `C-EGRESS`, `C-SECRET`, `C-SUPPLY`); 8.3 lifecycle
  validation is provenance and eval evidence (`C-PROV`, `C-EVAL`).
- **Clause 9.1 (monitoring, measurement, analysis).** Audit logging, SLOs, and eval evidence
  (`C-AUDIT`, `C-SLO`, `C-EVAL`).
- **Clause 10.1 (continual improvement).** Release gates and SLO burn-rate review (`C-SLO`).

## Operator responsibilities

The kit does not implement, and cannot substitute for, the following. These are the operator's
obligations under one or more of the frameworks:

- Determining whether a deployment is an EU AI Act high-risk system (Annex III) or a prohibited
  practice (Article 5).
- Fundamental-rights impact assessment, conformity assessment, CE marking, and EU database
  registration where the Act requires them.
- Post-market monitoring reporting and serious-incident reporting to authorities (Articles 72, 73).
- Wiring authentication to the enterprise identity boundary, backing API-key hashes or OIDC/JWT
  validation with the customer secret manager, and rotating keys (see
  [docs/threat-model.md](threat-model.md), Required Customer Hardening).
- Replacing source-reference model digests with pinned model-store digests before production.
- Setting `riskTier` and `dataClassification` to reflect the actual use case, and applying the
  high-tier requirements above when a deployment warrants them.
- Organizational AI policy, training, supplier agreements, and the management-review cadence that an
  ISO/IEC 42001 AIMS requires beyond the technical controls.

## Maintaining the crosswalk

When you add or change a control, update both this document and
[`platform/governance/control-framework-map.yaml`](../platform/governance/control-framework-map.yaml)
so the kit-implementation file references stay accurate. The framework citations are stable references
to published functions, articles, and clauses; revisit them when a framework is revised (for example,
a NIST AI RMF profile update or an EU AI Act implementing act).
