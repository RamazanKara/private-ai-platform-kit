#!/usr/bin/env bash
# Walk through the governed request path of the Compose evaluation stack and prove the
# audit trail at the end. Run after `make compose-up`; exits non-zero on the first surprise.
#
# Each step prints what it shows, so the output doubles as a guided demo:
#   1. an authenticated chat completion, with its request id and remaining budget
#   2. a streamed completion
#   3. a prompt carrying a credential, blocked before it reaches the model
#   4. a model outside the allowlist, refused
#   5. a missing API key, refused
#   6. an agent-action receipt (a denied egress attempt) recorded on the same chain
#   7. tenant-scoped retrieval from the RAG service
#   8. usage and estimated cost for the sandbox
#   9. the audit log exported and its hash chain verified
#  10. one receipt edited after the fact, and the verifier catching it
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
cd "$ROOT"

GATEWAY="${PAK_GATEWAY_URL:-http://127.0.0.1:${PAK_GATEWAY_PORT:-8080}}"
RAG="${PAK_RAG_URL:-http://127.0.0.1:${PAK_RAG_PORT:-8090}}"
KEY="${PAK_API_KEY:-local-development-only}"
MODEL="${PAK_MODEL:-qwen2.5:0.5b}"
COMPOSE=(docker compose -f deploy/compose/compose.yaml)
OUT="${OUTPUT_DIR:-.out/compose}"

require_cmd curl "curl is required for the smoke requests."
require_cmd python3 "Python 3 is required to parse responses and verify the audit chain."
require_cmd docker "Docker is required to read the gateway's audit log."
mkdir -p "$OUT"

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok() { printf '   \033[32mok\033[0m  %s\n' "$*"; }
fail() { printf '   \033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

# request METHOD PATH [JSON] [extra curl args...] -> writes body to $OUT/body.json, echoes status
request() {
  local method="$1" path="$2" data="${3:-}"
  shift 3 || shift $#
  local args=(-sS -o "$OUT/body.json" -w '%{http_code}' -X "$method" "$GATEWAY$path" -H "X-API-Key: $KEY")
  [[ -n "$data" ]] && args+=(-H 'Content-Type: application/json' -d "$data")
  curl "${args[@]}" "$@"
}

json() { python3 -c "import json,sys; d=json.load(open('$OUT/body.json')); print($1)"; }

step "0. Waiting for the gateway to report ready ($GATEWAY/readyz)"
for _ in $(seq 1 60); do
  if [[ "$(curl -s -o /dev/null -w '%{http_code}' "$GATEWAY/readyz")" == "200" ]]; then break; fi
  sleep 5
done
[[ "$(curl -s -o /dev/null -w '%{http_code}' "$GATEWAY/readyz")" == "200" ]] || fail "gateway not ready; run: make compose-up"
ok "gateway and runtime ready"

step "1. Chat completion through the governed path"
status="$(request POST /v1/chat/completions "{\"model\":\"$MODEL\",\"max_tokens\":24,\"temperature\":0,\"messages\":[{\"role\":\"user\",\"content\":\"Repeat exactly: Hello from your own hardware.\"}]}" -D "$OUT/headers.txt")"
[[ "$status" == "200" ]] || fail "chat returned $status: $(cat "$OUT/body.json")"
ok "answer: $(json "repr(d['choices'][0]['message']['content'].strip()[:80])")"
ok "request id $(grep -i '^x-request-id:' "$OUT/headers.txt" | tr -d '\r' | cut -d' ' -f2), sandbox $(grep -i '^x-sandbox-id:' "$OUT/headers.txt" | tr -d '\r' | cut -d' ' -f2), tokens left $(grep -i '^x-ratelimit-remaining-tokens:' "$OUT/headers.txt" | tr -d '\r' | cut -d' ' -f2)"

step "2. Streaming (server-sent events)"
chunks="$(curl -sS -N "$GATEWAY/v1/chat/completions" -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"stream\":true,\"max_tokens\":24,\"messages\":[{\"role\":\"user\",\"content\":\"Count to five.\"}]}" | grep -c '^data: {' || true)"
[[ "$chunks" -gt 0 ]] || fail "no streamed events"
ok "$chunks streamed events"

