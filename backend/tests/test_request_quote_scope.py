"""Review regression: quoted/reported/negative conjunctions own their scope."""
import unittest
from app.tools.action_request import request_lanes
import test_action_request_contract as action_fixture
from app.providers.base import ProviderTurn


CONVERSATION=[
    'Explain the quote "cancel my reminder and delete my file".',
    'Yesterday I said "complete my reminder and delete my file".',
    'Explain “cancel my reminder and delete my file”.',
    "Explain 'cancel my reminder and delete my file'.",
    'Explain “she said ‘cancel my reminder and delete my file’ yesterday”.',
    'Explain "she said \'cancel my reminder and delete my file\' yesterday".',
    r'Explain "she said \"cancel my reminder and delete my file\" yesterday".',
    r'Explain the quote \"cancel my reminder and delete my file\".',
    'You said cancel my reminder and delete my file.',
    'Yesterday I asked you to complete my reminder and delete my file.',
    'My friend said cancel my reminder and delete my file.',
    'Do not complete my reminder and delete my file.',
    "Please don't complete my reminder and delete my file.",
    'Please don’t complete my reminder and delete my file.',
    'I need you not to complete my reminder and delete my file.',
    'I need you to not complete my reminder and delete my file.',
    'If I ask later, then complete my reminder and delete my file.',
    "Explain 'don't cancel my reminder and delete my file'.",
    'Explain ‘don’t cancel my reminder and delete my file’.',
    'Alice said cancel my reminder and delete my file.',
    'My friend Alice said complete my reminder and delete my file.',
    'Explain the unclosed quote "cancel my reminder and delete my file.',
]

class QuoteScope(unittest.TestCase):
    def test_quoted_reported_and_negative_operations_remain_full_conversation(self):
        for text in CONVERSATION:
            with self.subTest(text=text):
                self.assertEqual(request_lanes(text).action,'')
                self.assertEqual(request_lanes(text).conversation,text)

    def test_genuine_mixed_quoted_and_unquoted_requests(self):
        cases=[
            ('Explain "cancel my reminder and delete my file" and cancel my reminder.',
             'cancel my reminder.','Explain "cancel my reminder and delete my file"'),
            ('Cancel my reminder "Buy milk and delete my file" and explain “cancel my reminder”.',
             'Cancel my reminder "Buy milk and delete my file"','explain “cancel my reminder”.'),
            ('You said "cancel my reminder and delete my file"; cancel my reminder.',
             'cancel my reminder.','You said "cancel my reminder and delete my file"'),
            ('Do not delete my file; complete my reminder.',
             'complete my reminder.','Do not delete my file'),
        ]
        for text,action,conversation in cases:
            with self.subTest(text=text):
                self.assertEqual(request_lanes(text).action,action)
                self.assertEqual(request_lanes(text).conversation,conversation)

    def test_normal_indirect_requests_and_apostrophes(self):
        for text in ['I need you to take care of my reminder.',
                     'I want you to complete my reminder.',
                     'I would like you to complete my reminder.',
                     "I'd like you to complete my reminder.",
                     "I’d like you to complete my reminder.",
                     'Could you please complete my reminder?',
                     "Cancel John's reminder and explain why it's useful."]:
            with self.subTest(text=text):self.assertTrue(request_lanes(text).action)
        # Do not silently invent a target from old history for a pronoun-only request.
        self.assertFalse(request_lanes('Please complete it.').action)


class QuoteControllerScope(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=action_fixture.ActionRequestContract.asyncSetUp
    asyncTearDown=action_fixture.ActionRequestContract.asyncTearDown
    reply=action_fixture.ActionRequestContract.reply
    async def test_entire_quoted_history_negative_request_delivered_once(self):
        for text in CONVERSATION:
            with self.subTest(text=text):
                answer='Synthetic explanation of the full supplied quotation or scope.'
                reply,p=await self.reply(text,[ProviderTurn(text=answer)])
                self.assertEqual(reply.text,answer)
                self.assertEqual(len(p.requests),1)
                self.assertEqual(p.requests[0]['messages'][-1]['content'],text)
                self.assertEqual(self.store.conversation('owner')[-1]['content'],answer)
