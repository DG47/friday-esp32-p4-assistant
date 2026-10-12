# Fail-closed local-only reasoning

Choose `ASSISTANT_PROVIDER=local-only`, a canonical numeric loopback
`OLLAMA_BASE_URL=http://127.0.0.1:11434`, and an explicitly approved installed
`OLLAMA_MODEL`. TTS must be disabled or use local macOS synthesis. Paid provider keys
do not change this routing. Existing auto/mock/other provider modes remain available
for explicit configuration; auto is not a local-only policy.

Settings, routing, readiness and every native completion validate the endpoint/model.
Only numeric loopback or exact localhost canonicalized to loopback is accepted.
Credentials, extra paths/query/fragments, ambiguous numeric addresses, LAN/remote
hosts, proxies and redirects are rejected. Before sending a prompt, the adapter checks
`GET /api/status` for boolean `cloud.disabled=true`, then `POST /api/show` for a local
model with required completion/tool capabilities. Missing/unknown cloud policy, remote
metadata and absent local models fail closed. There is no paid or mocked fallback.
Readiness performs metadata checks; it does not download or preload a model.

The native Ollama adapter preserves tool call/result identity and handles native
function envelopes. A typed local-model failure is bounded, retained without prompt
content and terminal for the queued attempt. Download helpers require explicit use,
verify official content-addressed model manifests and do not change daemon policy.
Model files and daemon configuration are not included in this publication.

Working context is bounded and tied to the current owner/request. Cutoff history cannot
silently revive stale references. Calendar clocks and durations use validated UTC facts
and the configured IANA timezone, including DST boundaries. The narrow English next-
event route forces a bounded fresh provider read and renders clocks deterministically.
Failed/stale reads do not reuse old events or continue model reasoning. Named/ordinal
or unsupported referents clarify rather than substitute the earliest event.

Actual local-model tests have run. They demonstrate bounded native tool roundtrips,
context correction and approved synthetic report planning through the production
WebSocket dispatcher. The report run used qwen3.6:35b, context 8192, output 180 tokens,
single inference concurrency and cloud-disabled loopback policy. Duplicate/reconnect
kept one verified artifact and execution admission; a separate report was stopped.
These are scoped passes, not general scheduling or factual reliability. Earlier runs
showed stale Calendar reuse, timezone arithmetic errors and mislabeled Weather units.
Deterministic clock/read-freshness paths address their defined cases; broader model
semantics and acoustic/physical voice acceptance remain unproved.

No account credentials, personal source files, raw model transcripts, machine logs,
model binaries or local private Git history are published. See
[publication validation](current-updates-validation.md) for the sanitized result summary.

## Primary references

- [Ollama cloud controls](https://docs.ollama.com/faq)
- [Native request/result types](https://raw.githubusercontent.com/ollama/ollama/v0.35.0/api/types.go)
- [Native metadata and chat routes](https://raw.githubusercontent.com/ollama/ollama/v0.35.0/server/routes.go)
- [Official local model catalog](https://ollama.com/library/qwen3.6:35b)
- [Qwen model card and license](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)
