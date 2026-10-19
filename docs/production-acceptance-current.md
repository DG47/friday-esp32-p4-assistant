# Friday feature acceptance — October 19, 2026

Baseline:ca9eed79d23dd8029ad9997c62bc1d0628731972. This document replaces the
older745bdf41 checklist. The audit candidate is not deployed. Source tests and
synthetic integrations do not establish full production acceptance, literal
sentience or absence of defects. Actual deployment flags/account validity are
owner data; the private handoff records the owner's separate current inventory.

| Feature | Source/default scope | Acceptance state and evidence |
| --- | --- | --- |
| Unified native context | Same-device Calendar/event/Weather relation, shared presence/content well; context/Stop firmware options default off | **Passed** host LVGL bounds, glyphs, protected facts and reentry. **Blocked** actual panel/touch, source/provider and installed-configuration acceptance. A backend update does not enable or flash native support. |
| Browser interruption | Negotiated target-bound Stop, ACK/dedup, local deadline and status | **Failed baseline; repaired candidate**: in-flight results restarted speech/replaced a stopped view. Candidate separates delivery ACK from presentation and preserves unrelated alerts/read-only views/new tasks. Actual Chrome/silent-spy fixtures; not acoustic proof. |
| Context ownership/restart | Bounded32 workspaces; opaque24h restart intent; source facts reread | **Failed baseline; repaired candidate**: eviction recreated ownerless authority and late Calendar returned stale success. Non-creating checks and workspace-instance fences cover replacement/eviction/same-ID reuse, including native navigation. **Not implemented** automatic bridging of distinct identities/shared universal workspace. |
| Explicit memory | Default off; local CRUD,500 ASCII characters, sequential full-page review, per-device scope; soft deletion | **Passed** SQL/restart/owner tests and68 actual Chrome checks. **Failed baseline browser latch; repaired candidate**. Memory is not automatically extracted or supplied to model reasoning. **Not implemented** provider-sharing approval, hard-purge/export UI or general autobiographical recall. |
| Voice/STT/TTS | Backend local Whisper/optional macOS speech; browser uses separate browser speech engines | **Failed baseline private STT exception disclosure; repaired candidate** with fixed public copy/reason codes. **Passed** synthetic thread ownership/cancel/metadata and browser interruption. **Blocked** real model availability/recognition, speaker/AEC, hardware interruption and soak. Ready/health are not those observations. Browser ASR/TTS locality/availability is not proved by backend local-only reasoning; typed browser input remains supported. |
| Tool outcome truth | Explicit English operation directives with operational targets use evidence-only action responses; ordinary conversation restored | **Passed bounded regressions**: original no-tool reminder claim and adversarial paraphrases cannot become action receipts/history for covered requests. No execution returns explicit no-action status. Mixed requests isolate tool-free chat. **Not established** universal action-intent recognition or semantic truthfulness of arbitrary conversational prose; see contract below. |
| Direct Calendar/Weather | Providers default none; Google read-only, approved Weather ZIPs; model sharing off by default | **Passed** synthetic freshness, units/clocks/DST, privacy, unavailable/stale and correction tests. **Blocked** real account/source validity and local-model end-to-end semantic acceptance in this audit. No Calendar write capability. |
| Music/timed lyrics/artwork | Default none; explicit Spotify opt-in; existing-device controls; conversational control default off | **Passed** mocked adapter/ownership/replay/lease/glyph tests. **Blocked** actual account/playback/lyrics/artwork acceptance. No invented artwork or unrestricted music search/selection. Opt-in conversational control is not established by a touch-control pass. |
| Bounded Mac reports | Default off, explicit nonsecret file allowlist, enum plan, fixed read-only renderer, exact scope approval | **Disabled** unless deliberately configured. Synthetic executor/fencing/artifact evidence passes; no real folder enrollment here. **Not implemented** general shell, arbitrary Mac app control or remote worker execution; worker presence is not an executor. |
| Phone/Telegram | Report connector default off, dedicated bot/numeric owner binding | **Disabled/deferred**. Mock HTTPS/poll/approval/delivery/recovery tests do not establish a live phone/bot path. **Not implemented** general phone companion/mobile app or reviewed Internet-facing deployment. |
| Auth/identity | Existing pairing/strict TLS, loopback admin, exact device/job/incarnation controls | **Passed** synthetic adversarial isolation/epoch/replay/invalid-auth tests. **Blocked** fresh physical authentication/owner setup. No implicit cross-device/account mapping or new permissions. |
| Stop/recovery | Durable admission revocation, immutable result IDs, same-device quarantine; native client default off | **Passed** bounded commit/failed storage/lost ACK/restart/resistant handler fixtures. **Blocked** universal process/remote-effect termination and physical one-second SLO. Reconciliation remains read-only; no clear/replay/attestation policy. Typed effect evidence remains required. |
| Backup/rollback | Manual exclusive0600 online SQLite backup/integrity check and isolated restore; no overwrite | **Passed** synthetic restore/ownership/quarantine regressions. Owner's separate installed acceptance records a13-table roundtrip. **Blocked** actual personal-state restoration and operational rollout acceptance. **Not implemented** automatic backup scheduling/restore UI. |
| Operations/free policy | Content-free diagnostics, readiness, supervisor/container checks; Settings auto is not a free-only policy | **Passed** bounded tests; free installations must explicitly keep local-only reasoning and local/disabled TTS. **Blocked** current real-model semantic/latency/resource and sustained service/physical soak. Optional paid modes are not enabled by this audit. Hosted CI is blocked until zero cost is established. |

