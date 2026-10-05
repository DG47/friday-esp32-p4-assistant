"""Deterministic transport/scenario contracts, not tests of real model style.
Scripted replies below are synthetic proposed behavior, not generated output.
"""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from app.agent import FridayAgent, TOOL_DEFINITIONS
from app.config import Settings
from app.db import ReminderStore
from app.providers.base import ProviderTurn, TextProvider, ToolCall

class ScriptedProvider(TextProvider):
    name = 'synthetic-persona-contract'
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []
    async def complete(self, messages, instructions, tools):
        self.requests.append((deepcopy(messages), instructions, deepcopy(tools)))
        return self.turns.pop(0)

class PersonaContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        settings = Settings(assistant_provider='mock', calendar_provider='none',
                            weather_provider='none', tts_provider='none',
                            assistant_database_path=Path(self.temp.name)/'test.db')
        self.store = ReminderStore(settings.assistant_database_path)
        self.agent = FridayAgent(settings, self.store)
    async def asyncTearDown(self):
        self.temp.cleanup()
    async def test_contextual_restraint_reaches_provider_without_extra_ack_or_action(self):
        scenarios = [
            ('I am worried about my health; please keep this serious.', 'What would you like help with?'),
            ('I cannot pay my bills; no jokes, please.', 'Which bill would you like to work through first?'),
            ('That failed. Keep it brief and do not joke.', 'Please try again.'),
            ('I need quiet right now.', 'Of course.'),
        ]
        for index,(request,proposed_reply) in enumerate(scenarios):
            with self.subTest(request=request):
                provider = ScriptedProvider([ProviderTurn(text=proposed_reply)])
                reply = await self.agent._handle_with_model('fixture-'+str(index),request,provider,request_id='contract-'+str(index))
                self.assertEqual(reply.text, proposed_reply) # Verifies delivery, not model tone.
                self.assertEqual(len(provider.requests),1)
                messages,instructions,tools = provider.requests[0]
                self.assertEqual(messages,[{'role':'user','content':request}])
                self.assertIn('financial distress',instructions)
                self.assertIn('Most replies need no joke at all.',instructions)
                self.assertIn('never facts about the user',instructions)
                self.assertIn('Never claim that a tool action',instructions)
                self.assertEqual(tools,TOOL_DEFINITIONS)
                self.assertEqual(self.store.pending(),[])
    async def test_followup_receives_explicit_context_not_example_facts(self):
        provider = ScriptedProvider([ProviderTurn(text='The fixture plan is to walk after lunch.'),ProviderTurn(text='After lunch.')])
        await self.agent._handle_with_model('followup','My fixture plan is to walk after lunch.',provider,request_id='first')
        await self.agent._handle_with_model('followup','When was that?',provider,request_id='second')
        messages=provider.requests[1][0]
        self.assertIn({'role':'user','content':'My fixture plan is to walk after lunch.'},messages)
        self.assertEqual(messages[-1],{'role':'user','content':'When was that?'})
        self.assertFalse(any('three meetings' in m.get('content','').lower() for m in messages))
    async def test_failure_result_reaches_provider_before_scripted_plain_reply(self):
        provider=ScriptedProvider([ProviderTurn(tool_calls=[ToolCall(id='weather-test',name='get_weather',arguments={})]),ProviderTurn(text="Weather isn't available right now.")])
        reply=await self.agent._handle_with_model('failure','Get the weather.',provider,request_id='failed-weather')
        result=provider.requests[1][0][-1]
        self.assertEqual(result['role'],'tool')
        self.assertFalse(json.loads(result['content'])['ok'])
        self.assertEqual(reply.text,"Weather isn't available right now.")
        self.assertEqual(len(provider.requests),2)
        self.assertEqual(self.store.pending(),[])
