"""Bounded read-only Google events and honest contextual scenes."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from ..google_oauth import OAuthUnavailable, read_credentials, token_request
from ..models import CalendarEvent, Surface, SurfaceAction
from .calendar import MockCalendar


@dataclass
class CalendarSnapshot:
    status: str
    events: list[CalendarEvent] = field(default_factory=list)
    fetched_at: datetime | None = None
    truncated: bool = False
    message: str = ''

    def payload(self):
        return {'ok': self.status in {'ready', 'sample'}, 'status': self.status,
                'source': 'Google Calendar (read only)' if self.status != 'sample' else 'sample; no account connected',
                'fetched_at': self.fetched_at.isoformat() if self.fetched_at else None,
                'truncated': self.truncated, 'event_count': len(self.events), 'message': self.message,
                'events': [e.model_dump(mode='json') for e in self.events]}


def clean_text(value, limit=160):
    if not isinstance(value, str): return ''
    return ''.join(c for c in value if c >= ' ' and c not in '\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069')[:limit].strip()


def parse_event(value, display_zone, calendar_zone=None):
    if not isinstance(value, dict) or value.get('status') == 'cancelled': return None
    calendar_zone = calendar_zone or display_zone
    def point(part):
        if not isinstance(part, dict): raise ValueError('Invalid calendar date')
        if 'date' in part:
            return datetime.combine(date.fromisoformat(part['date']), time.min, calendar_zone).astimezone(display_zone), True
        raw = datetime.fromisoformat(part['dateTime'].replace('Z', '+00:00'))
        if raw.tzinfo is None:
            zone = ZoneInfo(part.get('timeZone') or calendar_zone.key)
            raw = raw.replace(tzinfo=zone)
            if raw.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != raw.replace(tzinfo=None):
                raise ValueError('Nonexistent local calendar time')
        return raw.astimezone(display_zone), False
    start, all_day = point(value.get('start'))
    end, end_all_day = point(value.get('end'))
    if all_day != end_all_day or end.astimezone(timezone.utc) <= start.astimezone(timezone.utc):
        raise ValueError('Invalid calendar interval')
    identifier = clean_text(value.get('id'), 256)
    if not identifier: raise ValueError('Missing calendar event identifier')
    attendees = value.get('attendees', [])
    if not isinstance(attendees, list): raise ValueError('Invalid calendar attendees')
    return CalendarEvent(id=identifier, title=clean_text(value.get('summary')) or 'Busy',
        starts_at=start, ends_at=end, all_day=all_day,
        all_day_start=date.fromisoformat(value['start']['date']) if all_day else None,
        all_day_end=date.fromisoformat(value['end']['date']) if all_day else None,
        recurring=bool(value.get('recurringEventId')),
        location=clean_text(value.get('location'), 160),
        people=[name for p in attendees[:8] if isinstance(p, dict) and (name := clean_text(p.get('displayName'), 48))])


class CalendarService:
    def __init__(self, settings, client=None, clock=None):
        self.provider = settings.calendar_provider
        self.zone = ZoneInfo(settings.assistant_timezone)
        self.calendar_id = settings.calendar_google_id
        self.credentials_path = settings.calendar_google_credentials_path
        self.sample = MockCalendar(settings.assistant_timezone)
        self.client = client
        self.owns_client = client is None
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.cache = {}
        self.actions = {}
        self.lock = asyncio.Lock()
        self.access_token = None
        self.token_until = datetime.min.replace(tzinfo=timezone.utc)
        self.last_status = 'sample' if self.provider == 'sample' else 'unconnected'
        self.retry_until = datetime.min.replace(tzinfo=timezone.utc)

    # Legacy sample APIs exist only for explicitly selected deterministic previews.
    def today(self, now=None): return self.sample.today(now) if self.provider == 'sample' else []
    def next_event(self, now=None): return self.sample.next_event(now) if self.provider == 'sample' else None
    def timeline_items(self, now=None): return self.sample.timeline_items(now) if self.provider == 'sample' else []

    def http(self):
        if self.client is None: self.client = httpx.AsyncClient(timeout=8, follow_redirects=False)
        return self.client

    async def token(self, force=False):
        now = self.clock()
        if not force and self.access_token and now < self.token_until: return self.access_token
        credentials = read_credentials(self.credentials_path)
        result = await token_request(self.http(), {'client_id': credentials.client_id,
            'client_secret': credentials.client_secret, 'refresh_token': credentials.refresh_token,
            'grant_type': 'refresh_token'})
        lifetime = result.get('expires_in', 3600)
        if not isinstance(lifetime, int) or not 60 <= lifetime <= 86400: raise OAuthUnavailable('error')
        self.access_token = result['access_token']
        self.token_until = now + timedelta(seconds=lifetime - 30)
        return self.access_token

    async def fetch(self, window, now):
        local = now.astimezone(self.zone)
        midnight = datetime.combine(local.date(), time.min, self.zone)
        lower = midnight if window == 'today' else local
        upper = datetime.combine(local.date() + timedelta(days=1 if window == 'today' else 7), time.min, self.zone)
        params = {'timeMin': lower.isoformat(), 'timeMax': upper.isoformat(),
                  'singleEvents': 'true', 'orderBy': 'startTime', 'showDeleted': 'false', 'maxResults': 100,
                  'fields': 'nextPageToken,timeZone,items(id,summary,start,end,status,recurringEventId,location,attendees(displayName))'}
        endpoint = 'https://www.googleapis.com/calendar/v3/calendars/' + quote(self.calendar_id, safe='') + '/events'
        events, seen, pages = [], set(), set()
        for page in range(10):
            token = await self.token()
            async def request():
                async with self.http().stream('GET', endpoint, params=params, headers={'Authorization': 'Bearer ' + token}, timeout=8, follow_redirects=False) as response:
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 1024 * 1024: raise OAuthUnavailable('error')
                    return response.status_code, data
            status, data = await request()
            if status == 401:
                token = await self.token(force=True)
                status, data = await request()
            if status == 403:
                try:
                    reasons = {e.get('reason') for e in json.loads(data).get('error', {}).get('errors', []) if isinstance(e, dict)}
                except (ValueError, TypeError, AttributeError): reasons = set()
                if reasons & {'rateLimitExceeded', 'userRateLimitExceeded', 'quotaExceeded', 'dailyLimitExceeded'}: raise OAuthUnavailable('error')
                raise OAuthUnavailable('revoked')
            if status == 401: raise OAuthUnavailable('revoked')
            if status != 200: raise OAuthUnavailable('error')
            result = json.loads(data)
            if not isinstance(result, dict) or not isinstance(result.get('items', []), list): raise ValueError('Invalid calendar response')
            if len(result.get('items', [])) > 100: raise ValueError('Calendar response exceeds limit')
            for item in result.get('items', []):
                event = parse_event(item, self.zone, ZoneInfo(result.get("timeZone") or self.zone.key))
                if event and event.ends_at.astimezone(timezone.utc) > lower.astimezone(timezone.utc) and event.starts_at.astimezone(timezone.utc) < upper.astimezone(timezone.utc):
                    key = (event.id, event.starts_at.isoformat())
                    if key not in seen: events.append(event);seen.add(key)
            next_page = result.get('nextPageToken')
            if not next_page: break
            if not isinstance(next_page, str) or len(next_page) > 2048 or next_page in pages: raise ValueError('Invalid calendar pagination')
            pages.add(next_page); params['pageToken'] = next_page
        truncated = bool(next_page)
        events.sort(key=lambda event: event.starts_at.astimezone(timezone.utc))
        return CalendarSnapshot('ready', events, now, truncated)

    async def snapshot(self, range='today', now=None, force=False):
        if not isinstance(range, str) or range not in {'today', 'next'}: raise ValueError('Unsupported read-only calendar range')
        current = now or self.clock()
        if current.tzinfo is None: raise ValueError('Calendar clock requires timezone')
        current = current.astimezone(timezone.utc)
        if self.provider == 'none': return CalendarSnapshot('unconnected', message='Connect Google Calendar on the Mac to show real events.')
        if self.provider == 'sample':
            events = self.today(current) if range == 'today' else [self.next_event()]
            return CalendarSnapshot('sample', [e for e in events if e], current, message='Sample data; no calendar account connected.')
        key = (range, current.astimezone(self.zone).date())
        async with self.lock:
            cached = self.cache.get(key)
            if cached and cached.status == 'ready' and not force and timedelta(0) <= current - cached.fetched_at < timedelta(seconds=60): return cached
            try:
                if current < self.retry_until and not force:
                    if self.last_status == 'revoked': return CalendarSnapshot('revoked', message='Reconnect Google Calendar on the Mac.')
                    if self.last_status == 'unconnected': return CalendarSnapshot('unconnected', message='Google Calendar is not connected safely. Set up the account on the Mac.')
                    raise OAuthUnavailable('error')
                result = await asyncio.wait_for(self.fetch(range, current), 20)
                self.cache = {k:v for k,v in self.cache.items() if k[1] == key[1]}
                self.cache[key] = result
                self.last_status = 'ready'
                self.retry_until = datetime.min.replace(tzinfo=timezone.utc)
                return result
            except (OAuthUnavailable, httpx.HTTPError, ValueError, KeyError, TypeError, TimeoutError) as error:
                reason = error.reason if isinstance(error, OAuthUnavailable) else 'error'
                if reason in {'revoked', 'unconnected', 'unsafe_storage', 'invalid_client'}:
                    self.cache.clear();self.actions.clear();self.access_token = None
                    self.last_status = 'revoked' if reason == 'revoked' else 'unconnected'
                    self.retry_until = current + timedelta(seconds=15)
                    return CalendarSnapshot(self.last_status, message='Reconnect Google Calendar on the Mac.' if reason == 'revoked' else 'Google Calendar is not connected safely. Set up the account on the Mac.')
                self.retry_until = current + timedelta(seconds=15)
                if cached and timedelta(0) <= current - cached.fetched_at <= timedelta(minutes=10):
                    self.last_status = 'stale'
                    stale = CalendarSnapshot('stale', cached.events, cached.fetched_at, cached.truncated, 'Cached events; live refresh failed.')
                    self.cache[key] = stale
                    return stale
                self.last_status = 'error'
                return CalendarSnapshot('error', message='Calendar is temporarily unavailable. Try refresh shortly.')

    async def scene(self, action, device_id, now=None, snapshot=None):
        current = now or self.clock()
        if current.tzinfo is None: raise ValueError('Calendar clock requires timezone')
        current = current.astimezone(timezone.utc)
        if action.startswith('event:'):
            fresh = await self.snapshot('today', current)
            entry = self.actions.get(device_id, {}).get(action)
            if not entry or current >= entry[1] or fresh.status not in {'ready', 'sample', 'stale'}:
                return Surface(kind='message', title='Event unavailable', subtitle='Refresh the calendar; that event card is no longer current.', actions=[button('calendar_refresh','Refresh'),button('dismiss','Dismiss')], expires_ms=30000)
            event, _, stale = entry
            match = next((e for e in fresh.events if e.id == event.id and e.starts_at.astimezone(timezone.utc) == event.starts_at.astimezone(timezone.utc)), None)
            if match is None:
                return Surface(kind='message', title='Event unavailable', subtitle='That event changed. Refresh the calendar.', actions=[button('calendar_refresh','Refresh'),button('dismiss','Dismiss')], expires_ms=30000)
            return event_scene(match, self.zone, fresh.status)
        snapshot = snapshot or await self.snapshot('next' if action == 'calendar_next' else 'today', current, force=action == 'calendar_refresh')
        if snapshot.status not in {'ready', 'sample', 'stale'}:
            return Surface(kind='message', title={'revoked':'Calendar access revoked','unconnected':'Calendar not connected','error':'Calendar unavailable'}[snapshot.status], subtitle=snapshot.message,
                actions=[button('calendar_refresh','Refresh'),button('dismiss','Dismiss')], expires_ms=30000)
        events = snapshot.events
        if action == 'calendar_next':
            chosen = next_event(snapshot, current)
            selected = [chosen] if chosen else []
            if selected: return event_scene(selected[0], self.zone, snapshot.status)
            return Surface(kind='message',title='No upcoming event',subtitle='Results incomplete; no event retrieved before the limit.' if snapshot.truncated else 'No events found in the next seven days.',actions=[button('calendar','Today'),button('dismiss','Dismiss')],expires_ms=30000)
        page = int(action.split(':',1)[1]) if ':' in action else 0
        if page > max(0, (len(events)-1)//4):
            return Surface(kind='message',title='Calendar changed',subtitle='Refresh to see the current event list.',actions=[button('calendar_refresh','Refresh'),button('dismiss','Dismiss')],expires_ms=30000)
        selected = events[page*4:page*4+4]
        entries = self.actions.setdefault(device_id,{})
        entries = {key:value for key,value in entries.items() if current < value[1]}
        self.actions[device_id] = entries
        items = []
        for event in selected:
            identifier = 'event:' + hashlib.sha256((self.calendar_id+event.id+event.starts_at.isoformat()).encode()).hexdigest()[:16]
            entries[identifier] = (event,current+timedelta(minutes=2),snapshot.status)
            items.append({'time':('All day' if (event.all_day_start or event.starts_at.date()) == current.astimezone(self.zone).date() else (event.all_day_start or event.starts_at.date()).strftime('%b %d')+' all day') if event.all_day else event.starts_at.astimezone(self.zone).strftime('%H:%M'), 'label':event.title[:80], 'action':identifier})
        while len(entries)>64: entries.pop(next(iter(entries)))
        while len(self.actions)>32:self.actions.pop(next(iter(self.actions)))
        actions=[]
        if page:actions.append(button(f'calendar:{page-1}','Previous'))
        if (page+1)*4<len(events):actions.append(button(f'calendar:{page+1}','Next'))
        actions.extend([button('calendar_refresh','Refresh'),button('dismiss','Dismiss')])
        label = 'Sample calendar' if snapshot.status=='sample' else ('Cached calendar · stale' if snapshot.status=='stale' else 'Google Calendar · read only')
        subtitle = f'{label} · {len(events)} events · {self.zone.key}' if events else (f'{label} · No events retrieved' if snapshot.truncated else f'{label} · No events today')
        if snapshot.truncated:subtitle += ' · Results limited'
        subtitle = current.astimezone(self.zone).strftime('%A, %B %-d') + '\n' + subtitle
        return Surface(kind='timeline',title='Today' if snapshot.status!='sample' else 'Calendar preview',subtitle=subtitle,items=items,actions=actions,expires_ms=30000)

    async def close(self):
        if self.owns_client and self.client is not None:
            await self.client.aclose()
            self.client = None


def button(id,label): return SurfaceAction(id=id,label=label)
def event_scene(event, zone, state):
    start,end=event.starts_at.astimezone(zone),event.ends_at.astimezone(zone)
    when = (event.all_day_start or start.date()).strftime('%b %d') if event.all_day else start.strftime('%a %b %d · %H:%M')
    until=((event.all_day_end or end.date())-timedelta(days=1)).strftime('%b %d') if event.all_day else end.strftime('%b %d · %H:%M')
    items=[{'time':'When','label':('All day · ' if event.all_day else '')+when},{'time':'Until','label':until}]
    if event.location:items.append({'time':'Where','label':event.location[:80]})
    if event.people:items.append({'time':'With','label':', '.join(event.people)[:80]})
    elif event.recurring:items.append({'time':'Series','label':'Recurring event instance'})
    label='Sample event' if state=='sample' else ('Cached event · stale' if state=='stale' else 'Google Calendar · read only')
    return Surface(kind='artifact',title=event.title[:80],subtitle=label+' · '+zone.key,items=items,
        actions=[button('calendar','Today'),button('calendar_refresh','Refresh'),button('dismiss','Dismiss')],expires_ms=30000)



def next_event(snapshot, current):
    if snapshot.status == 'sample': return snapshot.events[0] if snapshot.events else None
    upcoming = [e for e in snapshot.events if e.ends_at.astimezone(timezone.utc) > current.astimezone(timezone.utc)]
    ongoing = [e for e in upcoming if not e.all_day and e.starts_at.astimezone(timezone.utc) <= current.astimezone(timezone.utc)]
    future = [e for e in upcoming if e.starts_at.astimezone(timezone.utc) > current.astimezone(timezone.utc)]
    return (ongoing or future or upcoming)[0] if upcoming else None


def calendar_answer(payload, zone):
    state = payload.get('status')
    if state not in {'ready', 'sample', 'stale'}: return payload.get('message') or 'Calendar is unavailable.'
    prefix = 'Sample calendar: ' if state == 'sample' else ('From cached calendar data: ' if state == 'stale' else '')
    events = payload.get('events', [])
    if payload.get('range') == 'next':
        if not events: return prefix + 'No upcoming event was found in the next seven days.'
        event = events[0]
        start = datetime.fromisoformat(event['starts_at']).astimezone(ZoneInfo(zone))
        return prefix + f"Your next event is {event['title'][:80]} " + (f"on {event.get('all_day_start') or start.date().isoformat()}, all day." if event.get('all_day') else f"on {start.strftime('%a at %H:%M')}.")
    if not events: return prefix + ('Calendar results are incomplete; no events were retrieved before the limit.' if payload.get('truncated') else 'No events today.')
    suffix = ' The list is limited; more events may exist.' if payload.get('truncated') else ''
    return prefix + f"Found {payload.get('event_count', len(events))} calendar events today." + suffix