## Candidate repairs and product limitation

Model-only text is not an operation receipt. Supported Calendar/Weather/reminder/
playback tools supply observed responses; failed/partial and round-limit admitted
effects retain explicit receipts. Proposed tool-call narrative is not stored as a
completed effect. Existing permission, confirmation and context fences remain.

The narrowed action contract is explicit English directives at clause starts
(optional "please" or "could you"), using a supported operation construction and
an operational target such as reminder, timer, playback, event, report or file.
This recognizes requests, not truth from generated words. On a covered request,
the final action response comes only from structured adapter outcomes. With no
observed execution it says "No action was performed"; raw model acknowledgment is
neither displayed nor stored. A read does not establish a requested mutation.
An unsupported operation still cannot acquire authority through this parser.

Ordinary greetings, explanations, clarifications, attributed history, hypotheticals
and negation retain model prose. Explicit mixed input such as "Complete my reminder
and explain why the sky looks blue" gets a separate tool-free conversational call
for the independent question. It receives no action arguments, outcomes, private
history or working references. Failure of that optional call cannot hide an
admitted action. Cancellation/Stop checks precede and follow model calls.

This is **a bounded action contract, not a universal hallucination detector**.
Unrecognized implicit commands, other languages, complex conditional scopes and
model claims made during ordinary conversation are not proven safe by it. Model
conversation and the isolated chat answer can still be wrong, including unexpected
action prose; neither is evidence of execution. Prompt instructions do not prove
otherwise. Mixed conditional input beginning with if/unless/when/suppose/imagine
is treated as conversation. Independent chat deliberately lacks prior context;
complex mixed requests should be split into separate turns. Chat following a
pending confirmation or the four-round limit is not separately generated in that
turn. General semantic acceptance remains open. The prior broad suppression is
removed, and the failed verb-based output filter is not shipped. Old conversation
history is not retroactively scrubbed.

Local Stop intent independently fences the interrupted target's visible/audible
result. Delivery can still be acknowledged without replaying output or clearing
uncertainty. Later explicitly admitted tasks remain usable. Memory decisions use
their existing single-use token/sequential cursor instead of a browser job-result
latch that their normal surface responses cannot clear. No consent is inferred.

Context freshness checks never recreate a missing workspace or load a checkpoint.
In-flight reads bind the actual workspace instance; the same textual request ID
cannot resurrect an evicted read. Fresh explicit admission/read is required after
eviction. Private facts are not made durable by this repair. STT error bodies and
paths are replaced by fixed device-visible copy while diagnostic categories remain.

## Evidence and release boundary

The PR/private handoff records exact candidate SHA, local aggregate/adversarial
results and raw-log locations. Actual Chrome fixtures cover memory68, Calendar15,
Stop13 and interrupted-result15 checks. Native host capture compiles the production
LVGL renderer with synthetic content. Mocks, intercepted account transports and
stubbed DSP are explicitly not real provider, model, phone or acoustic acceptance.

No runtime/device, credentials, source folders, permissions, account access or
integration activation is included. Keep report/real-project/Telegram switches off
unless separately configured. Calendar model sharing and conversational music can
remain disabled; this audit does not enable them. Preserve TLS/CA, pairing/NVS,
local-only configuration, current DB and uncertain rows. No firmware change is
required for these repairs. Source publication is a draft with automatic hosted
CI skipped under the no-paid constraint; no merge/deployment before coordinated
review. Read-only reconciliation stays read-only until typed evidence is designed.

The whole product remains **not fully production accepted**. Outstanding gates
include actual local-model/source semantics, physical Wi-Fi/panel/touch/voice/AEC/
Stop/reconnect/soak, and deliberately missing general memory/worker/phone capabilities.
The owner's physical checklist is an acceptance plan, not completed evidence.
