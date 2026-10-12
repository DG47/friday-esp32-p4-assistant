"""Deterministic display clocks for validated, offset-aware tool timestamps."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def clock_point(value, zone):
    point = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(point, datetime) or point.tzinfo is None or point.utcoffset() is None:
        raise ValueError('A clock fact requires an explicit UTC offset')
    local = point.astimezone(ZoneInfo(zone))
    offset = local.strftime('%z')
    return {'iso': local.isoformat(), 'date': local.date().isoformat(),
            'time': local.strftime('%H:%M'), 'zone': zone,
            'utc_offset': offset[:3]+':'+offset[3:],
            'label': f"{local:%Y-%m-%d %H:%M} {local.tzname()} (UTC{offset[:3]}:{offset[3:]})"}


def interval_facts(start, end, zone):
    first = datetime.fromisoformat(start) if isinstance(start, str) else start
    last = datetime.fromisoformat(end) if isinstance(end, str) else end
    points = [clock_point(first, zone), clock_point(last, zone)]
    seconds = (last.astimezone(timezone.utc)-first.astimezone(timezone.utc)).total_seconds()
    if seconds <= 0:
        raise ValueError('A clock interval must end after its start')
    return {'start': points[0], 'end': points[1], 'elapsed_seconds': seconds,
            'duration_minutes': seconds/60, 'basis': 'elapsed UTC time; local wall clocks may cross DST or midnight'}


def event_clock_facts(event, zone):
    if event.get('all_day'):
        start, end = date.fromisoformat(event['all_day_start']), date.fromisoformat(event['all_day_end'])
        ZoneInfo(zone)
        if end <= start:
            raise ValueError('An all-day interval must have a later exclusive end date')
        return {'all_day': True, 'start_date': start.isoformat(),
                'end_date_exclusive': end.isoformat(), 'zone': zone}
    return interval_facts(event['starts_at'], event['ends_at'], zone)
