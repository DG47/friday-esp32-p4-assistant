#!/usr/bin/env python3
"""Online SQLite backup. Does not read or print private rows or overwrite files."""
import argparse
import os
import sqlite3
from pathlib import Path


def backup_database(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise ValueError("Source database does not exist")
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(destination) as backup:
                original.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
                backup.execute("PRAGMA journal_mode=DELETE")
                backup.commit()
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if "original" in locals():
            original.close()
        if "backup" in locals():
            backup.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    backup_database(args.source, args.destination)
    print("Backup created and integrity verified")
