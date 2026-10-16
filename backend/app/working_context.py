"""Leased event/place facts with bounded opaque restart intent; never durable facts."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import re
import secrets
from .context_checkpoint import ContextCheckpointStore
from zoneinfo import ZoneInfo
from .models import CalendarEvent, Surface, SurfaceAction
from .tools.google_calendar import event_scene


def digest(*parts):
    return hashlib.sha256('\x1f'.join(map(str, parts)).encode()).hexdigest()[:24]


@dataclass
class Workspace:
    owner: str = ''
    token_epoch: str = field(default_factory=lambda: secrets.token_hex(16))
    restored: bool = False
    calendar_range: str = 'today'
    revision: int = 0
    events: dict = field(default_factory=dict)
    focus: str | None = None
    weather: Surface | None = None
    weather_event_ref: str | None = None
    location: str | None = None
    weather_source_fingerprint: str | None = None
    weather_until: object = None
    event_until: object = None
    tokens: dict = field(default_factory=dict)


class WorkingContext:
    def __init__(self, settings, clock):
        self.settings, self.clock = settings, clock
        self.devices = {}
        self.checkpoints = ContextCheckpointStore(settings.assistant_database_path, clock)

    def state(self, device):
        if device not in self.devices:
            if len(self.devices) >= 32: self.devices.pop(next(iter(self.devices)))
            state = Workspace()
            checkpoint = self.checkpoints.load(device)
            if checkpoint:
                state.focus, state.weather_event_ref, state.location, state.weather_source_fingerprint, state.calendar_range = checkpoint
                if state.location not in {place.label for place in self.settings.weather_locations}:
                    state.weather_event_ref = state.location = state.weather_source_fingerprint = None
                state.restored = True
            self.devices[device] = state
        state = self.devices[device]
        if state.event_until and self.clock() >= state.event_until:
            state.events.clear()  # erase private facts, retain public Weather identity only
        return state

    def begin(self, device, request):
        self.state(device).owner = request

    def current(self, device, request):
        return self.state(device).owner in {'', request}

    def dismiss(self, device):
        state = self.state(device)
        self.checkpoints.delete(device)
        self.devices[device] = Workspace(owner='dismissed', revision=state.revision+1)

    def token(self, device, ref, operation):
        state = self.state(device)
        token = 'context:' + digest(device, state.token_epoch, state.revision, ref, operation)
        state.tokens[token] = (ref, operation)
        return token

    def observe_calendar(self, device, snapshot, surface, payload):
        state = self.state(device)
        allowed = {(entry["id"],datetime.fromisoformat(entry["starts_at"])) for entry in payload["events"]}
        events = {digest(device, event.id, event.starts_at.isoformat()): CalendarEvent(id=event.id[:256],title=event.title[:160],starts_at=event.starts_at,ends_at=event.ends_at,location=(event.location or "")[:160],people=[name[:48] for name in event.people[:8]],recurring=event.recurring,all_day=event.all_day,all_day_start=event.all_day_start,all_day_end=event.all_day_end)
                  for event in snapshot.events[:32] if (event.id,event.starts_at) in allowed}
        state.events = events
        state.calendar_range = payload.get('range', 'today')
        state.revision += 1
        state.tokens.clear()
        if state.focus not in events:
            # A removed/moved resumed referent must not silently select another event.
            state.focus = None if state.restored else next(iter(events)) if len(events) == 1 else None
        state.restored = False
        self.checkpoints.save(device, state)
        state.event_until = self.clock() + timedelta(milliseconds=surface.expires_ms or 30000)
        payload['context_refs'] = [{'event_ref': ref, 'event_id': event.id} for ref, event in events.items()]
        for item in surface.items:
            # Match the calendar's existing instance action, not titles or positions.
            for ref, event in events.items():
                old = 'event:' + hashlib.sha256((self.settings.calendar_google_id+event.id+event.starts_at.isoformat()).encode()).hexdigest()[:16]
                if item.get('action') == old:
                    item['object_id'] = ref
                    item['action'] = self.token(device, ref, 'event')
        return payload

    def provider_context(self, device):
        state = self.state(device)
        if self.settings.calendar_provider == 'google' and not self.settings.calendar_model_context:
            return {'status':'local_only'}
        _, focused_location, error = self.resolve(device)
        return {'revision':state.revision, 'focused_event_ref':state.focus if state.focus in state.events else None,
                'event_refs':list(state.events), 'weather_location':state.location,
                'weather_event_ref':state.weather_event_ref,
                'focused_event_weather_location':focused_location if not error else None,
                'weather_status':('active' if state.weather_until and self.clock() < state.weather_until else 'expired') if state.weather else 'not_read',
                'status':'active' if state.events else 'no_current_event'}

    def resolve(self, device, event_ref=None, location='context'):
        state = self.state(device)
        if not state.event_until or self.clock() >= state.event_until:
            return None, None, 'The event context expired. Read Calendar again before using that reference.'
        ref = event_ref if event_ref is not None else state.focus
        if not isinstance(ref,str) or ref not in state.events:
            return None, None, 'Which event? Select one event on the device or name its reference.'
        event = state.events[ref]
        if location == 'context' and state.weather_event_ref == ref and state.weather_source_fingerprint == digest(device, ref, event.location) and state.location:
            # A successful explicit correction is authoritative for this event
            # until Calendar changes its source location. Touch and speech agree.
            location = state.location
        if location == 'context':
            # An exact configured ZIP in the actual event location is the only
            # automatic place binding. Never infer home/work or geocode a title.
            matches = [place.label for place in self.settings.weather_locations
                       if re.search(r'(?<!\d)'+re.escape(place.label)+r'(?!\d)', event.location or '')]
            if len(matches) != 1 or not re.search(re.escape(matches[0])+r'(?=\s*$|\s*\))',event.location or ''):
                return None, None, 'Which configured ZIP? This event has no unambiguous approved location.'
            location = matches[0]
        if not isinstance(location,str) or location not in {place.label for place in self.settings.weather_locations}:
            return None, None, 'Choose an approved configured ZIP.'
        return ref, location, None

    def attach_weather(self, device, ref, location, weather):
        state = self.state(device)
        state.focus = ref
        previous_source = state.weather_source_fingerprint if state.weather_event_ref == ref else None
        state.weather_event_ref = ref
        state.location = location
        event = state.events.get(ref)
        # Retain only an opaque comparison value, never an expired private address.
        # A public-only refresh after the event lease keeps its established binding.
        state.weather_source_fingerprint = digest(device, ref, event.location) if event else previous_source
        state.weather = weather.model_copy(deep=True)
        state.weather_until = weather.deadline_at or (self.clock()+timedelta(milliseconds=weather.weather_valid_ms or 1))
        state.revision += 1
        state.tokens.clear()
        self.checkpoints.save(device, state)
        return self.surface(device)

    def surface(self, device, view='both'):
        state = self.state(device)
        event = state.events.get(state.focus)
        event_valid = bool(event and state.event_until and self.clock() < state.event_until)
        weather_valid = bool(state.weather and state.weather_until and self.clock() < state.weather_until)
        items = []
        if event_valid:
            items = [{'time': 'Event', 'label': event.title[:160], 'object_id': state.focus},
                     {'time': event.starts_at.astimezone(ZoneInfo(self.settings.assistant_timezone)).strftime('%H:%M'),
                      'label': (event.location or 'Location not provided')[:160], 'object_id': state.focus}]
        else:
            items = [{'time': 'Event', 'label': 'Event details expired; read Calendar again.'}, {'time': '', 'label': ''}]
        if state.weather:
            first, second = (state.weather.items+[{}, {}])[:2]
            items += [{'time': 'Weather / '+state.location, 'label': (first.get('label','')+' / '+first.get('condition','')).strip(' /') if weather_valid else 'Reading expired; refresh Weather.', 'object_id': digest(device,'weather',state.location)},
                      {'time': 'Next hour', 'label': second.get('label','') if weather_valid else 'Forecast unavailable until refreshed.', 'object_id': digest(device,'weather',state.location)}]
        else:
            items += [{'time':'Weather','label':'Use the event location or choose a configured ZIP.'}]
        weather_summary = ('Weather '+state.location+' / '+' / '.join(item.get('label','') for item in items[2:]))[:200] if state.weather else 'Weather not read yet.'
        if view == 'event' and event_valid:
            items = event_scene(event,ZoneInfo(self.settings.assistant_timezone),'sample' if self.settings.calendar_provider=='sample' else 'ready').items
        actions = []
        if event_valid:
            actions += [SurfaceAction(id=self.token(device,state.focus,'event'),label='Event'),
                        SurfaceAction(id=self.token(device,state.focus,'weather'),label='Weather')]
        if not event_valid and state.weather:
            actions += [SurfaceAction(id=self.token(device,state.focus,'weather'),label='Weather')]
        actions += [SurfaceAction(id='calendar',label='Calendar'),SurfaceAction(id='dismiss',label='Dismiss')]
        return Surface(kind='artifact',title=event.title[:80] if view=='event' and event_valid else 'Your plan',subtitle=('Event as listed / Weather for '+state.location+'.' if state.location else 'Choose Weather to check the event location.'),
            items=items,actions=actions,context={
                'event_ref':state.focus or '', 'weather_event_ref':state.weather_event_ref or '', 'weather_ref':digest(device,'weather',state.location) if state.location else '',
                'revision':state.revision,'view':view,'weather_summary':weather_summary,
                'weather_location':state.location or '',
                'weather_temperature':(state.weather.items[0].get('label','')[:48] if weather_valid and state.weather.items else ''),
                'weather_condition':(state.weather.items[0].get('condition','')[:96] if weather_valid and state.weather.items else ''),
                'weather_outlook':(state.weather.items[1].get('label','')[:192] if weather_valid and len(state.weather.items)>1 else ''),
                'event_deadline_at':state.event_until.isoformat() if state.event_until else None,
                'weather_deadline_at':state.weather_until.isoformat() if state.weather_until else None,
                'event_valid_ms':max(0,int((state.event_until-self.clock()).total_seconds()*1000)) if event_valid else 0,
                'weather_valid_ms':max(0,int((state.weather_until-self.clock()).total_seconds()*1000)) if weather_valid else 0})

    def touch(self, device, action):
        state = self.state(device)
        selected = state.tokens.get(action)
        if not selected: raise ValueError('That context changed or was dismissed. Read Calendar again.')
        ref, operation = selected
        if operation == 'weather' and state.weather:
            return ref, operation, self.surface(device, operation)
        if not state.event_until or self.clock() >= state.event_until or ref not in state.events:
            raise ValueError('Event details expired. Read Calendar again.')
        if state.focus != ref:
            state.focus = ref
            state.revision += 1; state.tokens.clear()
            self.checkpoints.save(device, state)
        # Repeated touch changes presentation only, never object identity, lease,
        # or source generation. Weather reads happen only for a new relation or an explicitly refreshed stale reading.
        return ref, operation, self.surface(device, operation)
