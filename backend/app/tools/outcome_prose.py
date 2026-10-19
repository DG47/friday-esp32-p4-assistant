"""Operation receipts are derived from adapter results, never model prose."""
import json

NO_VERIFIED_OUTCOME = (
    "I have not verified an action outcome for this request. "
    "Choose a supported Calendar, Weather or reminder action."
)
REMINDER_MUTATIONS = {'create_reminder','cancel_reminder','complete_reminder','snooze_reminder'}


def observed_outcome_prose(name, outcome):
    if name not in REMINDER_MUTATIONS | {'list_reminders','get_music_state'}:
        return None
    try:
        payload=json.loads(outcome.content)
    except (TypeError,ValueError):
        return 'The tool returned no verifiable result. No completion is confirmed.'
    if not isinstance(payload,dict):
        return 'The tool returned no verifiable result. No completion is confirmed.'
    ok=outcome.success is True and payload.get('ok') is True
    if name in REMINDER_MUTATIONS:
        if not ok or not isinstance(payload.get('reminder'),dict):
            return 'The reminder action has no verified successful outcome. No completion is confirmed.'
        return {'create_reminder':'The reminder is set.',
                'cancel_reminder':'The reminder is cancelled.',
                'complete_reminder':'The reminder is complete.',
                'snooze_reminder':'The reminder is snoozed.'}[name]
    if name=='list_reminders':
        reminders=payload.get('reminders')
        if not ok or not isinstance(reminders,list):return 'The active reminders could not be verified.'
        count=len(reminders)
        return f'{count} active reminder'+('s.' if count!=1 else '.')
    # This fixed adapter message contains generic observed playback state, not
    # private track metadata or free-form model acknowledgment.
    message=payload.get('message')
    if not isinstance(message,str) or not message:return 'Playback state could not be verified.'
    if ok and payload.get('can_resume') is True and payload.get('playing') is False:
        return message+' Shall I resume the existing queue?'
    return message
