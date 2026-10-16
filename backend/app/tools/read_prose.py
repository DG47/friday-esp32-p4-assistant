"""Observed read facts for final prose; model wording is not tool evidence."""
import json
from .schedule_freshness import schedule_answer
from .weather import weather_display_name, weather_condition


def observed_read_prose(name, outcome):
    if name not in {'get_calendar', 'get_weather'}:
        return None
    payload = json.loads(outcome.content)
    if name == 'get_calendar':
        if not payload.get('ok'):
            return payload.get('message') or 'Calendar is unavailable; no current schedule was verified.'
        return schedule_answer(payload)
    if not payload.get('ok'):
        return 'Weather: ' + (payload.get('message') or payload.get('error') or 'Weather is unavailable; no current reading was verified.')
    readings = payload.get('locations')
    if readings is None:
        # Legacy single-point tool already supplies an observed, unit-qualified summary.
        return 'Weather: ' + (outcome.surface.subtitle if outcome.surface else 'Reading unavailable.')
    parts = []
    for reading in readings:
        label = weather_display_name(reading['location'])
        if not reading.get('ok'):
            parts.append(f'{label}: weather unavailable.')
            continue
        parts.append(f"{label}: {reading['temperature_2m']:g} degrees Fahrenheit; {weather_condition(reading.get('weather_code'))}.")
        forecast = reading.get('rain_forecast')
        facts = forecast.get('clock_facts') if forecast else None
        if facts:
            parts.append(f"Rain / snow chance {forecast['probability']:g}% from {facts['start']['label']} to {facts['end']['label']}.")
        else:
            parts.append('Rain / snow chance unavailable for the requested interval.')
    return 'Weather (Open-Meteo): ' + ' '.join(parts)
