"""In-process metrics recording (spec C.9).

M4/M5 record through this interface; this module now also exports Prometheus text
format directly, with no call-site changes (spec §9, audit fix #11).
"""

from __future__ import annotations

from collections import defaultdict

DECLARED: dict[str, tuple[str, str]] = {}
_BUCKETS: dict[str, tuple[float, ...]] = {}
_DEFAULT_BUCKETS: tuple[float, ...] = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30)


def _key(name: str, labels: dict) -> tuple:
    return (name, tuple(sorted((k, str(v)) for k, v in labels.items())))


def _escape(value) -> str:
    text = str(value)
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_labels(label_pairs, extra: dict | None = None) -> str:
    pairs = list(label_pairs)
    if extra:
        pairs = pairs + list(extra.items())
    if not pairs:
        return ""
    inner = ", ".join(f'{k}="{_escape(v)}"' for k, v in pairs)
    return "{" + inner + "}"


def _format_number(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _format_bound(bound) -> str:
    if isinstance(bound, float) and bound.is_integer():
        return str(int(bound))
    return str(bound)


class Metrics:
    def __init__(self) -> None:
        self._values: dict[tuple, float] = defaultdict(float)
        self._observations: dict[tuple, list[float]] = defaultdict(list)

    def inc(self, name: str, value: float = 1.0, **labels) -> None:
        self._values[_key(name, labels)] += value

    def gauge(self, name: str, value: float, **labels) -> None:
        self._values[_key(name, labels)] = value

    def observe(self, name: str, value: float, **labels) -> None:
        self._observations[_key(name, labels)].append(value)

    def get(self, name: str, **labels) -> float:
        return self._values.get(_key(name, labels), 0.0)

    def observations(self, name: str, **labels) -> list[float]:
        return list(self._observations.get(_key(name, labels), []))

    def snapshot(self) -> dict[tuple, float]:
        return dict(self._values)

    def reset(self) -> None:
        self._values.clear()
        self._observations.clear()

    @classmethod
    def declare(cls, name: str, kind: str, help_text: str, buckets=None) -> None:
        """Registers a series at import time so it's visible in /metrics before use."""
        DECLARED[name] = (kind, help_text)
        if kind == "histogram":
            _BUCKETS[name] = tuple(buckets) if buckets else _DEFAULT_BUCKETS

    def render(self) -> str:
        lines: list[str] = []
        for name, (kind, help_text) in DECLARED.items():
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            if kind == "histogram":
                lines.extend(self._render_histogram(name))
            else:
                lines.extend(self._render_value_series(name))
        return "\n".join(lines) + "\n"

    def _render_value_series(self, name: str) -> list[str]:
        matches = {k: v for k, v in self._values.items() if k[0] == name}
        if not matches:
            return [f"{name} 0"]
        return [
            f"{name}{_format_labels(labels)} {_format_number(value)}"
            for (_, labels), value in matches.items()
        ]

    def _render_histogram(self, name: str) -> list[str]:
        buckets = _BUCKETS.get(name, _DEFAULT_BUCKETS)
        groups: dict[tuple, list[float]] = {
            labels: values for (n, labels), values in self._observations.items() if n == name
        }
        if not groups:
            groups = {(): []}
        lines: list[str] = []
        for labels, values in groups.items():
            for bound in buckets:
                count = sum(1 for v in values if v <= bound)
                lines.append(
                    f"{name}_bucket{_format_labels(labels, {'le': _format_bound(bound)})} {count}"
                )
            lines.append(f"{name}_bucket{_format_labels(labels, {'le': '+Inf'})} {len(values)}")
            lines.append(f"{name}_sum{_format_labels(labels)} {_format_number(sum(values))}")
            lines.append(f"{name}_count{_format_labels(labels)} {len(values)}")
        return lines


Metrics.declare("nora_messages_total", "counter", "Ambient messages ingested, by platform.")
Metrics.declare("nora_tags_total", "counter", "Tags detected and accepted for handling.")
Metrics.declare("nora_tags_debounced_total", "counter", "Tags dropped by the debounce window.")
Metrics.declare("nora_tags_dropped_total", "counter", "Tags dropped for a reason other than debounce.")
Metrics.declare("nora_replies_total", "counter", "Delivery attempts, by outcome status.")
Metrics.declare("nora_errors_total", "counter", "Internal errors, by processing stage.")
Metrics.declare("nora_silent_failures_total", "counter", "Failures kept silent from the group, by reason.")
Metrics.declare("nora_guard_vetoes_total", "counter", "ReplyGuard vetoes, by rule.")
Metrics.declare("nora_leak_blocks_total", "counter", "Replies blocked by the persona/canary leak guard.")
Metrics.declare("nora_persona_reload_failures_total", "counter", "Persona hot-reload failures.")
Metrics.declare("honcho_feed_success", "counter", "Successful Honcho feed calls.")
Metrics.declare("honcho_feed_failure", "counter", "Failed Honcho feed calls.")
Metrics.declare("nora_tag_latency_seconds", "histogram", "End-to-end tag latency, including the model call.")
Metrics.declare("nora_llm_latency_seconds", "histogram", "Model invocation latency.")
Metrics.declare("nora_overhead_seconds", "histogram", "Tag latency excluding the model call.")
Metrics.declare("nora_last_event_age_seconds", "gauge", "Seconds since the last ingest event.")

METRICS = Metrics()
