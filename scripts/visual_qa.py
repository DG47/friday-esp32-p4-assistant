#!/usr/bin/env python3
"""Render local Friday QA fixtures using installed Chrome and a disposable profile."""
import argparse
import json
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('fixture', type=Path)
parser.add_argument('--screenshot', type=Path)
parser.add_argument('--width', type=int, default=1200)
parser.add_argument('--height', type=int, default=900)
args = parser.parse_args()
chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
if not chrome.is_file():
    raise SystemExit('Installed Google Chrome is required; nothing will be installed.')
with tempfile.TemporaryDirectory(prefix='friday-visual-qa-') as profile:
    command = [str(chrome), '--headless', '--disable-gpu', '--no-first-run',
               '--disable-background-networking', f'--user-data-dir={profile}',
               f'--window-size={args.width},{args.height}', '--virtual-time-budget=2000']
    if args.screenshot:
        command.append(f'--screenshot={args.screenshot.resolve()}')
    else:
        command.append('--dump-dom')
    command.append(args.fixture.resolve().as_uri())
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        output, error = process.communicate(timeout=25)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            output, error = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            output, error = process.communicate()
    if args.screenshot:
        if not args.screenshot.is_file():
            raise SystemExit('Chrome produced no screenshot; check host execution permission.')
        print(f'Screenshot saved: {args.screenshot}')
    else:
        match = re.search(r'<pre id="qa-result">(.*?)</pre>', output)
        if not match:
            raise SystemExit('Chrome produced no interaction result; check host execution permission.')
        results = json.loads(match.group(1))
        print(json.dumps(results, indent=2))
        if not all(item['passed'] for item in results):
            raise SystemExit(1)
