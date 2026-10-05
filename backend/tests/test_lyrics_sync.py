import unittest
from app.tools.lyrics import TrackIdentity
from app.tools.lyrics_sync import *

IDENTITY=TrackIdentity('original-fixture','ZZAAA2600001','Original fixture | Original album | 10000ms')
def observation(sequence=1,position=0,playing=True,observed=0,identity=IDENTITY,**extra):
    return PlaybackObservation(sequence,identity,position,10000,playing,observed,**extra)
def document(identity=IDENTITY,**extra):
    return TimedDocument(identity,10000,(TimedLine(1000,'The copper kite turns.',2500),TimedLine(3000,'A paper boat follows.'),TimedLine(7000,'We leave a light by the gate.')),'Original synthetic text','Invented solely for offline QA',**extra)

class SyncTests(unittest.TestCase):
    def setUp(self):
        self.now=[0];self.engine=LyricsSyncEngine(True,lambda:self.now[0]);self.engine.observe(observation());self.ticket=self.engine.begin_load();self.assertTrue(self.engine.apply(self.ticket,document()))
    def test_disabled_by_default(self):
        engine=LyricsSyncEngine();self.assertFalse(engine.observe(observation()));self.assertIsNone(engine.begin_load());self.assertEqual(engine.current()['status'],'disabled')
    def test_current_line_boundaries_and_instrumental_gap(self):
        for position,index in [(0,None),(999,None),(1000,0),(2499,0),(2500,None),(2999,None),(3000,1),(7000,2),(9999,2),(10000,None)]:
            self.engine.observe(observation(sequence=position+2,position=position,playing=False))
            self.assertEqual(self.engine.current()['line_index'],index)
    def test_interpolation_pause_resume_and_seek_back(self):
        self.now[0]=1.1;self.assertEqual(self.engine.current()['line_index'],0)
        self.engine.observe(observation(2,position=1100,playing=False,observed=1.1))
        self.now[0]=4;self.assertEqual(self.engine.current()['line_index'],0)
        self.engine.observe(observation(3,position=3100,playing=True,observed=4));self.assertEqual(self.engine.current()['line_index'],1)
        self.engine.observe(observation(4,position=1200,playing=False,observed=4));self.assertEqual(self.engine.current()['line_index'],0)
        self.engine.observe(observation(5,position=7000,playing=True,observed=4));self.assertEqual(self.engine.current()['line_index'],2)
    def test_offsets_advance_or_delay_and_are_bounded(self):
        self.engine.set_offset(1000);self.assertEqual(self.engine.current()['line_index'],0)
        self.engine.set_offset(-1000);self.assertIsNone(self.engine.current()['line'])
        with self.assertRaises(ValueError):self.engine.set_offset(10001)
    def test_stale_paused_or_playing_hides_line(self):
        self.now[0]=6;self.assertEqual(self.engine.current()['status'],'stale');self.assertIsNone(self.engine.current()['line'])
        self.engine.observe(observation(2,position=1000,playing=False,observed=6));self.assertEqual(self.engine.current()['line_index'],0)
        self.now[0]=12;self.assertEqual(self.engine.current()['status'],'stale')
    def test_track_change_clears_and_rejects_old_document(self):
        other=TrackIdentity('other','','Other fixture | 10000ms');self.engine.observe(observation(2,identity=other))
        self.assertIsNone(self.engine.current()['line']);self.assertFalse(self.engine.apply(self.ticket,document()))
    def test_superseded_provider_request_is_ignored(self):
        first=self.engine.begin_load();second=self.engine.begin_load();self.assertTrue(self.engine.apply(second,document()))
        self.assertFalse(self.engine.apply(first,None));self.assertEqual(self.engine.status,'timecoded')
    def test_old_observations_cannot_seek_or_change_track(self):
        self.engine.observe(observation(3,position=3000,playing=False));self.assertFalse(self.engine.observe(observation(2,position=7000)))
        self.assertEqual(self.engine.current()['line_index'],1)
    def test_ads_episodes_missing_and_errors_clear(self):
        for index,extra in enumerate([{'item_kind':'ad'},{'item_kind':'episode'},{'status':'unavailable'},{'status':'stale'}]):
            self.engine.observe(observation(index+2,**extra));self.assertIsNone(self.engine.document);self.assertIsNone(self.engine.current()['line'])
    def test_mismatch_version_duration_and_missing_attribution_are_rejected(self):
        wrong=TrackIdentity(IDENTITY.track_id,IDENTITY.isrc,'Other version')
        candidates=[document(wrong),TimedDocument(IDENTITY,15000,document().lines,'Fixture','Original QA'),TimedDocument(IDENTITY,10000,document().lines,'','')]
        for value in candidates:
            ticket=self.engine.begin_load();self.assertFalse(self.engine.apply(ticket,value));self.assertEqual(self.engine.status,'mismatch')
    def test_duplicate_negative_or_unsorted_timestamps_are_rejected(self):
        for lines in [(TimedLine(-1,'Original sample'),),(TimedLine(1000,'Original A'),TimedLine(1000,'Original B')),(TimedLine(3000,'Original A'),TimedLine(2000,'Original B')),(TimedLine(1000,'Original A',4000),TimedLine(3000,'Original B'))]:
            ticket=self.engine.begin_load();value=TimedDocument(IDENTITY,10000,lines,'Fixture','Original QA')
            self.assertFalse(self.engine.apply(ticket,value));self.assertEqual(self.engine.status,'invalid_timing')
    def test_no_lyrics_result_and_synthetic_native_surface(self):
        ticket=self.engine.begin_load();self.assertFalse(self.engine.apply(ticket,None));self.assertEqual(self.engine.current()['status'],'no_synced_lyrics')
        ticket=self.engine.begin_load();self.engine.apply(ticket,document());self.now[0]=1.2;surface=self.engine.synthetic_surface()
        self.assertEqual(surface.subtitle,'The copper kite turns.');self.assertIn('Synthetic',surface.items[0]['label'])
        self.assertFalse(self.engine.current()['live_use_approved'])

class FurtherSyncTests(unittest.TestCase):
    def test_duplicate_result_and_malformed_next_timestamp_do_not_overwrite(self):
        engine=LyricsSyncEngine(True,lambda:0);engine.observe(observation());ticket=engine.begin_load()
        self.assertTrue(engine.apply(ticket,document()));self.assertFalse(engine.apply(ticket,None));self.assertIsNotNone(engine.document)
        ticket=engine.begin_load();bad=TimedDocument(IDENTITY,10000,(TimedLine(0,'Original A',1000),TimedLine('bad','Original B')),'Fixture','Original QA')
        self.assertFalse(engine.apply(ticket,bad));self.assertEqual(engine.status,'invalid_timing')
    def test_document_offset_and_end_clamp(self):
        engine=LyricsSyncEngine(True,lambda:0);engine.observe(observation(position=0));ticket=engine.begin_load()
        self.assertTrue(engine.apply(ticket,document(offset_ms=1000)));self.assertEqual(engine.current()['line_index'],0)
        engine.observe(observation(2,position=10000,playing=True));self.assertEqual(engine.current()['status'],'ended')
