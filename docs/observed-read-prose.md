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
