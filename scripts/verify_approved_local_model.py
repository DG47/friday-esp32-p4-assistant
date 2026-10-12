#!/usr/bin/env python3
"""Read-only pin verification of approved public weights; no private state or inference."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

PIN = 'sha256:a7eb95c53bcf96b4bdd008d0fab4a5dac88047d9c1a7a9ab88ed453423fbd87c'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest_file(path, expected_size):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size == expected_size, 'Model blob size/type mismatch')
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return 'sha256:' + digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--model-store', required=True, type=Path)
    parser.add_argument('--evidence', required=True, type=Path)
    args = parser.parse_args()
    raw = args.manifest.read_bytes()
    require('sha256:' + hashlib.sha256(raw).hexdigest() == PIN, 'Unapproved source manifest')
    manifest = json.loads(raw)
    cache_manifest = args.model_store / 'manifests/registry.ollama.ai/library/qwen3.6/35b'
    fd = os.open(cache_manifest, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        cached = stream.read(1048577)
    require(len(cached) <= 1048576 and 'sha256:'+hashlib.sha256(cached).hexdigest() == PIN,
            'Installed model manifest differs from pinned source; stop before inference')
    verified = []
    for entry in [*manifest['layers'], manifest['config']]:
        digest = entry['digest']
        file = args.model_store / 'blobs' / digest.replace(':', '-')
        actual = digest_file(file, entry['size'])
        require(actual == digest, 'Installed model blob differs from pinned source; stop before inference')
        verified.append({'digest': actual, 'bytes': entry['size'], 'media_type': entry['mediaType']})
        print(json.dumps({'verified': actual, 'bytes': entry['size']}), flush=True)
    report = {'status': 'pinned_installed_artifacts_verified', 'manifest_digest': PIN,
              'model': 'qwen3.6:35b', 'blobs': verified, 'inference': False,
              'runtime_changes': False, 'credentials_read': False}
    args.evidence.mkdir(parents=True, exist_ok=True)
    (args.evidence/'installed-model-pin-verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
