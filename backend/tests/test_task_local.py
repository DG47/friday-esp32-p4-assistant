import asyncio
import json
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.task_local import LocalTaskStore,LocalTaskWorker,BEFORE,AFTER,TEST
from app.task_telegram import TelegramFixtureAdapter


@unittest.skipUnless(sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').exists(), 'Trusted local subprocess fixtures require macOS sandbox-exec')
class LocalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=str(Path(__file__).resolve().parents[2]/'outputs'))
        self.root=Path(self.temp.name)
        self.now=[1000.]
        self.store=LocalTaskStore(self.root/'tasks.db',self.root/'projects',lambda:self.now[0])
        self.worker=LocalTaskWorker(self.store,'owner')

    def tearDown(self):self.temp.cleanup()

    def proposed(self):return self.store.create('owner','start','Fix disposable Python fixture','range-total')

    def approved(self):
        t=self.proposed()
        return self.store.approve('owner',t['id'],'approve',t['revision'],t['scope_digest'],t['nonce'])

    async def test_real_failure_patch_and_pass(self):
        t=self.approved();await self.worker.run(t['id']);done=self.store.get('owner',t['id'])
        self.assertEqual(done['state'],'ready_for_review',done)
        check=done['evidence'][1]['output'];verify=done['evidence'][-1]['output']
        self.assertEqual(check['exit_code'],1);self.assertIn('6 != 10',check['output'])
        self.assertEqual(verify['exit_code'],0);self.assertIn('OK',verify['output'])
        self.assertIn('+    return sum(range(start, stop + 1))',done['artifact']['patch'])
        self.assertEqual((self.worker.project(t['id'])/'range_total.py').read_text(),AFTER)
        self.assertEqual(self.worker.processes,{})

    async def test_crash_gap_after_patch_replays_without_second_write(self):
        t=self.approved()
        for _ in range(3):self.assertTrue(await self.worker.step(t['id']))
        snapshot=self.store.claim('owner',t['id'],'crashed-worker')
        await self.worker.execute(snapshot)  # Crash before DB artifact commit.
        path=self.worker.project(t['id'])/'range_total.py';mtime=path.stat().st_mtime_ns
        self.now[0]+=31;self.store.recover('owner')
        await LocalTaskWorker(self.store,'owner').run(t['id'])
        self.assertEqual(self.store.get('owner',t['id'])['state'],'ready_for_review')
        self.assertEqual(path.stat().st_mtime_ns,mtime)
        self.assertFalse(self.store.commit('owner',t['id'],snapshot['fence'],snapshot['generation'],{'source_digest':snapshot['scope']['original_digest']}))

    async def test_stop_real_child_reaped(self):
        t=self.approved();await self.worker.step(t['id'])
        running=asyncio.create_task(self.worker.step(t['id']))
        for _ in range(100):
            if self.worker.processes:break
            await asyncio.sleep(.001)
        self.assertTrue(self.worker.processes)
        proc=self.worker.processes[t['id']]
        self.store.stop('owner',t['id'],'stop')
        await running
        self.assertIsNotNone(proc.returncode)
        self.assertEqual(self.store.get('owner',t['id'])['state'],'stopped')
        self.assertEqual(len(self.store.get('owner',t['id'])['evidence']),1)

    async def test_changed_approval_and_symlink_escape(self):
        t=self.proposed();refreshed=self.store.review('owner',t['id'],'review',t['revision'])
        with self.assertRaises(ValueError):self.store.approve('owner',t['id'],'old',t['revision'],t['scope_digest'],t['nonce'])
        self.store.approve('owner',t['id'],'new',refreshed['revision'],refreshed['scope_digest'],refreshed['nonce'])
        path=self.worker.project(t['id']);(path/'range_total.py').unlink()
        target=self.root/'outside.py';target.write_text(BEFORE)
        (path/'range_total.py').symlink_to(target)
        await self.worker.run(t['id'])
        self.assertEqual(self.store.get('owner',t['id'])['state'],'failed')
        self.assertEqual(target.read_text(),BEFORE)
        with self.assertRaises(ValueError):self.worker.project('../outside')

    async def test_real_startup_failure_is_recorded(self):
        t=self.approved()
        with patch.object(self.worker,'profile',return_value='(version 1)(deny default)'):
            await self.worker.run(t['id'])
        done=self.store.get('owner',t['id']);self.assertEqual(done['state'],'failed')
        self.assertNotEqual(done['evidence'][-1]['output']['exit_code'],0)

    async def test_telegram_duplicate_local_task_same_identity(self):
        adapter=TelegramFixtureAdapter(self.store);adapter.bind_verified(7,7,'owner')
        update={'update_id':1,'message':{'from':{'id':7,'is_bot':False},'chat':{'id':7,'type':'private'},'text':'/fixture range-total'}}
        first=adapter.handle(update);self.assertEqual(adapter.handle(update),first)
        self.assertEqual(len(self.store.list('owner')),1)
        token=first['buttons'][0]['callback_data']
        approve={'update_id':2,'callback_query':{'from':update['message']['from'],'message':update['message'],'data':token}}
        adapter.handle(approve);t=self.store.list('owner')[0];await self.worker.run(t['id'])
        self.assertEqual(self.store.get('owner',t['id'])['state'],'ready_for_review')

    async def test_sandbox_denies_network_outside_read_and_write(self):
        t=self.approved();project=self.worker.project(t['id']);outside=self.root/'private-marker';outside.write_text('original synthetic marker')
        code="import socket,pathlib,os; p=pathlib.Path("+repr(str(outside))+");\nfor op in [lambda:p.read_text(),lambda:pathlib.Path('blocked').write_text('x'),lambda:socket.socket().connect(('127.0.0.1',9)),lambda:os.fork()]:\n try: op(); print('UNEXPECTED')\n except PermissionError: print('DENIED')\n"
        # Fixed test-only probe; production runner argv remains exact and closed.
        proc=await asyncio.create_subprocess_exec('/usr/bin/sandbox-exec','-p',self.worker.profile(project),str(Path(sys.executable).resolve()),'-I','-S','-B','-c',code,cwd=project,env={'LANG':'C'},stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
        output,_=await asyncio.wait_for(proc.communicate(),4)
        self.assertEqual(proc.returncode,0,output)
        self.assertEqual(output.decode().count('DENIED'),4,output)
        self.assertNotIn('UNEXPECTED',output.decode())

    async def test_expired_patch_authority_no_write(self):
        t=self.approved()
        for _ in range(3):await self.worker.step(t['id'])
        snapshot=self.store.claim('owner',t['id'],'stale')
        self.now[0]+=31
        with self.assertRaises(ValueError):await self.worker.execute(snapshot)
        self.assertEqual((self.worker.project(t['id'])/'range_total.py').read_text(),BEFORE)

    async def test_stale_worker_zero_preparation_or_process_admission(self):
        t=self.approved();stale=self.store.claim('owner',t['id'],'old')
        self.now[0]+=31;self.store.recover('owner')
        self.store.claim('owner',t['id'],'new')
        with patch.object(self.worker,'project') as prepare:
            with self.assertRaises(ValueError):await self.worker.execute(stale)
            prepare.assert_not_called()
        with patch('app.task_local.subprocess.Popen') as spawn:
            with self.assertRaises(ValueError):await self.worker.command(stale,self.store.root/t['id'])
            spawn.assert_not_called()
        self.assertFalse((self.store.root/t['id']).exists())

    async def test_output_bound_reaps_real_child(self):
        # Internal adversarial registered-command replacement, never user input.
        with patch('app.task_local.BOOT',"import signal; signal.alarm(3); print('x'*20000)"):
            t=self.approved();await self.worker.run(t['id'])
        self.assertEqual(self.store.get('owner',t['id'])['state'],'failed')
        self.assertEqual(self.worker.processes,{})

    async def test_child_watchdog_survives_no_supervisor_monitor(self):
        import subprocess,time
        project=self.worker.project(self.proposed()['id'])
        # The actual child alarm is independent of the parent monitor/crash.
        proc=subprocess.Popen(['/usr/bin/sandbox-exec','-p',self.worker.profile(project),str(Path(sys.executable).resolve()),'-I','-S','-B','-c',"import signal,time; signal.alarm(1); time.sleep(100)"],cwd=project,env={'LANG':'C'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        try:
            self.assertEqual(await asyncio.to_thread(proc.wait,3),-signal.SIGALRM)
        finally:
            if proc.poll() is None:os.killpg(proc.pid,signal.SIGKILL)
            await asyncio.to_thread(proc.wait)
