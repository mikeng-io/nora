"""Every branch of the pure decision, asserted on behaviour."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nora.infra.social.session_probe.decide import (
    Alert,
    BridgeState,
    Capped,
    ChromeState,
    CookieInfo,
    ErrorAction,
    History,
    LiveCheck,
    Snapshot,
    SyncBridge,
    decide,
    next_attempts,
    verdict,
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
GOOD = CookieInfo(NOW - timedelta(days=10), NOW + timedelta(days=200))


def chrome(**over: CookieInfo | None) -> ChromeState:
    base: dict = {"instagram": GOOD, "x": GOOD, "linkedin": GOOD}
    base.update(over)
    return ChromeState(True, base)


def snap(
    *, ch: ChromeState | None = None, bridge: str = "authenticated", differs: bool = False,
    attempts: int = 0, live: dict | None = None,
) -> Snapshot:
    return Snapshot(
        NOW, ch or chrome(), BridgeState(bridge), differs,  # type: ignore[arg-type]
        History(attempts), live or {},
    )


def test_all_healthy_is_pass_with_no_actions() -> None:
    actions = decide(snap())
    assert actions == []
    assert verdict(actions) == "PASS"


def test_unreadable_chrome_is_error_and_never_syncs_or_passes() -> None:
    actions = decide(snap(ch=ChromeState(False), bridge="logged_out", differs=True))
    assert actions == [ErrorAction("chrome_cookies_unreadable")]
    assert verdict(actions) == "ERROR"


def test_unreachable_bridge_is_error() -> None:
    actions = decide(snap(bridge="unreachable"))
    assert actions == [ErrorAction("bridge_unreachable")]
    assert verdict(actions) == "ERROR"


def test_both_unreadable_reports_both() -> None:
    actions = decide(snap(ch=ChromeState(False), bridge="unreachable"))
    assert {a.reason for a in actions if isinstance(a, ErrorAction)} == {
        "chrome_cookies_unreadable", "bridge_unreachable"}


def test_error_outranks_a_concurrent_alert() -> None:
    actions = decide(snap(ch=chrome(x=None), bridge="unreachable"))
    assert verdict(actions) == "ERROR"
    assert Alert("x", "chrome_session_missing") in actions


def test_missing_session_in_chrome_alerts_per_network() -> None:
    actions = decide(snap(ch=chrome(linkedin=None)))
    assert actions == [Alert("linkedin", "chrome_session_missing")]
    assert verdict(actions) == "FAIL"


def test_expired_session_in_chrome_alerts() -> None:
    dead = CookieInfo(NOW - timedelta(days=400), NOW - timedelta(seconds=1))
    assert decide(snap(ch=chrome(x=dead))) == [Alert("x", "chrome_session_expired")]


def test_near_expiry_alerts_but_far_expiry_does_not() -> None:
    soon = CookieInfo(None, NOW + timedelta(hours=71))
    edge = CookieInfo(None, NOW + timedelta(hours=72))
    assert decide(snap(ch=chrome(x=soon))) == [Alert("x", "chrome_session_near_expiry")]
    assert decide(snap(ch=chrome(x=edge))) == []


def test_browser_session_cookie_without_expiry_is_not_an_alert() -> None:
    assert decide(snap(ch=chrome(x=CookieInfo(None, None)))) == []


def test_chrome_newer_than_bridge_logged_out_syncs() -> None:
    actions = decide(snap(bridge="logged_out", differs=True))
    assert actions == [SyncBridge(1)]
    assert verdict(actions) == "FAIL"


def test_checkpointed_with_different_cookie_syncs_too() -> None:
    assert decide(snap(bridge="checkpointed", differs=True)) == [SyncBridge(1)]


def test_authenticated_bridge_is_left_alone_even_if_cookies_differ() -> None:
    assert decide(snap(bridge="authenticated", differs=True)) == []


def test_logged_out_with_identical_cookie_alerts_and_does_not_sync() -> None:
    actions = decide(snap(bridge="logged_out", differs=False))
    assert actions == [Alert("instagram", "bridge_logged_out_same_cookie")]


def test_checkpointed_with_identical_cookie_alerts_and_does_not_sync() -> None:
    actions = decide(snap(bridge="checkpointed", differs=False))
    assert actions == [Alert("instagram", "bridge_checkpointed_same_cookie")]


def test_no_sync_when_chrome_has_no_usable_instagram_session() -> None:
    actions = decide(snap(ch=chrome(instagram=None), bridge="logged_out", differs=True))
    assert actions == [Alert("instagram", "chrome_session_missing")]


def test_no_sync_when_live_check_says_chrome_itself_is_logged_out() -> None:
    actions = decide(snap(bridge="logged_out", differs=True,
                          live={"instagram": LiveCheck.LOGGED_OUT}))
    assert SyncBridge(1) not in actions
    assert actions == [Alert("instagram", "live_check_logged_out")]


def test_sync_attempts_count_up_then_cap_at_three() -> None:
    assert decide(snap(bridge="logged_out", differs=True, attempts=1)) == [SyncBridge(2)]
    assert decide(snap(bridge="logged_out", differs=True, attempts=2)) == [SyncBridge(3)]
    capped = decide(snap(bridge="logged_out", differs=True, attempts=3))
    assert capped == [Capped(3), Alert("instagram", "sync_capped")]
    assert not any(isinstance(a, SyncBridge) for a in capped)
    assert verdict(capped) == "FAIL"


def test_live_check_error_is_error_not_logged_out() -> None:
    actions = decide(snap(live={"x": LiveCheck.ERROR}))
    assert actions == [ErrorAction("live_check_error:x")]
    assert verdict(actions) == "ERROR"


def test_live_logged_in_and_not_run_add_nothing() -> None:
    assert decide(snap(live={"x": LiveCheck.LOGGED_IN, "linkedin": LiveCheck.NOT_RUN})) == []


def test_live_logged_out_alerts_even_with_a_present_cookie() -> None:
    assert decide(snap(live={"linkedin": LiveCheck.LOGGED_OUT})) == [
        Alert("linkedin", "live_check_logged_out")]


# ---- the counter carried between runs -------------------------------------

def test_counter_resets_when_the_bridge_recovers() -> None:
    assert next_attempts(2, "aaaaaaaa", "aaaaaaaa", "authenticated", False) == 0


def test_counter_increments_on_a_sync_that_did_not_recover() -> None:
    assert next_attempts(1, "aaaaaaaa", "aaaaaaaa", "logged_out", True) == 2


def test_counter_restarts_when_a_human_changed_chrome_cookie() -> None:
    assert next_attempts(3, "aaaaaaaa", "bbbbbbbb", "logged_out", True) == 1
    assert next_attempts(3, "aaaaaaaa", "bbbbbbbb", "logged_out", False) == 0


def test_counter_holds_when_bridge_unreachable_and_no_sync() -> None:
    assert next_attempts(2, "aaaaaaaa", "aaaaaaaa", "unreachable", False) == 2


def test_unreadable_chrome_never_resets_the_counter() -> None:
    # chrome_hash None = Chrome could not be read: no evidence the cookie changed.
    assert next_attempts(3, "aaaaaaaa", None, "logged_out", False) == 3


def test_counter_sequence_across_an_unreadable_run_still_reaches_the_cap() -> None:
    last, n, seen = None, 0, []
    for chrome_hash, synced in [("a", True), ("a", True), ("a", True), (None, False), ("a", True)]:
        n = next_attempts(n, last, chrome_hash, "logged_out", synced)
        last = chrome_hash if chrome_hash is not None else last
        seen.append(n)
    assert seen == [1, 2, 3, 3, 4]