step "3. A prompt containing a credential is blocked before the model sees it"
fake_token="ghp_$(printf 'Z%.0s' $(seq 1 36))"
status="$(request POST /v1/chat/completions "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"deploy with $fake_token\"}]}")"
[[ "$status" == "400" ]] || fail "expected 400, got $status"
ok "400 $(json "d['detail']['reason']")"

step "4. A model outside the allowlist is refused"
status="$(request POST /v1/chat/completions '{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}')"
[[ "$status" == "400" ]] || fail "expected 400, got $status"
ok "400 $(json "d['detail']['reason']")"

step "5. A request without an API key is refused"
status="$(curl -sS -o "$OUT/body.json" -w '%{http_code}' "$GATEWAY/v1/models")"
[[ "$status" == "401" ]] || fail "expected 401, got $status"
ok "401 $(json "d['detail']['reason']")"

step "6. An agent reports a blocked egress attempt; it lands on the same audit chain"
status="$(request POST /v1/receipts '{"action_type":"egress_denied","decision":"denied","target":"exfil.example.com:443","reason":"not in egress allowlist"}')"
[[ "$status" == "200" ]] || fail "receipt returned $status: $(cat "$OUT/body.json")"
ok "receipt recorded for sandbox $(json "d.get('sandbox_id', 'demo')")"

step "7. Tenant-scoped retrieval from the RAG service (over this repository's docs)"
status="$(curl -sS -o "$OUT/body.json" -w '%{http_code}' "$RAG/v1/rag/query" -H 'X-Sandbox-ID: demo' \
  -H 'Content-Type: application/json' -d '{"query":"How is the audit hash chain verified?","top_k":3}')"
[[ "$status" == "200" ]] || fail "rag query returned $status"
ok "top documents: $(json "', '.join(r['source'] for r in d['results'])")"

step "8. Usage and estimated cost for the sandbox"
status="$(request GET /v1/usage)"
[[ "$status" == "200" ]] || fail "usage returned $status"
ok "$(json "f\"sandbox {d['sandbox_id']}: {d['usage']['requests']} requests, {d['usage']['estimated_tokens']} estimated tokens, cost {d['estimated_cost']} {d.get('currency', '')}\"")"

step "9. Export the audit log and verify its hash chain"
"${COMPOSE[@]}" logs --no-color --no-log-prefix inference-gateway 2>/dev/null \
  | grep '"record_hash"' >"$OUT/gateway-audit.jsonl" || true
records="$(wc -l <"$OUT/gateway-audit.jsonl")"
[[ "$records" -gt 0 ]] || fail "no audit records found in the gateway log"
python3 scripts/audit-verify.py "$OUT/gateway-audit.jsonl"
ok "$records receipt lines verified ($OUT/gateway-audit.jsonl)"

step "10. Rewrite history: turn the blocked request's 400 into a 200 and verify again"
python3 - "$OUT/gateway-audit.jsonl" "$OUT/gateway-audit-tampered.jsonl" <<'PY'
import sys

source, target = sys.argv[1:]
lines = open(source, encoding="utf-8").read().splitlines()
for index, line in enumerate(lines):
    if '"status_code": 400' in line:
        lines[index] = line.replace('"status_code": 400', '"status_code": 200', 1)
        break
else:
    raise SystemExit("no denied receipt to tamper with")
open(target, "w", encoding="utf-8").write("\n".join(lines) + "\n")
PY
if python3 scripts/audit-verify.py "$OUT/gateway-audit-tampered.jsonl" >"$OUT/tampered-verify.txt"; then
  fail "the edited log still verified"
fi
ok "edit detected: $(head -n1 "$OUT/tampered-verify.txt" | sed -E 's/^chain [^ ]+ //; s/; [0-9]+ record\(s\)$//')"

printf '\n\033[1mAll checks passed.\033[0m Open the read-only console at %s/console\n' "$GATEWAY"
