"""Bounded current-request contract; not a classifier of arbitrary model prose.

Recognize explicit English operation directives at clause starts. Quoted history,
questions about earlier actions, negation and hypotheticals are conversation.
Only the action lane is projected from tool evidence. The independent chat lane
receives no tool authority or operation result context.
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


_PREFIX = r"(?:(?:please|Friday)[, ]+)?(?:(?:can|could|would|will) you (?:please )?)?"
# The scope is deliberately imperative syntax, not occurrences of success words.
_OPERATION = r"(?:complete|mark|cancel|delete|remove|create|set|schedule|snooze|send|resume|pause|start|stop|book|update|save|change|turn|finish|handle|take care of)\b"
_DIRECTIVE = re.compile(r'^'+_PREFIX+_OPERATION, re.I)
_TARGET = re.compile(r'\b(?:reminders?|timers?|alarms?|calendar|events?|appointments?|meetings?|music|songs?|playback|queue|reports?|messages?|emails?|files?)\b', re.I)

def _is_action(text):
    # The covered operation vocabulary needs an operational target. Commands
    # such as "complete this sentence" or "start a story" remain chat.
    head = re.split(r'[.;]|\s+(?:and|then|also)\s+', text, maxsplit=1, flags=re.I)[0]
    return bool(_DIRECTIVE.match(head) and _TARGET.search(head))

# Independent conversational clauses are requested explicitly; do not try to
# recover them by inspecting generated prose or believing a model scope label.
_CHAT_START = r"(?:explain|tell me|help me (?:think|understand|compare)|why\b|how\b|what\b|discuss|say hello|chat)"
_SPLIT_CHAT = re.compile(r'(?:[.;]\s*|\s+(?:and|also|then)\s+)(?:please\s+)?(?='+_CHAT_START+r')', re.I)
_CLAUSE = re.compile(r'[.;]\s+|\s+(?:and|then|also)\s+', re.I)


def request_lanes(text: str) -> RequestLanes:
    """Bind only explicit current directives, never action vocabulary in a story.

    Unsupported/ambiguous language remains conversational; this contract makes no
    universal claim about action intent or hallucination detection in that lane.
    """
    text=text.strip()
    if re.match(r'^(?:if|unless|when|suppose|imagine|hypothetically)\b', text, re.I):
        return RequestLanes(conversation=text)
    if _is_action(text):
        split=_SPLIT_CHAT.search(text)
        if split:
            return RequestLanes(text[:split.start()].strip(),text[split.end():].strip())
        return RequestLanes(action=text)
    # A conversational lead followed by an explicit imperative is mixed input.
    for split in _CLAUSE.finditer(text):
        tail=text[split.end():].strip()
        if _is_action(tail):
            return RequestLanes(tail,text[:split.start()].strip())
    return RequestLanes(conversation=text)
