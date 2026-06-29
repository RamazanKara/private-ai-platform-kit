# Sample Supply-Chain Scan Summary

Generated: `2026-05-31T00:00:00Z`

Status: pass

Gate: HIGH and CRITICAL image vulnerabilities must be zero.

| Image | SBOM | Trivy SARIF |
| --- | --- | --- |
| `private-ai-platform-kit/inference-gateway:local-scan` | `.out/results/supply-chain/inference-gateway-20260531T000000Z.spdx.json` | `.out/results/supply-chain/trivy-inference-gateway-20260531T000000Z.sarif` |
| `private-ai-platform-kit/rag-service:local-scan` | `.out/results/supply-chain/rag-service-20260531T000000Z.spdx.json` | `.out/results/supply-chain/trivy-rag-service-20260531T000000Z.sarif` |

Use `make image-scan` to generate fresh SBOM, SARIF, checksum, JSON, and Markdown evidence.
