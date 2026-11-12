"""Current explicit directive contract, through actual reply/history/controller paths."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
from app.agent import FridayAgent
from app.db import ReminderStore
from app.providers.base import ProviderTurn,ToolCall
from app.tools.action_request import request_lanes,NO_ACTION_PERFORMED
from test_conversation_scenario import settings_for,ScriptedProvider


class ActionRequestContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder=tempfile.TemporaryDirectory()
        path=Path(self.folder.name)/'synthetic.db'
        self.store=ReminderStore(path);self.agent=FridayAgent(settings_for(path),self.store)
        self.reminder=self.store.create('Synthetic active reminder',datetime.now(timezone.utc)+timedelta(hours=1),'owner')
    async def asyncTearDown(self):
        await self.agent.calendar.close();self.folder.cleanup()
    async def reply(self,prompt,turns):
        provider=ScriptedProvider(turns)
        with patch.object(self.agent.providers,'select',return_value=provider):
            reply=await self.agent.handle('owner',prompt,'contract-'+str(len(self.store.conversation('owner'))))
        return reply,provider

    async def test_failed_grammar_cases_cannot_become_current_action_receipts(self):
        # Original reproduction plus contraction, indirect, elliptical, paraphrase
        # and mixed historical/hypothetical/negative forms rejected by prototype.
        claims=[
            'I marked your existing reminder complete. It is done.',
            "I've completed the reminder.",
            'Your existing reminder no longer needs attention; I took care of that for you.',
            'That is now off your list.',
            'The reminder has been ticked off.',
            'Yesterday you asked me to handle it, so I completed it just now.',
            'If you were wondering, I completed your reminder.',
            'I did not send a message, although your reminder is complete.',
            'Your reminder is complete.',
            'I sent the report to your phone.',
        ]
        for claim in claims:
            with self.subTest(claim=claim):
                reply,_=await self.reply('Please take care of my existing reminder as we discussed.',[ProviderTurn(text=claim)])
                self.assertEqual(reply.text,NO_ACTION_PERFORMED)
                self.assertNotIn(claim,json.dumps(self.store.conversation('owner')))
                self.assertIsNotNone(self.store.active_by_id(self.reminder.id,'owner'))

    async def test_chat_history_hypotheticals_negation_and_quotation_are_preserved(self):
        pairs=[
            ('Hi Friday.','Hello! How can I help?'),
            ('Explain why the sky looks blue.','Air scatters shorter blue wavelengths more strongly.'),
            ('Help me compare two priorities.','Which two priorities would you like to compare?'),
            ('What did I say earlier?','You said yesterday that I completed the reminder.'),
            ('What happens if a reminder is completed?','If I completed the reminder, it would disappear.'),
            ('Did you complete the reminder?','I have not completed your reminder.'),
            ('Explain an example action claim.','The phrase "I completed the reminder" is an example of an action claim.'),
            ('Discuss a childhood memory.','When I completed my homework as a child, I felt relieved.'),
            ('What do you think of that suggestion?','I changed my mind about that suggestion.'),
            ('Did I ask you to do anything?','Did you say "I completed the reminder"?'),
        ]
        for prompt,answer in pairs:
            with self.subTest(prompt=prompt):
                reply,_=await self.reply(prompt,[ProviderTurn(text=answer)])
                self.assertEqual(reply.text,answer)
                self.assertEqual(self.store.conversation('owner')[-1]['content'],answer)

    async def test_mixed_action_and_chat_has_separate_tool_free_question(self):
        lie="I've completed the reminder. Blue wavelengths scatter."
        chat='Blue wavelengths scatter more strongly.'
        reply,p=await self.reply('Complete my reminder and explain why the sky looks blue.',[ProviderTurn(text=lie),ProviderTurn(text=chat)])
        self.assertEqual(reply.text,NO_ACTION_PERFORMED+'\n'+chat)
        self.assertNotIn(lie,json.dumps(self.store.conversation('owner')))
        self.assertEqual(p.requests[-1]['messages'],[{'role':'user','content':'explain why the sky looks blue.'}])
        self.assertEqual(p.requests[-1]['tools'],[])
        self.assertNotIn('Synthetic active reminder',json.dumps(p.requests[-1]))

    async def test_mixed_observed_action_and_chat_keeps_actual_receipt(self):
        due=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
        reply,p=await self.reply('Create a reminder and explain why the sky looks blue.',[
            ProviderTurn(tool_calls=[ToolCall('create','create_reminder',{'text':'Synthetic new item','due_at':due})]),
            ProviderTurn(text='I deleted everything.'),ProviderTurn(text='Blue light scatters.')])
        self.assertEqual(reply.text,'The reminder is set.\nBlue light scatters.')
        self.assertEqual(len(self.store.active('owner')),2)
        self.assertEqual(p.requests[-1]['tools'],[])

    def test_request_scope_does_not_classify_history_or_negation_as_commands(self):
        for prompt in ['I completed it yesterday.','Do not complete my reminder.','If I complete it, what happens?',
                       'You said "complete my reminder".','Did you complete my reminder?',
                       'Complete this sentence.','Start a story.','Change my mind about this suggestion.',
                       'If I ask later, then complete my reminder.']:
            self.assertFalse(request_lanes(prompt).action,prompt)
        self.assertTrue(request_lanes('Could you please complete my reminder?').action)
        self.assertEqual(request_lanes('Explain blue light; complete my reminder.').conversation,'Explain blue light')

    async def test_optional_chat_failure_cannot_hide_an_admitted_action(self):
        due=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
        class FailingChat(ScriptedProvider):
            async def complete(self,messages,instructions,tools):
                if not tools:raise RuntimeError('SYNTHETIC_PRIVATE_FAILURE')
                return await super().complete(messages,instructions,tools)
        provider=FailingChat([
            ProviderTurn(tool_calls=[ToolCall('created','create_reminder',{'text':'Synthetic admitted','due_at':due})]),
            ProviderTurn(text='Everything is complete.')])
        # Optional chat raises; observed creation must still return.
        with patch.object(self.agent.providers,'select',return_value=provider):
            reply=await self.agent.handle('owner','Create a reminder and explain blue light.','chat-failed')
        self.assertEqual(reply.text,'The reminder is set.\nThe conversational part could not be answered.')
        self.assertEqual(len(self.store.active('owner')),2)

    async def test_read_receipt_cannot_acknowledge_requested_mutation(self):
        reply,_=await self.reply('Complete my reminder.',[
            ProviderTurn(tool_calls=[ToolCall('read','list_reminders',{})]),
            ProviderTurn(text='Your reminder is done.')])
        self.assertIn('1 active reminder.',reply.text)
        self.assertIn(NO_ACTION_PERFORMED,reply.text)
        self.assertIsNotNone(self.store.active_by_id(self.reminder.id,'owner'))

    async def test_chat_tool_proposal_is_never_executed(self):
        due=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
        reply,_=await self.reply('Complete my reminder and explain blue light.',[
            ProviderTurn(text='Done.'),ProviderTurn(tool_calls=[ToolCall('forbidden','create_reminder',{'text':'Should not exist','due_at':due})])])
        self.assertEqual(len(self.store.active('owner')),1)
        self.assertEqual(reply.text,NO_ACTION_PERFORMED+'\nThe conversational part could not be answered.')

    async def test_stop_during_separate_chat_prevents_reply_and_history(self):
        from app.execution_authority import execution_scope,ExecutionRevoked
        allowed=True
        class Provider:
            name='synthetic'
            async def complete(self,messages,instructions,tools):
                nonlocal allowed
                if not tools:allowed=False
                return ProviderTurn(text='Synthetic late chat')
        with execution_scope(lambda:allowed),patch.object(self.agent.providers,'select',return_value=Provider()):
            with self.assertRaises(ExecutionRevoked):
                await self.agent.handle('owner','Complete my reminder and explain blue light.','revoked-mixed')
        self.assertEqual(self.store.conversation('owner'),[])
