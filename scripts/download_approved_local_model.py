#!/usr/bin/env python3
"""Approved official local-model pull with manifest-bound progress. No inference/config changes."""
import argparse
import http.client
import json
from pathlib import Path
import time


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--evidence', required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    pinned_manifest = 'sha256:a7eb95c53bcf96b4bdd008d0fab4a5dac88047d9c1a7a9ab88ed453423fbd87c'
    import hashlib
    require('sha256:'+hashlib.sha256(args.manifest.read_bytes()).hexdigest() == pinned_manifest, 'Manifest differs from pinned approved source')
    layers = {entry['digest']: entry['size'] for entry in [*manifest['layers'], manifest['config']]}
    total = sum(layers.values())
    args.evidence.mkdir(parents=True, exist_ok=True)
    progress = {'model': 'qwen3.6:35b', 'manifest_digest': pinned_manifest, 'phase': 'starting',
                'total_bytes': total, 'completed_bytes': 0, 'inference': False, 'credential_operations_requested': False,
                'runtime_configuration_changed': False, 'download_cancellation_proven': False}
    completed = {}
    started = time.monotonic()
    last_emit = 0
    c = http.client.HTTPConnection('127.0.0.1', 11434, timeout=60)
    try:
        c.request('POST', '/api/pull', body=json.dumps({'model': 'qwen3.6:35b', 'stream': True}),
                  headers={'Content-Type': 'application/json'})
        response = c.getresponse()
        require(response.status == 200, 'Local public-model pull refused')
        while True:
            line = response.readline(1048577)
            if not line:
                break
            require(len(line) <= 1048576, 'Invalid pull progress size')
            data = json.loads(line)
            if data.get('error'):
                raise RuntimeError('Local model pull failed: ' + str(data['error'])[:300])
            digest = data.get('digest')
            if digest:
                require(digest in layers, 'Pull differs from pinned official manifest; stop before inference')
                count = data.get('completed', 0)
                require(type(count) is int and 0 <= count <= layers[digest], 'Invalid pull byte count')
                completed[digest] = max(completed.get(digest, 0), count)
            progress['phase'] = data.get('status', 'unknown')
            progress['completed_bytes'] = sum(completed.values())
            elapsed = time.monotonic()-started
            progress['elapsed_seconds'] = round(elapsed, 1)
            progress['mean_bytes_per_second'] = round(progress['completed_bytes']/max(elapsed, 1))
            rate = progress['mean_bytes_per_second']
            progress['estimated_remaining_seconds'] = round((total-progress['completed_bytes'])/rate) if rate else None
            if elapsed-last_emit >= 3 or progress['phase'] == 'success':
                last_emit = elapsed
                (args.evidence/'model-download-progress.json').write_text(json.dumps(progress, indent=2)+'\n')
                print(json.dumps(progress), flush=True)
        require(progress['phase'] == 'success', 'Pull stream ended without reported success; daemon outcome uncertain')
        progress['status'] = 'daemon_reported_success_pin_verification_pending'
    except Exception as error:
        progress['status'] = 'failed_or_unknown'
        progress['error'] = str(error)
        # Closing the stream never proves daemon-side download cancellation.
        raise
    finally:
        c.close()
        (args.evidence/'model-download-progress.json').write_text(json.dumps(progress, indent=2)+'\n')


if __name__ == '__main__':
    main()
