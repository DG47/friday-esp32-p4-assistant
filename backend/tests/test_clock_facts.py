import unittest
from datetime import datetime
from app.tools.clock_facts import clock_point, interval_facts, event_clock_facts

class ClockFactsTests(unittest.TestCase):
    def test_original_conversion(self):
        self.assertEqual(clock_point('2026-10-12T18:55:00+00:00', 'America/New_York')['label'], '2026-10-12 14:55 EDT (UTC-04:00)')
        self.assertEqual(clock_point('2026-10-12T18:55:00+00:00', 'UTC')['label'], '2026-10-12 18:55 UTC (UTC+00:00)')

    def test_changed_time_duration_and_date_boundary(self):
        facts = interval_facts('2026-10-12T23:40:00Z','2026-10-13T01:10:00Z','UTC')
        self.assertEqual(facts['start']['label'], '2026-10-12 23:40 UTC (UTC+00:00)')
        self.assertEqual(facts['end']['label'], '2026-10-13 01:10 UTC (UTC+00:00)')
        self.assertEqual(facts['duration_minutes'], 90)
        self.assertEqual(interval_facts('2026-10-12T23:40:00Z','2026-10-13T00:10:00Z','UTC')['duration_minutes'],30)

    def test_dst_spring_gap_and_fall_repeat(self):
        spring=interval_facts('2026-03-08T06:30:00Z','2026-03-08T07:30:00Z','America/New_York')
        self.assertEqual(spring['start']['label'], '2026-03-08 01:30 EST (UTC-05:00)')
        self.assertEqual(spring['end']['label'], '2026-03-08 03:30 EDT (UTC-04:00)')
        self.assertEqual(spring['duration_minutes'],60)
        fall=interval_facts('2026-11-01T05:30:00Z','2026-11-01T06:30:00Z','America/New_York')
        self.assertEqual(fall['start']['label'], '2026-11-01 01:30 EDT (UTC-04:00)')
        self.assertEqual(fall['end']['label'], '2026-11-01 01:30 EST (UTC-05:00)')
        self.assertEqual(fall['duration_minutes'],60)

    def test_invalid_and_all_day(self):
        for start,end in [('2026-01-01T10:00','2026-01-01T11:00Z'),('2026-01-01T10:00Z','2026-01-01T09:00Z')]:
            with self.assertRaises(ValueError): interval_facts(start,end,'UTC')
        facts=event_clock_facts({'all_day':True,'all_day_start':'2026-03-08','all_day_end':'2026-03-09'},'America/New_York')
        self.assertEqual(facts,{'all_day':True,'start_date':'2026-03-08','end_date_exclusive':'2026-03-09','zone':'America/New_York'})

    def test_all_day_validation(self):
        for event in [{'all_day': True}, {'all_day':True,'all_day_start':'2026-03-09','all_day_end':'2026-03-08'}]:
            with self.assertRaises((KeyError, ValueError)):
                event_clock_facts(event,'America/New_York')
