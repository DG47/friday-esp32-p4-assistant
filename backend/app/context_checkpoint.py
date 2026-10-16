"""Bounded resume intent, never cached Calendar facts, Weather readings or consent."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import re
import sqlite3

HEX = re.compile(r'^[0-9a-f]{24}$')


class ContextCheckpointStore:
    def __init__(self, path, clock):
        self.path, self.clock = path, clock
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS context_checkpoints (
                device_id TEXT PRIMARY KEY, focus TEXT NOT NULL,
                weather_ref TEXT, location TEXT, source_fingerprint TEXT, calendar_range TEXT NOT NULL,
                expires_at TEXT NOT NULL)''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(context_checkpoints)')}
            if 'calendar_range' not in columns:
                db.execute("ALTER TABLE context_checkpoints ADD COLUMN calendar_range TEXT NOT NULL DEFAULT 'today'")

    def load(self, device):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM context_checkpoints WHERE expires_at <= ?', (self.clock().astimezone(timezone.utc).isoformat(),))
            row = db.execute('SELECT focus,weather_ref,location,source_fingerprint,calendar_range,expires_at FROM context_checkpoints WHERE device_id=?', (device,)).fetchone()
        if row is None or not HEX.fullmatch(row[0]):
            return None
        focus, ref, location, fingerprint, calendar_range, expires_at = row
        try:
            remaining = datetime.fromisoformat(expires_at) - self.clock().astimezone(timezone.utc)
            if not timedelta(0) < remaining <= timedelta(hours=24):
                return None
        except (ValueError, TypeError):
            return None
        if calendar_range not in {'today', 'next'}:
            return None
        if ref is not None and (not HEX.fullmatch(ref) or not isinstance(location, str)
                or not re.fullmatch(r'\d{5}', location) or not isinstance(fingerprint, str)
                or not HEX.fullmatch(fingerprint)):
            return None
        return focus, ref, location, fingerprint, calendar_range

    def save(self, device, state):
        if not state.focus:
            self.delete(device)
            return
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO context_checkpoints (device_id,focus,weather_ref,location,source_fingerprint,calendar_range,expires_at) VALUES (?,?,?,?,?,?,?)',
                       (device, state.focus, state.weather_event_ref, state.location,
                        state.weather_source_fingerprint, state.calendar_range, (self.clock().astimezone(timezone.utc)+timedelta(hours=24)).isoformat()))
            db.execute('DELETE FROM context_checkpoints WHERE expires_at <= ?', (self.clock().astimezone(timezone.utc).isoformat(),))
            db.execute('''DELETE FROM context_checkpoints WHERE device_id NOT IN
                          (SELECT device_id FROM context_checkpoints ORDER BY expires_at DESC, rowid DESC LIMIT 32)''')

    def delete(self, device):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM context_checkpoints WHERE device_id=?', (device,))
