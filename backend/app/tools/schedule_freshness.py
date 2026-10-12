"""Bounded English scheduling reads; model prose is not the source of current clocks."""
import re


def current_schedule_query(text):
    words = text.lower()
    historical = re.search(r'\b(historical|previously|earlier|used to|last time|old schedule|originally|yesterday)\b', words)
    if historical:
        return False
    # Weather remains its separate read path. This gate covers calendar-clock queries,
    # not every arbitrary planning discussion or possible English paraphrase.
    if re.search(r'\b(weather|rain|temperature)\b', words):
        return False
    subject = re.search(r'\b(calendar|appointment|meeting|event|schedule|dinner)\b', words)
    clock = re.search(r'\b(when|time|times|start|starts|end|ends|duration|minutes|next|moved|deleted|reread|again)\b', words)
    return bool(subject and clock)


def schedule_answer(payload):
    if not payload.get('current_read_verified') or not payload.get('ok'):
        return payload.get('message') or 'Current Calendar freshness is unavailable. I cannot confirm the schedule; please try again.'
    prefix = 'Sample calendar, no account connected: ' if payload['status'] == 'sample' else 'Current Calendar read: '
    events = payload.get('events', [])
    if not events:
        return prefix + ('Results are incomplete; I cannot confirm that no event exists.' if payload.get('truncated') else 'No upcoming event was found in the returned calendar range.')
    parts = []
    for event in events[:3]:
        facts = event.get('clock_facts')
        if not facts:
            return 'Calendar clock facts are unavailable. I cannot confirm appointment times.'
        title = event.get('title', 'Event')[:80]
        if facts.get('all_day'):
            parts.append(f"{title}: all day from {facts['start_date']} to {facts['end_date_exclusive']} (exclusive end date), {facts['zone']}.")
        else:
            minutes = facts['duration_minutes']
            parts.append(f"{title}: {facts['start']['label']} to {facts['end']['label']}; elapsed duration {minutes:g} minutes.")
    suffix = ' More events are available on the display.' if len(events)>3 or payload.get('truncated') else ''
    return prefix + ' '.join(parts) + suffix


def next_schedule_query(text):
    # Supported referent is explicitly the next event; named/ordinal/date selectors
    # require clarification rather than silently substituting the earliest event.
    if re.search(r'[^a-zA-Z\s.,?!]', text):
        return False
    words = re.findall(r"[a-z]+", text.lower())
    allowed = set("read calendar again what are is my the next appointment appointments meeting event dinner start starts end ends time times when does and its elapsed duration in minutes with date timezone offset please tell me of how long will take reread current show a on".split())
    return bool(re.search(r'\bnext\s+(appointment|event)\b', text.lower()) and all(word in allowed for word in words))
