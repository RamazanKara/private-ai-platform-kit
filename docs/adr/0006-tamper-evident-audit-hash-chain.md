# 0006. Tamper-evident audit hash chain

- Status: Accepted
- Date: 2026-07-01
- Deciders: Platform maintainer

## Context

The gateway emits one redacted audit event per request. For customer handoff and regulated tenants,
an auditor must be able to detect whether that event stream has been edited, reordered, truncated, or
had records inserted after the fact — without trusting the process that wrote it. Plain append-only
logging does not give that property: a log writer (or anyone with log access) can rewrite history
silently. The control must be cheap (no extra infrastructure), verifiable by independent tooling, and
must not weaken the existing redaction guarantees.

## Decision

Link each audit event into a per-process tamper-evident SHA-256 hash chain.

- Construction, in
  [`src/inference-gateway/app/main.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/inference-gateway/app/main.py): `h_0 =
  SHA-256("genesis")`; for each record `h_i = SHA-256(h_{i-1} || canonical(record_i))`, where
  `canonical` is `json.dumps(..., sort_keys=True, separators=(",", ":"))`. The function
  `_chain_audit_event` computes the record hash over the event before adding the chain fields, then
  stamps `prev_hash` and `record_hash` onto the event and advances the stored head
  (`state.audit_prev_hash`).
- The chain is layered over already-redacted events: audit principals are non-reversible (a key-id
  digest prefix or summarized JWT claims) and payloads are summarized into fingerprints
  (`_payload_fingerprint`: counts, roles, prompt hash), so chaining adds integrity without
  reintroducing raw prompt or credential data.
- The live construction matches the auditor/verifier reference in
  [`paper/evidence-model/audit_chain.py`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/paper/evidence-model/audit_chain.py) byte for byte
  (same genesis, same canonical form, same `SHA-256(prev || canonical(record))`), so the same tooling
  that an auditor runs verifies the live log.

## Consequences

- Any edit, insertion, deletion, or reordering of emitted records breaks the chain and is detectable
  by recomputation. The control adds one SHA-256 per request and two fields per event — effectively
  free, with no extra service to operate.
- Detecting a wholesale rewrite (re-chaining every record from genesis) requires an external
  commitment to the head hash. The reference model is explicit about this: editing a record without
  re-chaining is caught by the internal consistency check, while a full re-chain is caught only by an
  anchor mismatch against an externally committed head.
- The chain is per gateway replica (per process); the head lives in `app.state`. With multiple
  replicas there are multiple chains, and a process restart starts a new chain from genesis.
  Verification therefore operates per-chain, and cross-replica/long-horizon integrity depends on the
  log shipping and anchoring the operator puts around it.
- Keeping the gateway implementation and the paper/evidence verifier in lockstep is a maintenance
  obligation: the canonical form and genesis must not drift, or the auditor tooling stops matching.

## Alternatives considered

- **Plain append-only logging (no chaining).** Simplest, but provides no detection of after-the-fact
  edits or reordering — the exact property the audit trail needs for handoff. Rejected.
- **External managed audit log / SIEM with immutability guarantees.** Strong for retention and
  cross-service correlation, and operators are encouraged to ship these events into one. Rejected as
  the in-service mechanism because it adds an infrastructure dependency to get a property a few lines
  of SHA-256 provide locally, and it does not let the bundled `paper/evidence-model` tooling verify
  integrity offline.
- **Merkle tree per batch.** Gives efficient inclusion proofs at scale. Rejected as over-engineered
  for a per-request, per-process event stream; a linear hash chain (Crosby & Wallach style, as the
  reference notes) detects the same tampering with far less complexity. A future ADR could revisit
  this if external anchoring and inclusion proofs become requirements.
- **HMAC/keyed signing of each record.** Adds authenticity if a key is held outside the writer, but
  introduces key management and still needs anchoring to defeat a full rewrite. Rejected for now in
  favor of the unkeyed chain plus an external head commitment, which keeps the control dependency-free
  while leaving anchoring to the operator.
