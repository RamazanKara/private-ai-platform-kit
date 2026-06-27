# Summary

Describe the change and why it is needed.

# Validation

- [ ] `make validate`
- [ ] `make api-contract` if public routes or schemas changed
- [ ] `make config-contract` if settings, env vars, Helm values, or chart defaults changed
- [ ] `make production-check` if platform controls changed
- [ ] `make image-scan` if images, Dockerfiles, or runtime dependencies changed
- [ ] `make release-gate-strict` if release evidence changed

# Compatibility

- [ ] Local lab behavior remains compatible
- [ ] Customer overlay behavior remains compatible or migration notes are included
- [ ] Security-sensitive behavior is documented

# Evidence

Link or paste the relevant command output summary. Do not include secrets, raw prompts, customer data, or private context.
