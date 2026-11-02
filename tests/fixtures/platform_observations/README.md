# Platform observation fixtures — generated native protocol vectors

Generated test data for the platform-observation cluster
(`design/platform-observations/`). Each file is one **native wire event**,
pinned to the upstream version Nora actually runs, with the semantic fields
the observation contract (`02-observation-contract.md`) expects a capture
implementation to preserve. Nothing here is a production row: every id,
name and timestamp is generated. Denylist-clean placeholders only —
Discord snowflake-shaped ids keep a `1234567…` prefix, and Signal ids reuse
the generated sender/group values already committed by
`tests/test_signal_parser.py`.

## Pinned natives

| Directory | Native | Pinned at | Shape source |
|---|---|---|---|
| `signal/` | signal-cli **0.14.7** | `deploy/signal-cli/Dockerfile` `SIGNAL_CLI_VERSION` | `JsonDataMessage` + sibling records, `github.com/AsamK/signal-cli` at tag `v0.14.7` |
| `discord/` | Discord Gateway via **discord.py 2.7.1** | `pyproject.toml` `discord-py[voice]>=2.7.1`, locked 2.7.1 in `uv.lock` | Discord Gateway events; `discord/state.py` `parse_<event>` names |

## File format

```json
{
  "meta":      {"native", "native_version", "transport", "upstream"|"dispatch",
                "discord_py_parse"|"discord_py_event" (discord only),
                "upstream_source", "case", "generated": true},
  "payload":   the literal wire value,
  "semantics": {"object", "operation", "room_ref", "object_ref", "target_ref",
                "actor", "source_time", "native_fields", "notes", "today"}
}
```

- `payload` is what the transport hands capture, verbatim:
  - **Signal**: one SSE `data:` line JSON — `{"envelope": …, "account": …}` —
    exactly what `nora.platform.signal.parser.parse_event` accepts (the HTTP
    `GET /api/v1/events` framing; `receive` was stripped by the SSE event
    field).
  - **Discord**: one Gateway dispatch frame `{"op": 0, "t": <name>,
    "s": <seq>, "d": <payload>}` — `d` is what
    `discord.state.ConnectionState.parse_<snake>` receives, and the raw
    `Raw*Event` DTOs are built from it.
- `semantics.operation` uses the contract vocabulary: `create`, `revise`,
  `withdraw`, `add`, `remove`, `clear`, `snapshot`, `ignore`.
- `semantics.today` records the outcome on the current parser at this
  branch's base — a gap is stated, not assumed. `…_dm` files are the
  admission-boundary shapes (no `groupInfo` / no `guild_id`).

## What the shapes establish (and what they do not)

Upstream capability is not capture, storage or model visibility. These
vectors prove the **native operation exists on the pinned transport and what
it carries**; each `semantics.today` line records where the present pipeline
drops it. Known upstream limits the fixtures encode:

- Signal room change arrives as `groupInfo.type:"UPDATE"` carrying only the
  new name and revision — never which fact changed.
- Signal `adminDelete` is moderation evidence: actor ≠ message author, unlike
  `remoteDelete` (self-delete).
- Discord `CHANNEL_PINS_UPDATE` names no message and no actor; the type-6
  `CHANNEL_PINNED_MESSAGE` system message is the only per-message pin
  evidence.
- Discord `GUILD_MEMBER_*`/`CHANNEL_UPDATE`/`GUILD_ROLE_UPDATE` carry the
  AFTER object — deltas are a diff against captured state, not a field.
- Discord polls and reactions are never anonymous; a `MESSAGE_UPDATE` with
  `poll.results.is_finalized` is the only poll-close delivery (no dedicated
  event), and it is not an edit.
- Receipt-time-only events (reaction add/remove/clear, poll votes, member
  remove) carry no source time; ordering is the Gateway `s` sequence —
  session-scoped, reset on reconnect.

Validated by `tests/unit/test_platform_observation_fixtures.py`, which checks
each vector against the existing parsers and the pinned `discord.py`
`parse_*`/`Raw*Event` seam — it does not re-assert the fixture's own
`semantics` text.
