# Portable local TTS investigation (not integrated)

No new TTS package/model has been installed or enabled. There is no paid fallback. The current Linux image intentionally reports macOSspeech unavailable.

Piper is the first bounded option to evaluate: upstream OHF-Voice maintains a local neural engine with Python/WAV and streaming APIs, embedding eSpeak NG for phonemization. Engine license is GPL-3.0; each chosen voice needs its own MODEL_CARD/license review and pinned ONNX/config hashes. It has BritishEnglish voices. Native sample rate may differ; conversion must produce the existing monoPCM16/24kHz format and preserve bounded length/output/cancellation and playbackack rules. Existing PyAV/ONNXRuntime imports succeed in the image but do not establish synthesis. [Upstream](https://github.com/OHF-Voice/piper1-gpl), [API](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md), [voice requirements](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/VOICES.md).

eSpeak NG is a smaller explicit local option available through standard distribution packages and WAVstdout; it needs output conversion and audible quality acceptance. Do not silently substitute it for the selected assistant voice. [Upstream](https://github.com/espeak-ng/espeak-ng), [guide](https://github.com/espeak-ng/espeak-ng/blob/master/docs/guide.md).

Kokoro is another local inference library; its upstream example uses torch, espeak-ng and24kHz output, with Apache-licensed82M weights. Its dependency/memory envelope needs separate CPU benchmarking before selecting it for a small host. [Upstream](https://github.com/hexgrad/kokoro).

Next gate after recovery clearance: select a legally usable voice, pin library/model/config and optional resampler dependencies, synthesize original test text only inside an isolated container without provider credentials/network, and measure format, intelligibility, cold/warm latency, output budget, cancellation/drain and real device acknowledgment. Do not infer cloud readiness from library import or from a waveform alone. No model download or benchmark has occurred here.
