"""Bounded inventory questions/claim constructions; never a semantic truth oracle."""
import re
from .action_request import _outside_quotes, _blocked_scope

NOT_CHECKED='the current reminder inventory has not been checked'
_QUERY=re.compile(r"^(?:(?:please )?(?:show|list) (?:me )?(?:my |the )?(?:active |pending )?reminders|how many (?:active |pending )?reminders do I have|do I have (?:(?:any|no) )?(?:active |pending )?reminders|are there (?:any|no) (?:active |pending )?reminders(?: for me)?)\s*[?.!]*$",re.I)
# Precise existential/possessive current constructions, not all reminder prose.
_UNCERTAIN=re.compile(r"\b(?:whether|maybe|perhaps)\b|\b(?:don't|don’t|do not) know if\b|\b(?:cannot|can't|can’t|could not|couldn't) (?:confirm|verify|say)\b|\bnot (?:true|known|confirmed) (?:that|whether)\b",re.I)
_CLAIM=re.compile(r"\b(?:there (?:are|aren't|are not) (?:no|zero|any|\d+) (?:active |pending )?reminders?(?: to cancel)?|you have (?:no|zero|\d+) (?:active |pending )?reminders?)\b",re.I)


def current_inventory_question(text):
    return bool(_QUERY.fullmatch(text.strip()))


def inventory_not_checked(text, *, verified_count=None):
    """Replace covered unsolicited current claims, preserving surrounding prose.

    Conversation scope can be wrong or expressed outside this grammar; do not
    treat this as a universal inventory-truthfulness guarantee. Quoted/reporting/
    conditional clauses and historical tense are not current inventory receipts.
    """
    masked=_outside_quotes(text)
    replacements=[]
    for match in _CLAIM.finditer(masked):
        # Scope at the current sentence/semicolon, outside masked quotations.
        start=max(masked.rfind('.',0,match.start()),masked.rfind(';',0,match.start()),masked.rfind('\n',0,match.start()))+1
        prefix=masked[start:match.start()].strip()
        if _blocked_scope(prefix) or _UNCERTAIN.search(prefix):continue
        replacements.append((match.start(),match.end()))
    for start,end in reversed(replacements):
        replacement=NOT_CHECKED
        if type(verified_count) is int and verified_count >= 0:
            replacement=f"the checked inventory contains {verified_count} active reminder"+('s' if verified_count!=1 else '')
        if start==0 or text[start-1:start] in '.\n':replacement=replacement[0].upper()+replacement[1:]
        text=text[:start]+replacement+text[end:]
    return text
