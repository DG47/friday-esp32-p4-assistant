"""Authenticated audio.end telemetry: fixed integer schema, globally rate-bounded."""
import time

FIELDS = ('seq', 'frames', 'samples', 'stream_samples', 'wall_ms', 'clean', 'playback', 'recording',
          'l_rms', 'r_rms', 'mono_rms', 'post_aec_rms', 'gain_rms', 'stream_rms',
          'corr_valid', 'corr_ppm', 'l_clips', 'r_clips', 'gain_clips', 'stream_clips', 'age_ms')


def validate_capture_metadata(payload):
    if type(payload) is not dict or len(payload) != len(FIELDS) or set(payload) != set(FIELDS):
        return None
    if any(type(payload[k]) is not int for k in FIELDS):
        return None
    if any(not 0 <= payload[k] <= 4294967295 for k in FIELDS if k != 'corr_ppm'):
        return None
    if payload['frames'] != 50 or payload['samples'] != 24000 or payload['stream_samples'] != 12000:
        return None
    if not -1000000 <= payload['corr_ppm'] <= 1000000:
        return None
    if payload['corr_valid'] not in (0, 1) or (not payload['corr_valid'] and payload['corr_ppm']):
        return None
    if payload['age_ms'] > 3000 or not 1 <= payload['wall_ms'] <= 60000:
        return None
    if payload['clean'] > 50 or payload['playback'] > 50 or payload['recording'] > 50:
        return None
    if any(payload[k] > 32768 for k in FIELDS if k.endswith('_rms')):
        return None
    if any(payload[k] > 24000 for k in ('l_clips', 'r_clips', 'gain_clips')) or payload['stream_clips'] > 12000:
        return None
    return {k: payload[k] for k in FIELDS}


class CaptureMetadataLog:
    def __init__(self):
        self._last = float('-inf')

    def record(self, logger, payload):
        # Called only in the authenticated audio.end branch on the event loop.
        values = validate_capture_metadata(payload)
        now = time.monotonic()
        if values is None or now - self._last < 5:
            return False
        self._last = now
        logger.info('capture_diag ' + ' '.join(k + '=%d' for k in FIELDS),
                    *(values[k] for k in FIELDS))
        return True
