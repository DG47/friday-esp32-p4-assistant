#!/usr/bin/env python3
"""Prepared serial competence gate. Dry-run by default; never pulls or configures a daemon."""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import re
from pathlib import Path
import sys
import subprocess
import tempfile
import time
from unittest.mock import patch

PROMPTS = [
    'Read my synthetic fixture calendar and check weather for the next dinner event at its listed ZIP.',
    'Correction for that same dinner: use ZIP 10010 for Weather. Keep the actual Calendar location unchanged.',
    'Check Weather for that same dinner again using my correction. Tell me which ZIP you used.',
]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


async def evaluate(args):
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root/'backend'), str(root/'backend/tests')]
    from app.agent import FridayAgent
    from app.config import Settings, WeatherLocation
    from app.db import ReminderStore
    from app.models import CalendarEvent
    from app.providers.local_policy import LocalModelUnavailable
    from app.providers.ollama_provider import OllamaProvider
    from app.tools.google_calendar import CalendarSnapshot
    import test_conversation_scenario as fixtures

    report = {'model': args.model, 'status': 'failed', 'invocation_mode': 'real_local_model_test', 'model_response_observed': False,
              'inference_attempt_outcome': 'not_started',
              'real_device_or_voice_acceptance': False, 'concurrency': 1, 'prompts': PROMPTS,
              'model_downloads': False, 'live_backend_changes': False, 'turns': [], 'wire_rounds': []}
    report['host_memory_before'] = subprocess.check_output(['vm_stat'], text=True)
    args.evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='friday-local-competence-') as temp:
        config = Settings(_env_file=None, assistant_env='test', assistant_provider='local-only',
                          ollama_base_url=args.base_url, ollama_model=args.model,
                          assistant_user_name='Synthetic fixture user', assistant_timezone='UTC',
                          assistant_database_path=Path(temp)/'original-synthetic.db',
                          openai_api_key='', anthropic_api_key='', assistant_memory_enabled=False,
                          calendar_provider='sample', weather_provider='none', music_provider='none',
                          tts_provider='none', stt_provider='none',
                          weather_locations=[WeatherLocation(label='07306', latitude=1, longitude=1),
                                             WeatherLocation(label='10010', latitude=2, longitude=2)])
        agent = FridayAgent(config, ReminderStore(config.assistant_database_path))
        initial = datetime.now(timezone.utc)
        event = CalendarEvent(id='original-synthetic-dinner', title='Original synthetic dinner',
                              starts_at=initial+timedelta(hours=1), ends_at=initial+timedelta(hours=2),
                              location='Original synthetic place (07306)')
        report['calendar_reads'] = []
        snapshot_error = None
        async def calendar_snapshot(range='today', force=False):
            report['calendar_reads'].append({'range':range,'force':force,'event_id':event.id if event else None})
            if snapshot_error:
                raise snapshot_error
            return CalendarSnapshot('sample', [event.model_copy(deep=True)] if event else [], agent.calendar.clock())
        agent.calendar.snapshot = calendar_snapshot
        class CurrentSyntheticWeather(fixtures.SyntheticWeather):
            async def current(self, location='both'):
                # Fresh synthetic timestamps; actual context expiry clock remains active.
                fixtures.NOW = datetime.now(timezone.utc)
                return await super().current(location)
        agent.weather = CurrentSyntheticWeather(config.weather_locations)
        class TracedProvider(OllamaProvider):
            async def complete(self, messages, instructions, tools):
                wire = self._messages(messages, instructions)
                call_ids = {call['id'] for m in wire for call in m.get('tool_calls', [])}
                for m in wire:
                    if m['role'] == 'tool':
                        require(m['tool_call_id'] in call_ids and m['tool_name'] in {'get_calendar', 'get_weather'}, m)
                require(sum(len(str(m)) for m in wire) < 60000, 'Synthetic context exceeded bounded input budget')
                if len(report['turns']) == 2:
                    require(not any('Correction for that same dinner' in m.get('content', '') for m in wire), 'Correction survived in history; cutoff test invalid')
                start = time.monotonic()
                report['inference_attempt_outcome'] = 'unverified'  # A failure/timeout is not proof of no daemon computation.
                import httpx
                original_post = httpx.AsyncClient.post
                async def bounded_post(client, url, **kwargs):
                    if url.endswith('/api/chat'):
                        kwargs['json']['options']['num_ctx'] = 8192
                    response = await original_post(client, url, **kwargs)
                    if url.endswith('/api/chat') and response.status_code == 200:
                        raw = response.json()
                        report.setdefault('daemon_timings', []).append({key: raw.get(key) for key in ['total_duration', 'load_duration', 'prompt_eval_count', 'prompt_eval_duration', 'eval_count', 'eval_duration', 'done_reason']})
                    return response
                with patch.object(httpx.AsyncClient, 'post', bounded_post):
                    result = await super().complete(messages, instructions, tools)
                report['model_response_observed'] = True
                report['inference_attempt_outcome'] = 'completed_response_received'
                report['wire_rounds'].append({'seconds': round(time.monotonic()-start, 3), 'messages': deepcopy(wire),
                                              'response_text': result.text,
                                              'tool_calls': [vars(c) for c in result.tool_calls]})
                # Log the observed choice, then block every mutation/account/media tool.
                if not all(c.name in {'get_calendar', 'get_weather'} for c in result.tool_calls):
                    raise RuntimeError('Unexpected tool selection; no fixture tool executed')
                return result
        provider = TracedProvider(config.ollama_base_url, config.ollama_model, local_only=True)
        # Only router selection is intercepted for tracing; production adapter and agent run unchanged.
        with patch('app.providers.router.OpenAIProvider', side_effect=AssertionError('Paid provider prohibited')) as openai, \
             patch('app.providers.router.AnthropicProvider', side_effect=AssertionError('Paid provider prohibited')) as anthropic:
            try:
                await provider.ensure_available()
                missing = OllamaProvider(config.ollama_base_url, 'original-friday-synthetic-missing-model:local', local_only=True)
                try:
                    await missing.ensure_available()
                    raise AssertionError('Synthetic missing-model check unexpectedly succeeded')
                except LocalModelUnavailable as error:
                    require(error.reason == 'local_model_missing', str(error))
                    report['missing_model'] = error.reason
                with patch.object(agent.providers, 'select', return_value=provider):
                    for number, prompt in enumerate(PROMPTS):
                        if number == 2:
                            for filler in range(12):
                                agent.store.append_exchange('synthetic-device', f'synthetic-filler-{filler}', f'Original neutral synthetic exchange {filler}', 'Acknowledged synthetic exchange.')
                            report['history_cutoff'] = {'synthetic_filler_request_groups': 12, 'actual_filler_inference': False}
                        reads_before = len(agent.weather.calls)
                        start = time.monotonic()
                        reply = await asyncio.wait_for(agent.handle('synthetic-device', prompt, request_id=f'original-local-turn-{number}'), timeout=300)
                        report['turns'].append({'seconds': round(time.monotonic()-start, 3),
                                                'reply': reply.model_dump(mode='json'),
                                                'working_context': agent.working_context.provider_context('synthetic-device')})
                        state = agent.working_context.state('synthetic-device')
                        require(state.focus in state.events, 'Event context expired or missing; not a passing correction')
                        require(state.events[state.focus].location == event.location, 'Calendar facts changed')
                        expected = '07306' if number == 0 else '10010'
                        require(state.location == expected, f'Weather binding is {state.location}, expected {expected}')
                        if not (len(agent.weather.calls) > reads_before and agent.weather.calls[-1] == expected):
                            report['model_acceptance_error'] = 'A fresh synthetic Weather read was not observed; model prose is not accepted'
                require(agent.weather.calls[0] == '07306' and agent.weather.calls[-1] == '10010', 'Local model acceptance condition failed')
                require('10010' in report['turns'][-1]['reply']['text'], 'Final correction was not stated in the reply')
                report['clock_cases'] = []
                cases = [
                    ('changed-time', 'UTC', '2026-10-12T23:40:00+00:00', '2026-10-13T00:10:00+00:00', '2026-10-12 23:40 UTC (UTC+00:00)', '2026-10-13 00:10 UTC (UTC+00:00)', 30),
                    ('duration-correction', 'UTC', '2026-10-12T23:40:00+00:00', '2026-10-13T01:10:00+00:00', '2026-10-12 23:40 UTC (UTC+00:00)', '2026-10-13 01:10 UTC (UTC+00:00)', 90),
                    ('spring-DST', 'America/New_York', '2026-03-08T06:30:00+00:00', '2026-03-08T07:30:00+00:00', '2026-03-08 01:30 EST (UTC-05:00)', '2026-03-08 03:30 EDT (UTC-04:00)', 60),
                    ('fall-DST', 'America/New_York', '2026-11-01T05:30:00+00:00', '2026-11-01T06:30:00+00:00', '2026-11-01 01:30 EDT (UTC-04:00)', '2026-11-01 01:30 EST (UTC-05:00)', 60),
                ]
                with patch.object(agent.providers, 'select', return_value=provider):
                    for name, zone, begin, end, expected_start, expected_end, duration in cases:
                        config.assistant_timezone = zone
                        event.starts_at = datetime.fromisoformat(begin)
                        event.ends_at = datetime.fromisoformat(end)
                        agent.calendar.clock = lambda: event.starts_at-timedelta(hours=1)
                        prompt = 'Read Calendar again. What are the next appointment start and end times, with date, timezone and offset, and its elapsed duration in minutes?'
                        reads_before = len(report['calendar_reads'])
                        rounds_before = len(report['wire_rounds'])
                        start = time.monotonic()
                        reply = await asyncio.wait_for(agent.handle('synthetic-device', prompt, request_id='original-clock-'+name), timeout=300)
                        fresh_reads = sum(read['force'] for read in report['calendar_reads'][reads_before:])
                        duration_present = bool(re.search(r'(?<![\d:])'+str(duration)+r'(?:\.0)?\s+minutes\b', reply.text))
                        passed = fresh_reads > 0 and expected_start in reply.text and expected_end in reply.text and duration_present
                        report['clock_cases'].append({'name':name,'zone':zone,'starts_at':begin,'ends_at':end,'expected_start':expected_start,'expected_end':expected_end,'expected_minutes':duration,'reply':reply.text,'seconds':round(time.monotonic()-start,3),'passed':passed,'fresh_calendar_reads':fresh_reads,'explicit_duration_present':duration_present,'reply_provider':reply.provider,'deterministic_route':reply.provider == 'local'})
                report['freshness_cases'] = []
                event.starts_at = datetime.fromisoformat('2026-11-01T07:30:00+00:00')
                event.ends_at = datetime.fromisoformat('2026-11-01T09:00:00+00:00')
                with patch.object(agent.providers, 'select', return_value=provider):
                    moved = await agent.handle('synthetic-device', 'What are my next appointment times and duration?', request_id='original-moved')
                    require('2026-11-01 02:30 EST (UTC-05:00)' in moved.text and '2026-11-01 04:00 EST (UTC-05:00)' in moved.text and '90 minutes' in moved.text, 'Moved event was not refreshed')
                    report['freshness_cases'].append({'name':'externally-moved','reply':moved.text,'deterministic_route':moved.provider=='local'})
                    deleted_clock = agent.calendar.clock()
                    agent.calendar.clock = lambda: deleted_clock
                    event = None
                    deleted = await agent.handle('synthetic-device', 'What are my next appointment times?', request_id='original-deleted')
                    require('No upcoming event' in deleted.text, 'Deleted event remained current')
                    report['freshness_cases'].append({'name':'externally-deleted','reply':deleted.text,'deterministic_route':deleted.provider=='local'})
                    snapshot_error = asyncio.TimeoutError()
                    unavailable = await agent.handle('synthetic-device', 'What are my next appointment times?', request_id='original-timeout')
                    require('freshness is unavailable' in unavailable.text, 'Refresh timeout did not fail closed')
                    report['freshness_cases'].append({'name':'provider-timeout','reply':unavailable.text,'deterministic_route':unavailable.provider=='local'})
                require(all(c['passed'] for c in report['clock_cases']), 'Exact clock grounding acceptance failed; see captured cases')
                require(not report.get('model_acceptance_error'), report.get('model_acceptance_error'))
                report['prose_semantic_review'] = 'requires reviewer assessment of captured original synthetic transcript'
                require(any(m['role']=='tool' for round in report['wire_rounds'] for m in round['messages']), 'Local model acceptance condition failed')
                openai.assert_not_called(); anthropic.assert_not_called()
                report['weather_reads'] = agent.weather.calls
                report['paid_provider_constructions'] = {'openai': 0, 'anthropic': 0}
                report['status'] = 'passed'
            except Exception as error:
                report['error'] = str(error)
            finally:
                await agent.calendar.close()
    report['host_memory_after'] = subprocess.check_output(['vm_stat'], text=True)
    report['latency_measurement'] = 'Nonstreaming HTTP response/completion timing; daemon durations captured. Token-first latency not measured.'
    (args.evidence/'real-local-model-report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'status': report['status'], 'report': str(args.evidence/'real-local-model-report.json')}))
    if report['status'] != 'passed':
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='qwen3.6:35b')
    parser.add_argument('--base-url', default='http://127.0.0.1:11434')
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--run-inference', action='store_true', help='Use only after recovery-owner clearance and local model provenance/cloud-disable checks')
    args = parser.parse_args()
    if not args.run_inference:
        print(json.dumps({'mode': 'preparation_only', 'inference': False, 'downloads': False,
                          'prompts': PROMPTS, 'concurrency': 1,
                          'gates': ['recovery-owner clearance', 'verified official local model already installed',
                                    'daemon cloud.disabled=true', 'local tool-capable model metadata'],
                          'checks': ['three-turn correction', 'native tool ID/result roundtrip', 'missing-model failure', 'zero paid fallback']}))
        return
    if args.evidence is None:
        parser.error('--evidence is required for real inference')
    asyncio.run(evaluate(args))


if __name__ == '__main__':
    main()
