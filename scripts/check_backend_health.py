#!/usr/bin/env python3
"""Probe the backend readiness endpoint over the pinned local CA.

Exit 0 when ready, 1 otherwise. With --kickstart-after N, restart the LaunchAgent
after N consecutive failures; a hung process is the case KeepAlive cannot see.
"""
import argparse
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

LABEL = "com.friday.backend"


def probe(url: str, ca_path: Path, timeout: float) -> tuple[bool, str]:
    context = ssl.create_default_context(cafile=str(ca_path)) if ca_path else ssl.create_default_context()
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=context) as response:
            return response.status == 200, f"http_{response.status}"
    except urllib.error.HTTPError as error:
        return False, f"http_{error.code}"
    except (urllib.error.URLError, OSError, ssl.SSLError) as error:
        return False, type(error).__name__


def read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"failures": 0}


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(state, output)
    os.chmod(path, 0o600)


def kickstart(label: str) -> bool:
    result = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
                            capture_output=True, text=True, timeout=30)
    return result.returncode == 0


def main(argv=None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://127.0.0.1:8000/ready")
    parser.add_argument("--ca", type=Path, default=root / "data/tls/friday-ca.crt")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--state", type=Path, default=root / "backend/data/health-state.json")
    parser.add_argument("--kickstart-after", type=int, default=0,
                        help="restart the LaunchAgent after this many consecutive failures (0 disables)")
    parser.add_argument("--label", default=LABEL)
    args = parser.parse_args(argv)
    ok, detail = probe(args.url, args.ca, args.timeout)
    state = read_state(args.state)
    if ok:
        write_state(args.state, {"failures": 0})
        print(f"ready {detail}")
        return 0
    failures = int(state.get("failures", 0)) + 1
    restarted = False
    if args.kickstart_after and failures >= args.kickstart_after:
        restarted = kickstart(args.label)
        failures = 0
    write_state(args.state, {"failures": failures})
    print(f"not ready {detail} failures={failures}" + (" restarted" if restarted else ""))
    return 1


if __name__ == "__main__":
    sys.exit(main())
