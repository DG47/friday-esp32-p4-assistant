#!/usr/bin/env python3
"""Online SQLite backup. Does not read or print private rows or overwrite files."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.backup import backup_database  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    backup_database(args.source, args.destination)
    print("Backup created and integrity verified")
