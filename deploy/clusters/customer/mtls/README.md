# Encryption in Transit (Opt-In Overlay)

By default the kit's in-cluster data plane is **plaintext HTTP**. NetworkPolicies restrict *who*
may connect (default-deny plus explicit allows), but they do not encrypt traffic on the wire. For a
private-AI platform the payloads that traverse the pod network (prompts, completions, retrieved RAG
context, and the `X-API-Key` header) are exactly the data a regulated deployment must protect in
transit. Encrypting the data plane is therefore an operator responsibility, and this overlay gives
two reviewed, opt-in ways to do it. Pick one; do not apply both.

See [docs/threat-model.md](../../../../docs/threat-model.md) (Transport confidentiality) and the
"Encryption in transit" row of [docs/production-readiness.md](../../../../docs/production-readiness.md).

## Option A: Service mesh mTLS (recommended)

If the cluster runs (or can run) a mesh, this is the lowest-friction path: the mesh issues and
rotates workload certificates and encrypts pod-to-pod traffic transparently, with no service change.

- Label the platform namespaces for sidecar injection (mesh-specific), then apply
  [`istio-peerauthentication-strict.yaml`](istio-peerauthentication-strict.yaml) to require STRICT
  mTLS in each platform namespace. Linkerd is equivalent: annotate the namespaces for injection and
  mTLS is on by default.
- Cilium (no sidecars) is another option: enable WireGuard or IPsec transparent encryption at the
  CNI, which encrypts all node-to-node pod traffic without per-namespace policy.

## Option B: cert-manager-issued TLS

When a mesh is not available, terminate TLS in each service behind a cert-manager-issued certificate.
[`cert-manager-selfsigned.yaml`](cert-manager-selfsigned.yaml) ships a self-signed `ClusterIssuer`
and an example `Certificate` for the gateway; replace the self-signed issuer with your enterprise CA
(or an ACME issuer) and mount the resulting secret into the gateway/RAG pods, fronting them with a
TLS listener. This is more moving parts than a mesh and is offered for clusters that standardize on
cert-manager rather than a mesh.

## Scope

This overlay is **not** wired into the default GitOps apps; it is applied deliberately per
environment. The certificate authority, mesh installation, and secret material are customer-owned.
