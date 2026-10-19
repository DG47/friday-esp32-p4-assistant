"""Bounded current inventory facts and the recorded natural-model regression."""
import json,unittest
from pathlib import Path
from unittest.mock import patch
import test_action_request_contract as fixture
from app.providers.base import ProviderTurn,ToolCall
from app.tools.reminder_inventory import inventory_not_checked,NOT_CHECKED

class InventoryInvestigation(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.ActionRequestContract.asyncSetUp
    asyncTearDown=fixture.ActionRequestContract.asyncTearDown
    reply=fixture.ActionRequestContract.reply

    async def test_actual_earlier_local_model_output_no_longer_claims_empty(self):
        recorded=json.loads((Path(__file__).parent/'fixtures/recorded_inventory_claim.json').read_text())
        original=recorded['reply']
        answer,p=await self.reply('You said cancel my reminder and delete my file. Explain those words.',[ProviderTurn(text=original)])
        self.assertIn('"Cancel a reminder" means',answer.text)
        self.assertNotIn('there are no active reminders',answer.text)
        self.assertIn(NOT_CHECKED,answer.text)
        self.assertEqual(len(p.requests),1)
        self.assertEqual(len(self.store.active('owner')),1)
        self.assertEqual(self.store.conversation('owner')[-1]['content'],answer.text)

    async def test_direct_inventory_forces_owner_scoped_list_without_model(self):
        with patch.object(self.agent.providers,'select',side_effect=AssertionError('must not ask model')):
            reply=await self.agent.handle('owner','How many active reminders do I have?','inventory')
        self.assertEqual(reply.text,'1 active reminder.')
        self.assertEqual(reply.tool,'list_reminders')
        reply=await self.agent.handle('different-owner','How many active reminders do I have?','other-inventory')
        self.assertEqual(reply.text,'0 active reminders.')

    async def test_observed_inventory_cannot_be_replaced_by_false_empty_model_text(self):
        reply,_=await self.reply('Can you check the number of reminders?',[
            ProviderTurn(tool_calls=[ToolCall('inventory','list_reminders',{})]),
            ProviderTurn(text='There are no active reminders.')])
        self.assertEqual(reply.text,'1 active reminder.')

    def test_quote_history_hypothetical_negation_and_explanation_preserved(self):
        values=['A reminder is a prompt to do something later.',
                'You said there are no active reminders.',
                'If there are no active reminders, cancellation has no target.',
                'Explain “there are no active reminders”.',
                'There were no active reminders yesterday.',
                'Do not say there are no active reminders.',
                "I don't know whether there are no active reminders.",
                "I don’t know if there are no active reminders.",
                'I cannot confirm whether there are no active reminders.',
                'I cannot verify there are no active reminders.',
                'It is not true that there are no active reminders.',
                'Yesterday you said you have 2 pending reminders.',
                'Explain “she said ‘you have 2 pending reminders’ yesterday”.']
        for value in values:
            with self.subTest(value=value):self.assertEqual(inventory_not_checked(value),value)

    async def test_positive_and_negative_queries_use_actual_scoped_inventory(self):
        from datetime import datetime,timezone
        self.store.create('Other owner must not count',datetime(2030,1,1,tzinfo=timezone.utc),'other')
        for prompt in ['Do I have any active reminders?','Are there any pending reminders for me?',
                       'How many active reminders do I have?','Are there no active reminders for me?',
                       'Do I have no active reminders?']:
            with self.subTest(prompt=prompt):
                answer=await self.agent.handle('owner',prompt,'inventory-'+prompt)
                self.assertEqual(answer.text,'1 active reminder.')
                self.assertNotIn('Other owner',str(answer.surface))
        self.store.mutate_reminder(self.reminder.id,'owner','complete','synthetic-complete')
        answer=await self.agent.handle('owner','Do I have any active reminders?','empty-inventory')
        self.assertEqual(answer.text,'0 active reminders.')
        self.assertEqual(answer.surface.items,[])
        self.assertEqual(len(self.store.active('other')),1)

    def test_positive_negative_current_claims_do_not_invent_inventory(self):
        for claim in ['There are no active reminders.','There are 2 pending reminders.',
                      'You have 1 active reminder.','You have no pending reminders.']:
            with self.subTest(claim=claim):
                projected=inventory_not_checked('Hello. '+claim+' What would you like to discuss?')
                self.assertIn(NOT_CHECKED,projected.lower())
                self.assertTrue(projected.startswith('Hello. '))
                self.assertTrue(projected.endswith(' What would you like to discuss?'))
        # Remaining unsupported constructions are explicit, not a universal guard.
        for unsupported in ['Your reminder list is empty.','You have two pending reminders.']:
            self.assertEqual(inventory_not_checked(unsupported),unsupported)

    async def test_list_is_refreshed_after_model_wait_not_replayed_as_current(self):
        from test_conversation_scenario import ScriptedProvider
        outer=self
        class Provider(ScriptedProvider):
            async def complete(self,messages,instructions,tools):
                if self.requests:
                    outer.store.mutate_reminder(outer.reminder.id,'owner','complete','concurrent-complete')
                return await super().complete(messages,instructions,tools)
        provider=Provider([ProviderTurn(tool_calls=[ToolCall('old-list','list_reminders',{})]),
                           ProviderTurn(text='You have 1 active reminder.')])
        with patch.object(self.agent.providers,'select',return_value=provider):
            answer=await self.agent.handle('owner','Can you check my reminder count?','old-list')
        self.assertEqual(answer.text,'0 active reminders.')
        self.assertEqual(answer.surface.items,[])
        self.assertNotIn(self.reminder.text,str(answer.surface))

    async def test_superseded_or_replaced_list_cannot_publish_old_inventory(self):
        from test_conversation_scenario import ScriptedProvider
        for mode in ['new-request','disconnect','evict-same-id']:
            with self.subTest(mode=mode):
                outer=self
                class Provider(ScriptedProvider):
                    async def complete(self,messages,instructions,tools):
                        if self.requests:
                            context=outer.agent.working_context
                            if mode=='new-request':context.begin('owner','replacement')
                            elif mode=='disconnect':context.disconnect('owner')
                            else:
                                for i in range(32):context.begin('other-'+str(i),'other-request')
                                context.begin('owner','stale-'+mode)
                        return await super().complete(messages,instructions,tools)
                provider=Provider([ProviderTurn(tool_calls=[ToolCall('stale','list_reminders',{})]),ProviderTurn(text='You have 1 active reminder.')])
                with patch.object(self.agent.providers,'select',return_value=provider):
                    answer=await self.agent.handle('owner','Can you check my reminder count?','stale-'+mode)
                self.assertEqual(answer.text,'The active reminders could not be verified.')
                self.assertNotIn(self.reminder.text,str(answer.surface))
                self.assertNotIn('1 active reminder',answer.text)

    async def test_read_failure_is_not_empty_and_does_not_leak_exception(self):
        with patch.object(self.agent,'_execute_tool',side_effect=RuntimeError('SYNTHETIC_PRIVATE_ERROR')):
            answer=await self.agent.handle('owner','How many active reminders do I have?','failed-read')
        self.assertEqual(answer.text,'The active reminders could not be verified.')
        self.assertNotIn('SYNTHETIC_PRIVATE_ERROR',str(answer))
        self.assertNotIn('0 active',answer.text)

    async def test_mixed_chat_delay_refreshes_inventory_before_presentation(self):
        from test_conversation_scenario import ScriptedProvider
        outer=self
        class Provider(ScriptedProvider):
            async def complete(self,messages,instructions,tools):
                if not tools:
                    outer.store.mutate_reminder(outer.reminder.id,'owner','complete','during-chat')
                return await super().complete(messages,instructions,tools)
        provider=Provider([ProviderTurn(tool_calls=[ToolCall('read','list_reminders',{})]),
                           ProviderTurn(text='It is complete.'),ProviderTurn(text='Blue light scatters.')])
        with patch.object(self.agent.providers,'select',return_value=provider):
            answer=await self.agent.handle('owner','Complete my reminder and explain blue light.','chat-stale')
        self.assertTrue(answer.text.startswith('0 active reminders.'))
        self.assertTrue(answer.text.endswith('Blue light scatters.'))
        self.assertEqual(answer.surface.items,[])
