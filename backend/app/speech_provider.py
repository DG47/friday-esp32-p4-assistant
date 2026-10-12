"""Portable provider selection; availability is not playback success."""
from dataclasses import dataclass


@dataclass(frozen=True)
class UnavailableSpeech:
    name: str
    reason: str

    async def synthesize(self, text):
        raise RuntimeError('Configured speech provider unavailable')

    async def close(self):
        pass


def create_speech_provider(settings):
    if settings.tts_provider == 'none':
        return None
    if settings.tts_provider == 'macos':
        from .local_speech import MacSpeech, LocalSpeechUnavailable
        try:
            return MacSpeech(settings.tts_local_voice, settings.tts_local_rate, settings.tts_local_gain)
        except LocalSpeechUnavailable:
            return UnavailableSpeech('macos', 'macos_runtime_unavailable')
    if settings.tts_provider == 'openai':
        # Explicit existing configuration only; this does not enable a fallback.
        from .speech import OpenAISpeech
        return OpenAISpeech(settings.openai_api_key, settings.tts_model, settings.tts_voice)
    raise ValueError('Unsupported speech provider')


def speech_availability(provider):
    if provider is None:
        return {'state': 'disabled', 'reason': 'not_configured'}
    if isinstance(provider, UnavailableSpeech):
        return {'state': 'unavailable', 'reason': provider.reason}
    return {'state': 'available', 'reason': 'playback_not_verified'}
