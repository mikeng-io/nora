# bridge-projection

Standalone publisher for the bridge admission projection. One long-lived
process — independent of the `nora` app — loads `config.toml` + `policy.toml`
+ `identity.toml` through the **same** loaders the app composes (no second
authorization parser), and publishes `{platform}.json` into a shared volume
that every platform bridge mounts read-only. A bridge uses its file to
pre-filter capture: denied rooms drop no body/attachment traffic; granted
scopes admit capture without waiting for the app.

## Layout and contract

```
/bridge-projection/{platform}.json   # atomically written (tmp + fsync + rename + dir-fsync)
```

File shape (the frozen `nora-bridge-wire` contract):

```json
{
  "enabled": true,
  "account_key": "opaque-stream-leg",
  "generated_ms": 1750000000000,
  "revision": 42,
  "rooms": ["conv-1", "conv-2"],
  "room_receipt_senders": {"conv-1": ["+85290000102"], "conv-2": ["*"]},
  "dm_allowed_senders": ["+85290000103"],
  "self_aliases": ["+85290000104"],
  "ack_emoji": "✅",
  "music_channels": {"guild-1": "chan-9"}
}
```

- `rooms` — admitted conversation ids, sorted. Absent = denied = no
  body/attachment capture. Discord guild-wide entries expand through the
  bridge's live channel directory (`POST /v1/reads`).
- `room_receipt_senders` — per-room receipt grants: listed platform sender
  ids, or `["*"]` only when the policy grants the room a wildcard sender
  class. A room missing from this map grants no receipts — denied senders
  may still be *absorbed* (room content captured, no engage/receipt grant).
- `dm_allowed_senders` — explicit DM admission set (platform ids).
- `self_aliases` — live `/v1/self` ∪ configured account. Empty/degraded
  upstream identity fails closed for self-reply eligibility; room capture
  still publishes. Signal keeps the configured-account floor.
- `music_channels` — routing-only metadata for the Discord bridge's
  notification path (`Policy.music_channel_for` per guild). Never an
  admitted room; `{}` for every other platform.
- `revision` — content hash bounded to `[0, 2^53-1]` (JS-exact integers).
- `enabled:false` artifacts still carry the contract's deny shape; consumers
  treat the file as authoritative either way.

The publisher writes the file only when the semantic truth changes
(`generated_ms` is write-time provenance, not part of the comparison).

## Modes

```
# Fleet mode — one process hosts every platform's publisher (the deployment's
# shape: a single bridge-projection service feeding all four bridges).
entry.py --config /app/config.toml --all --projection-dir /bridge-projection --health-port 8091

# Single mode — the original one-platform shape, kept for compatibility.
entry.py --config /app/config.toml --platform signal \
    --projection-dir /bridge-projection --health-port 8091
```

`--all` degrades per platform: one platform's missing/mismatched leg,
unreachable bridge, or invalid projection denies **that platform only** —
its own `enabled:false`/stale file — while the others keep publishing.

## Environment

| Variable | Mode | Meaning |
|---|---|---|
| `SIGNAL_BRIDGE_ACCOUNT_KEY` etc. | `--all` | Per-platform opaque stream leg. Must equal `[platforms.<p>] account_key` in config.toml when that platform is enabled — a mismatch publishes a deny artifact and reports `key_mismatch` stale. A **disabled** platform needs no leg. |
| `NORA_BRIDGE_ACCOUNT_KEY` | both | Fallback leg when the per-platform env is unset (single mode: the primary source). Refuses rc=2 if missing/blank in `--platform` mode — a guessed leg is a guessed grant. |
| `NORA_BRIDGE_API_TOKEN` (fallback `NORA_BRIDGE_TOKEN`) | both | Bearer for `/v1/self` and `/v1/reads` calls. |
| `<P>_BRIDGE_SELF_URL` / `NORA_BRIDGE_SELF_URL` | both | Explicit `/v1/self` endpoint; else derived from the platform's `base_url` (env `*_BRIDGE_BASE_URL`/`NORA_BRIDGE_BASE_URL`, then `[platforms.<p>] base_url`), else `NORA_BRIDGE_PORT` loopback. |
| `<P>_BRIDGE_READS_URL` / `NORA_BRIDGE_READS_URL` | both | Explicit `POST /v1/reads` endpoint for the guild channel directory; same base_url fallback chain. |
| `NORA_BRIDGE_PROJECTION_DIR` | both | Output dir (default `/bridge-projection` in the image). |
| `NORA_BRIDGE_PROJECTION_PORT` | both | Health port (alternative to `--health-port`). |

Config/policy/identity are mounted **read-only**; the publisher never opens
`secret.toml` (`require_secrets=False`) and holds no credentials of its own.

## Endpoints (`--health-port`)

| Route | Meaning |
|---|---|
| `GET /live` | Process liveness — always 200 while the process is up. Docker's healthcheck uses this: a stale projection must never trigger a restart (the file already denies; restarting fixes nothing). |
| `GET /health` | Authorization truth. Fleet mode: `{"ok": bool, "platforms": {p: status}}`, **200 only when every running publisher is fresh**. Single mode: that platform's status, 503 while stale. |
| `GET /metrics` | Shared Prometheus registry. Each platform's failures degrade only its own projection. |

Staleness is honest: an invalid reload keeps the last valid file on disk and
reports `stale`; a failed write marks write-staleness until a new truth is
verified on disk; a missing channel directory denies guild expansion rather
than pretending empty scope. First boot with invalid config writes an
explicit `enabled:false` deny artifact.

## Deployed shape

Compose service `bridge-projection` (see root `docker-compose.yml`): private
`nora` network only, no published ports, `/app/config.toml` +
`/app/policy.toml` + `/app/identity.toml` read-only, named volume
`bridge-projection` read-write here and `:ro` in all four bridge services.
No `depends_on` — it must outlive the app and publish while the app is
stopped. The `/app` mount targets mirror `nora`'s so the deploy wrapper's
config-reload reader detection already includes it.

Release: `scripts/release_bundle.py` builds it as `ghcr.io/mikeng-io/nora-
bridge-projection` (context `.`, Dockerfile here). Its build input is the
transitive import closure of `entry.py` — an app-only source change keeps
its digest and leaves the container running.
