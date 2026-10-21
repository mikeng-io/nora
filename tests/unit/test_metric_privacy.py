"""Synthetic identity keys must never enter a Prometheus registry or HTTP response."""

from dataclasses import replace

import pytest

from nora.analytics.__main__ import _metrics_app, _tick
from nora.analytics.composition import _subjects_from_identity
from nora.obs.metric_specs import _COUNTERS, _GAUGES, _HISTOGRAMS
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from tests.unit.test_analytics_main import _deps, _FakeRunner

# Independent of the registry: these were the identity-bearing metrics in the
# audit, including the less obvious dream scope key (a room id).
COUNTERS = {
    "nora_analytics_stays_total": {},
    "nora_analytics_places_total": {"kind": "home"},
    "nora_analytics_unlabeled_total": {},
    "nora_analytics_occupancy_bins_total": {},
    "nora_analytics_anomalies_total": {"kind": "novelty"},
    "nora_analytics_trips_total": {},
    "nora_analytics_corridors_total": {"outcome": "created"},
    "nora_presence_fixes_total": {"wire_state": "live"},
    "nora_presence_reads_total": {"status": "ok"},
    "nora_presence_place_reads_total": {"status": "ok"},
    "nora_music_liveness_total": {"verdict": "wedged"},
    "nora_music_stream_failed_total": {},
    "nora_dream_home_room_missing_total": {"adaptor": "culture"},
    "nora_dream_evidence_lost_total": {},
    "nora_relevance_gate_total": {"verdict": "no_go", "platform": "signal"},
    "nora_relevance_gate_errors_total": {"kind": "timeout", "platform": "signal"},
}
GAUGES = {
    "nora_analytics_rollup_age_seconds": 300,
    "nora_presence_last_collection_age_seconds": 300,
    "nora_presence_circle_subset_ok": 0,
    "nora_note_index_entries": 302,
    "nora_stimulus_pending_age_seconds": 300,
    "nora_music_expected_playing": 302,
    "nora_music_controller_playing": 302,
}
PRIVATE_KEYS = (
    "subject", "circle", "unit", "conversation", "conversation_id", "guild_id", "scope",
    "person_id", "account_id", "room_id", "room_label",
)


async def test_private_labels_never_reach_metrics_endpoint(aiohttp_client):
    metrics = Metrics()
    sentinels = []
    for i, value in enumerate((300, 2)):
        private = {key: f"synthetic_private_{key}_{i}" for key in PRIVATE_KEYS}
        sentinels.extend(private.values())
        for name, public in COUNTERS.items():
            extra = {}
            if name == "nora_music_stream_failed_total":
                extra["reason"] = f"synthetic_private_controller_reason_{i}"
                sentinels.append(extra["reason"])
            metrics.inc(name, value=i + 1, **public, **private, **extra)
        for name in GAUGES:
            # A bad subset followed by a healthy room must remain bad.
            reading = i if name == "nora_presence_circle_subset_ok" else value
            metrics.gauge(name, reading, platform="signal", **private)
    client = await aiohttp_client(_metrics_app(_deps(_FakeRunner(fail=set()), metrics)))
    response = await client.get("/metrics")
    assert response.status == 200
    text = await response.text()
    for sentinel in sentinels:
        assert sentinel not in text
    for spec in (*_COUNTERS, *_GAUGES, *_HISTOGRAMS):
        assert not set(spec.labelnames).intersection(PRIVATE_KEYS), spec.name
    for name, public in COUNTERS.items():
        assert metrics.get(name, **public) == 3, name
    for name, expected in GAUGES.items():
        assert metrics.get(name) == expected, name
    # Updating an existing entity replaces its reading rather than accumulating.
    metrics.gauge("nora_note_index_entries", 5, unit="synthetic_private_unit_0")
    assert metrics.get("nora_note_index_entries") == 7
    metrics.gauge("nora_presence_circle_subset_ok", 1, platform="signal",
                  circle="synthetic_private_circle_0",
                  conversation="synthetic_private_conversation_0")
    assert metrics.get("nora_presence_circle_subset_ok") == 1


async def test_identity_subjects_publish_max_age_and_failure_count(
    tmp_path, monkeypatch, aiohttp_client,
):
    from nora.analytics import __main__ as main

    identity = tmp_path / "identity.toml"
    identity.write_text('''
[persons.synthetic_private_person_a]
name = "Synthetic A"
[[persons.synthetic_private_person_a.life360]]
member_id = "synthetic_private_account_a"
circle_id = "synthetic_private_circle"
[persons.synthetic_private_person_b]
name = "Synthetic B"
[[persons.synthetic_private_person_b.life360]]
member_id = "synthetic_private_account_b"
circle_id = "synthetic_private_circle"
''')
    persons = load_persons(identity)
    subjects = _subjects_from_identity(persons)
    metrics = Metrics()
    failed = {subjects[0].subject_id}
    runner = _FakeRunner(fail=failed)
    deps = replace(_deps(runner, metrics), subjects=subjects, persons=persons)
    last, first = {}, {}
    monkeypatch.setattr(main.time, "time", lambda: 1000)
    await _tick(deps, last, first)
    monkeypatch.setattr(main.time, "time", lambda: 1300)
    await _tick(deps, last, first)
    assert metrics.get("nora_analytics_rollup_age_seconds") == 300
    assert metrics.get("nora_analytics_subjects") == 2
    assert metrics.get("nora_analytics_rollup_failures_total") == 2
    runner.explode = True
    await _tick(deps, last, first)
    assert metrics.get("nora_analytics_rollup_failures_total") == 4
    client = await aiohttp_client(_metrics_app(deps))
    assert "synthetic_private" not in await (await client.get("/metrics")).text()


@pytest.mark.parametrize("verdict,expected", [("wedged", 1), ("ended", 1), ("unknown", 0)])
def test_music_failure_count_cannot_be_hidden_by_a_healthy_guild(verdict, expected):
    from nora.domain.playback_liveness import PlaybackVerdict
    from nora.platform.discord.music_manager import MusicPlayerManager

    manager = object.__new__(MusicPlayerManager)
    manager._metrics = metrics = Metrics()
    manager._record_liveness("synthetic_private_guild_a", PlaybackVerdict(verdict))
    manager._record_liveness("synthetic_private_guild_b", PlaybackVerdict.OK)
    assert metrics.get("nora_music_wedged_guilds") == expected
    manager._record_liveness("synthetic_private_guild_a", PlaybackVerdict.OK)
    assert metrics.get("nora_music_wedged_guilds") == 0
    assert "synthetic_private" not in metrics.render()
