from __future__ import annotations

from nora.agent.toolkit.spec import _spec


def build(deps):
    from nora.agenda.adapters.tools.reminders import _make_remind

    remind = _make_remind(deps)
    from nora.agenda.adapters.tools.reminders import _make_list_reminders

    list_reminders = _make_list_reminders(deps)
    from nora.agenda.adapters.tools.reminders import _make_cancel_reminder

    cancel_reminder = _make_cancel_reminder(deps)
    from nora.agenda.adapters.tools.events import _make_event_search

    event_search = _make_event_search(deps)
    from nora.agenda.adapters.tools.events import _make_event_propose

    event_propose = _make_event_propose(deps)
    from nora.agenda.adapters.tools.events import _make_event_revise

    event_revise = _make_event_revise(deps)
    from nora.agenda.adapters.tools.events import _make_event_rsvp

    event_rsvp = _make_event_rsvp(deps)
    from nora.agenda.adapters.tools.events import _make_event_set_status

    event_set_status = _make_event_set_status(deps)
    from nora.agenda.adapters.tools.events import _make_suggest_venue

    suggest_venue = _make_suggest_venue(deps)
    return (
        _spec("remind", remind),
        _spec("list_reminders", list_reminders),
        _spec("cancel_reminder", cancel_reminder),
        _spec("event_search", event_search),
        _spec("event_propose", event_propose),
        _spec("event_revise", event_revise),
        _spec("event_rsvp", event_rsvp),
        _spec("event_set_status", event_set_status),
        _spec("suggest_venue", suggest_venue),
    )
