# Model Cards

Every model the Private AI Platform Kit catalog marks `status: approved` must have a model card
in this directory. A model card is the human-readable companion to the machine-checked catalog
entry and provenance record: it restates the governed facts in one place so an operator,
reviewer, or customer can understand what a model is, how it is served, where it came from, and
what its limits are without reading three YAML files.

The cards do not introduce new facts. Every field is copied from, and must stay consistent with,
the governed sources of truth:

- catalog entry — `platform/model-catalog/models.yaml`
- artifact provenance — `platform/governance/model-provenance.yaml`
- promotion request — `platform/model-catalog/promotion-requests/<name>.yaml`
- gateway allowlist — `deploy/clusters/{local,customer}/values/inference-gateway.yaml`

If a card disagrees with those files, the YAML wins and the card is wrong. The promotion and
provenance workflows that keep these sources in sync are in
[`runbooks/model-governance.md`](../../../runbooks/model-governance.md) and
[`runbooks/model-provenance.md`](../../../runbooks/model-provenance.md).

## Approved models

| Model | Card | Runtime | Stage | Risk tier |
| --- | --- | --- | --- | --- |
| `qwen2.5:0.5b` | [qwen2.5-0.5b.md](qwen2.5-0.5b.md) | ollama | local-lab | low |
| `qwen3.5:0.8b` | [qwen3.5-0.8b.md](qwen3.5-0.8b.md) | ollama | customer-lab | low |
| `Qwen/Qwen3-Coder-Next` | [qwen3-coder.md](qwen3-coder.md) | vllm | customer-lab | medium |
| `BAAI/bge-small-en-v1.5` | [bge-small-en.md](bge-small-en.md) | vllm (embedding) | customer-lab | low |

Models with `status: proposed` in the catalog do not yet have a card. A card is added as part of
promoting a model to `approved`, alongside its provenance digest and promotion request.

## When a card is required

A card is mandatory for any catalog entry with `status: approved`. The reverse also holds: an
approved model without a card is a governance gap. The presence check wired into
`scripts/model-catalog.py` fails validation if an approved model is missing its card, so
`make model-check` will not pass until the card exists.

## Card template

Use this template when adding a card for a newly approved model. Fill every field from the
governed YAML; do not leave placeholders or invent values.

```markdown
# Model Card: <model id>

## Identity

- Model id: <id from models.yaml>
- Runtime: <ollama | vllm>
- Modality: <text-generation | embedding>
- Owner: <owner>
- Lifecycle status: <status>
- Stage: <stage>

## Intended use

<What this model is approved for in the kit, and the environment(s) it is allowlisted in.>

## Out-of-scope / not approved for

<Uses the promotion request did not cover; defer to governance before any of these.>

## Runtime and serving profile

- Serving runtime: <ollama | vllm + serving mode>
- Accelerators: <cpu | nvidia | amd>
- Context window: <contextWindow> tokens
- Gateway admission: maxPromptChars <n>, maxCompletionTokens <n>
- Serving profile values: <paths from model-provenance.yaml servingProfiles>
- Gateway allowlist: <local | customer>

## Provenance

- Source: <source> (<sourceUri>)
- Immutable reference: <immutableRef>
- Digest: <algorithm>:<value> (scope: <scope>)
- Verification: <verificationMode> — `<verificationCommand>`
- License: <license>

## Data classification and risk

- Data classification: <dataClassification>
- Risk tier: <riskTier>
- Prompt logging: <conditions.promptLogging>
- External network required: <conditions.externalNetworkRequired>
- Requires GPU: <conditions.requiresGpu>

## Known limitations

<Factual limits: size, CPU/GPU behaviour, context, multi-GPU requirements, etc.>

## Evaluation evidence

- Eval suite: <evidence.evalSuite>
- Eval summary: <evidence.evalSummary>
- Load-test summary: <evidence.loadTestSummary>
- Security workflow: <evidence.securityWorkflow>

## Approval reference

- Promotion request: <promotionRequest path>
- Requested by: <requestedBy>
- Approvers: <approvers>
- Business justification: <businessJustification>
```
