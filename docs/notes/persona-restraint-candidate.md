# Persona restraint candidate: interaction review

This is a separate prompt-only implementation layered on verified UI candidate 6955f799125353d03e3b875085d06f22499465c0. No voice/provider settings, cancellation code, tools, permission scopes or service behavior changed.

The established target is a competent, concise Friday/Jarvis-inspired butler with occasional dry wit. Existing agent instructions already require competence first, polish, natural dry sarcasm, no flattery, no tool narration, no repeated request, no urgent jokes, one or two short spoken sentences, follow-up context and confirmed tool success. This candidate adds contextual restraint, explicit no-joke rules for errors/failed actions/health/financial distress/grief/sensitive conversations, quiet listening, no repeated acknowledgments, concise response after interruption, and explicit examples separated from user facts. Humor remains optional, not a compulsory punchline.

Existing local service definition selects macOS speech; no voice override was found in the inspected nonsecret settings. Code defaults are Daniel (English UK), rate 180, gain 0.25. This is configured selection, not an auditory or active-process verification. Alternative OpenAI defaults exist but were not selected, invoked or changed.

Turn-taking audit: recognized activation cancels current speech, then listening state is emitted; accepted work emits thinking; speech queueing/playback emits speaking; finished streams emit idle. Backend cancellation is serialized and device playback epochs reject stale work. Acoustic detection is separate: wake probes collect approximately three seconds of audio before recognition; physical detection/cancellation latency and voice character were not tested. This candidate does not claim instant spoken interruption detection or change that pipeline.

## Synthetic proposed transcripts — not observed model output

| Explicit synthetic context | Proposed response |
| --- | --- |
| Tool confirms three meetings before lunch; ordinary low-stakes request | “Three meetings before lunch. An ambitious scheduling choice.” |
| Tool reports save failed | “That didn't save. Please try again.” |
| Two actual synthetic reminder matches: water plants / pay bill | “Which reminder: water the plants or pay the bill?” |
| User in a sensitive conversation asks for quiet | “Of course.” Then no unsolicited prompt. |
| New request after recognized activation cancels speech | Listen to the new request; answer it concisely. No spoken interruption apology or immediate acoustic-detection claim. |

Deterministic tests use a scripted provider and synthetic content. They verify that restraint instructions reach the real model-call path, explicit context reaches follow-ups, examples are not copied into conversation facts, failures reach the provider as failures, no extra acknowledgment call is inserted, and the exposed tool schema/action behavior is unchanged. They do not establish real model humor, empathy, restraint, voice quality or runtime silence compliance. The quiet and interruption guidance is instruction-level; existing transport cancellation gates remain authoritative. Existing confirmation/ambiguity/cancellation suites provide separate engine regression coverage.

The next physical review should sample ordinary task completion, serious requests, failures, ambiguous changes, context followups and recognized interruption. Use explicitly synthetic scenarios and a separately authorized provider/device session; no such run was performed here.
