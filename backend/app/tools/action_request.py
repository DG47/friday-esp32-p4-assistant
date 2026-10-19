"""Bounded current-request contract; not a classifier of arbitrary model prose.

Quote-aware clause boundaries recognize explicit current English directives.
Quoted/attributed history and negated clauses remain conversation. Only actual
adapter outcomes establish execution; this parser never grants tool authority.
"""
from dataclasses import dataclass
import re

NO_ACTION_PERFORMED = (
    "No action was performed for this request: no supported tool execution was observed. "
    "Choose a supported reminder action, or clarify what you want me to do."
)

@dataclass(frozen=True)
class RequestLanes:
    action: str = ''
    conversation: str = ''


_PREFIX = (r"(?:(?:please|Friday)[, ]+)?"
           r"(?:(?:can|could|would|will) you (?:please )?|"
           r"I (?:need|want|would like) you to |I['’]d like you to )?")
_OPERATION = r"(?:complete|mark|cancel|delete|remove|create|set|schedule|snooze|send|resume|pause|start|stop|book|update|save|change|turn|finish|handle|take care of)\b"
_DIRECTIVE = re.compile(r'^'+_PREFIX+_OPERATION, re.I)
_TARGET = re.compile(r'\b(?:reminders?|timers?|alarms?|calendar|events?|appointments?|meetings?|music|songs?|playback|queue|reports?|messages?|emails?|files?)\b', re.I)
_CHAT_START = r"(?:explain|tell me|help me (?:think|understand|compare)|why\b|how\b|what\b|discuss|say hello|chat)"
_SPLIT_CHAT = re.compile(r'(?:[.;]\s*|\s+(?:and|also|then)\s+)(?:please\s+)?(?='+_CHAT_START+r')', re.I)
_CLAUSE = re.compile(r'[.;]\s+|\s+(?:and|then|also)\s+', re.I)
_CONDITIONAL = re.compile(r'^(?:if|unless|when|suppose|imagine|hypothetically)\b', re.I)
_NEGATED = re.compile(r"^(?:please )?(?:do not|don't|don’t|never|not to)\b|\b(?:you not to|you to not)\b", re.I)
_ATTRIBUTED = re.compile(
    r"^(?:(?:earlier|yesterday|previously|last (?:week|month|year))[, ]+)?"
    r"(?:I|you|we|they|he|she|[A-Z][\w’'-]*|(?:my|our|the|your) \w+(?: \w+){0,2})\s+"
    r"(?:said|asked|wrote|told|mentioned|reported|claimed)\b", re.I)


def _outside_quotes(text):
    """Mask quotes with non-whitespace sentinels, retaining original offsets.

    Supports straight/curly nested quotes and backslash escapes. Apostrophes
    within/after words are not quote openings. An unclosed quote masks its tail;
    ambiguity never invents an inner current directive.
    """
    opens = {'"':'"', "'":"'", '“':'”', '‘':'’'}
    closes=[]
    masked=[]
    backslashes=0
    for index,char in enumerate(text):
        escaped=backslashes % 2 == 1
        inside=bool(closes)
        contraction=(char in {"'",'’'} and index>0 and index+1<len(text)
                     and text[index-1].isalnum() and text[index+1].isalnum())
        if closes and (char,escaped)==closes[-1] and not contraction:
            closes.pop()
        elif not escaped or not closes:
            if char in opens:
                apostrophe=(char in {"'",'‘'} and index>0 and text[index-1].isalnum())
                if not apostrophe:closes.append((opens[char],escaped))
        masked.append('\ufffc' if inside or closes else char)
        backslashes=backslashes+1 if char=='\\' else 0
    return ''.join(masked)


def _is_action(masked):
    head=_CLAUSE.split(masked,maxsplit=1)[0].strip()
    return bool(_DIRECTIVE.match(head) and _TARGET.search(head))


def _blocked_scope(masked):
    return bool(_CONDITIONAL.match(masked) or _NEGATED.search(masked) or _ATTRIBUTED.match(masked))


def request_lanes(text: str) -> RequestLanes:
    """Keep conversational spans intact; never inspect model output for intent.

    Indirect forms explicitly covered: can/could/would/will you, I need/want/would
    like you to, I'd like you to. Pronoun-only/other implicit forms remain outside
    this bounded target contract. This is not general linguistic scope analysis.
    """
    text=text.strip()
    masked=_outside_quotes(text)
    if _CONDITIONAL.match(masked):
        return RequestLanes(conversation=text)
    if _is_action(masked):
        split=_SPLIT_CHAT.search(masked)
        if split:
            return RequestLanes(text[:split.start()].strip(),text[split.end():].strip())
        return RequestLanes(action=text)
    # Conjunctions inside a quoted span are masked. An attributed or negative
    # clause owns its conjunctions until an explicit sentence/semicolon boundary.
    scope_start=0
    for split in _CLAUSE.finditer(masked):
        punctuation=split.group().lstrip().startswith(('.', ';'))
        blocked=_blocked_scope(masked[scope_start:split.start()].strip())
        if punctuation:scope_start=split.end()
        if (punctuation or not blocked) and _is_action(masked[split.end():]):
            return RequestLanes(text[split.end():].strip(),text[:split.start()].strip())
    return RequestLanes(conversation=text)
