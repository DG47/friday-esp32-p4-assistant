# Observed read prose boundary

A scripted provider reproduced three failures after real synthetic tools returned:
Calendar/Weather timestamps and Fahrenheit readings were paraphrased with a wrong
clock/unit, Calendar sharing-disabled became an empty live calendar, and disconnected
Weather became an invented current reading. All three regressions fail on main 649d184.

The tool loop now renders final Calendar/Weather read facts from the observed payload,
using existing deterministic offset-aware clock facts and explicit sample/unavailable/
sharing-disabled state. Weather includes source, Fahrenheit and the exact forecast
interval when available. The latest read of each supported tool supersedes its earlier
read within the turn; Calendar and Weather remain separately attributed. The actual
answer is stored in conversation history. Existing confirmation and verified playback
paths keep their semantics. Two old tests expected provider prose (including an
invented interview time); they now assert observed facts and failure state instead.

Three new adversarial tests and the aggregate 626-test suite pass. No prompt-only fix,
new inference, account call, configuration or firmware change is included. The provider
still chooses tools and may emit text before/without a supported read. General knowledge,
no-tool hallucinations, arbitrary planning synthesis and actual local-model acceptance
remain unproved. Returning verified readings instead of free-form advice is a deliberate
scope limit; do not claim general factual reliability is solved.


## Exact unsupported-temporal trace and explicit ZIP

The owner-provided synthetic trace used get_weather location=context with an explicit
event reference, observed ZIP10010/70.7F and a10% probability only during22:00–23:00UTC.
The model added “no rain expected until later tonight.” The merged observed renderer
already excludes that extrapolation, but it named the city without answering the
explicit requested ZIP. This narrow follow-up includes the approved ZIP beside the
city label. An exact-payload adversarial-provider regression fails on2c2bc89 due to the
missing ZIP and passes with ZIP10010, Fahrenheit,10% and exact offset-aware interval,
without a rain-free-before/until claim. Only the original synthetic payload is committed;
no local paths, model metadata, account data or private history are included.

Aggregate642 tests pass. This adds no source, network/inference, configuration or
firmware change and does not solve general prose/tool-selection accuracy.
