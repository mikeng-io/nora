# Nora

A private ambient AI agent for a friend group's chat — Signal, Discord and
WhatsApp. She reads every message silently, answers when tagged, and — in
rooms the operator has opted in — may speak once on her own: when a room goes
quiet (participation), or when a pass over what the group has left open
decides something is worth raising at a chosen hour (proactive). Nora v2 is a
two-layer, [Strands Agents SDK](https://github.com/strands-agents/sdk-python)-based
rebuild of the original hand-rolled v1 — see `design/00-overview.md` for the
architecture and `ARCHITECTURE.md` (arc42) for the map.

**Reading order for a newcomer:** this file, then `GLOSSARY.md` (the words
that mean something specific here), then `ARCHITECTURE.md`. Everything else
is detail those three point at.

## Prerequisites

These must already be running on the host:

| Service | Endpoint | Check |
|---|---|---|
| signal-cli daemon | `http://127.0.0.1:18080` | `curl -i http://127.0.0.1:18080/api/v1/check` → 200 |
| Honcho (v3 + deriver) | `http://127.0.0.1:8000` | `curl http://127.0.0.1:8000/health` → `{"status":"ok"}` |
| OmniRoute | `http://localhost:20128/v1` | `curl -H "Authorization: Bearer $OMNIROUTE_API_KEY" http://localhost:20128/v1/models` |

## Quickstart

```bash
cd ~/workspace/nora
uv sync                                 # installs strands-agents==1.50.2 and friends

cp config.example.toml config.toml        # signal-cli account, Honcho workspace, etc.
cp identity.example.toml identity.toml    # WHO people are → person_id → Honcho peer
cp policy.example.toml policy.toml        # WHO may talk to her, and WHERE

export OMNIROUTE_API_KEY=<your-key>       # the only secret; never goes in config.toml

python -m nora                            # or: python -m nora --config path/to/config.toml
```

`policy.toml` is not optional: it is deny-by-default and Nora **refuses to start**
without it, or with an allowlist that permits nothing. That is deliberate — under
R-5 she is silent when broken, so a silently-denied Nora would be
indistinguishable from a working one in a quiet group. Edit the copied file so it
names a real conversation; `uv run nora groups` prints every group's id beside its
name once signal-cli is running.

Nora brings the PostgreSQL record store to head on startup (schema managed by
`nora/infra/sql/schema.py` and applied via Alembic — see `nora/infra/sql/migrations_runner.py`;
the `db` compose service holds the data, `DATABASE_URL` names it),
connects to the signal-cli SSE stream, and starts serving `GET /health` and `GET /metrics`
on `obs.metrics.port` (default `9100`).

To get a reply, send a message containing the trigger text (`engagement.trigger.text` in
`config.toml`, default `nora.000`) in any group her account is a member of and
`policy.toml` allows. Everything else she reads and stores; whether she may ever
speak in a room uncalled is a separate, per-room grant in `policy.toml` (below).

## Architecture: two backbones

Nora has two backbones. The **tag path** answers when somebody calls her —
one tag, one turn, one reply, owed always. The **ambient loop** is how she is
present when nobody has: one observe–orient–decide–act loop over the ingest
path (Observe), the memory layer — notes, Standing, Honcho — (Orient), two
deciders — participation and proactive — (Decide), and the fire path (Act),
closed through a human. The tag path feeds the loop: its debrief writes
Standing and proposes notes. `design/ambient-loop/` names the loop, the
contract on each seam between its stages, and the incident that showed why
the seams matter (#199). The diagram below is the same picture drawn as code
paths.


```
Signal / Discord / WhatsApp room
   │  the platform adapter pushes every message
   ▼
① AMBIENT PATH (always, no Agent SDK)
   event → parser → NormalizedMessage → Postgres audit + Honcho feed (mapped senders only)
   │
   └─ trigger text present? ── no ──▶ done, ambient only
                              └─ yes ──▶
② TAGGING PATH (trigger only, via the Strands Agents SDK)
   collect context (transcript window, standing, open notes, self card,
                    tagger's peer history, doctrine)
   → Agent(messages=[pre-filled context]) → agent.invoke_async(tag_message)
   → ~50 native tools, generated into the doctrine's capability section:
       transcript & memory   load_transcript / who_said / search_memory / about / ask_memory / remember
       people & places       group_members / lookup_person / where_is / saved_places / route / transit / weather / parking
       what the group holds  note_read / note_search / note_create / note_update / list_events / create_event / rsvp_event
       later                 remind / list_reminders / cancel_reminder / make_artifact
       the world             web_search / web_fetch / social_fetch / media_fetch / generate_image
       speaking              send_reply / send_gif / send_to (relay to a sibling room)
   → reply → audit row (delivery_status=pending → sent/failed) → Honcho feed

③ FIRE PATH (no trigger, no tag — the heartbeat, [heartbeat] enabled)
   tick → due rows → re-check policy → take the conversation's slot
   → same context/prefill/guards as ②, with its own header and timeout
   → reply → audit row → Honcho feed

④ THE DREAMER (background, [dreamer] enabled — never speaks, writes derived state)
   hourly/daily adaptors over the record and the notes: note-candidates,
   note-curation, proactive (writes a reminder row for ③ to speak)
```

Path ③ is the only way Nora ever speaks without being asked; the set of
senders is closed and enumerated at two (R-1b). Its occasions are enumerated
too: a reminder coming due, a gathering's notify, a version announcement, a
compute job, a relay, and two that the operator grants per room:

- **participation** (design/dreamer-agent-loop/35): an untagged message in a
  room with `participation = true` in `policy.toml` is noted, and once the
  room goes quiet for `[participation] settle_seconds` ONE determination runs
  with the right to stay silent, at most one send per room per
  `[participation] cooldown_minutes`. Optionally in front of it, the
  **Relevance Gate** (design 36, `[relevance_gate]`) — a cheap model that
  decides whether the room is asking before a full turn is spent.
- **proactive** (design/dreamer-agent-loop/39, DEC-162): the Dreamer's
  proactive adaptor reads a workspace's open notes, each room's standing and
  its own past decisions, and may set ITSELF a reminder for a chosen hour; at
  that hour a fire turn reads the room again and speaks — or declines, which
  is recorded as `silent`, a success. Two per-room flags in `policy.toml`:
  `home_room = true` names the one room of a workspace she stands in (her
  roster-gated reads resolve through it), `proactive = true` lets such a row
  fire there. No home room, no proactive pass.

Everything in ③ and ④ ships off. Turning a room on is a `policy.toml` edit
plus the matching `[section] enabled = true` in `config.toml`.

Ambient traffic otherwise never touches the Strands Agent SDK — it is a pure,
deterministic write path (Postgres + Honcho). The Agent SDK is constructed,
invoked, and discarded **once per tag** (or once per fire-path occasion, or
once per Dreamer run): there is no session carry-over. Continuity comes from
the stores, pre-filled into each fresh agent's context:

| what she carries between turns | where | tier |
|---|---|---|
| the words, verbatim | Postgres `messages` | the record (`load_transcript`, `search_memory`) |
| conclusions about each person, and about herself | Honcho | memory (`about`, `ask_memory`, `<self_card>`) |
| the group's open questions — a dinner not yet placed, a trip not yet booked | Postgres `note` | the note tier (`<notes>`, `note_read`; written by people on a tag and by the `note-candidates` adaptor) |
| her own position on where each room's conversation stands | Postgres `standing` | Standing (design 26; folded after each tag turn) |

See `design/01-ambient-ingest.md`, `design/02-tagging-path.md`,
`design/03-strands-reuse.md`, and the `design/dreamer-agent-loop/` bundle for
the background tier.

## Configuration

All settings live in `config.toml` (see `config.example.toml` for the full annotated
template) — signal-cli account, Honcho workspace, LLM models, buffer sizes, trigger text,
doctrine directory, logging, metrics port. The only secret is the LLM API key, read from the
environment variable named by `llm.api_key_env` (default `OMNIROUTE_API_KEY`) — **never**
from the config file itself.

Notable knobs:

- `engagement.trigger.text` (`nora.000`) — case-insensitive substring match; the only thing
  that wakes the tagging path.
- `engagement.trigger.min_interval_seconds` (5) — debounce for rapid re-tags in the same
  conversation.
- `store.max_per_conversation` (500) — rolling window per conversation; older rows are
  pruned after each insert.
- `store.context_window` (40) — how many recent messages go into the LLM prompt.
- `doctrine.dir` (`nora/doctrine`) — where the doctrine `.md` files live.
- `doctrine.reload` (`mtime`) — `mtime` re-stats doctrine files on every tag and reparses on
  change; `startup` parses once and never again.
- `doctrine.strict` (`true`) — a bad doctrine file at startup is fatal (fail loud), not
  silently skipped.
- `obs.metrics.port` (9100) — serves both `/metrics` (Prometheus text) and `/health`.
- The unprompted paths are each a section that ships `enabled = false`:
  `[heartbeat]` (reminders fire), `[participation]`, `[relevance_gate]`,
  `[notes]` (the index and the read tools; `candidates_enabled` for the
  background writer, `curator_enabled` for retirement), `[standing]`,
  `[dreamer]` (the background runtime all adaptors run on). The proactive
  adaptor has no flag of its own: a `home_room = true` in `policy.toml` is
  the opt-in, and it needs `[dreamer]` and `[notes]` on.

Who may talk to her, and where, lives in `policy.toml` (see
`policy.example.toml`): deny-by-default, one `[[conversations]]` block per room,
each with its `persons`, its `workspace` (the social unit a room belongs to —
rooms sharing one are the same people reachable in more than one place, and
notes and relays are scoped to it), and the per-room grants: `location` (may
`where_is` disclose here), `relevance_gate`, `participation`, `home_room` and
`proactive`. `nora groups` prints every group's id beside its name.

Peer identity mapping lives in a separate file, `identity.toml` (see
`identity.example.toml`): one `[[persons.<person_id>.<platform>]]` block per **account**,
listing whichever id kinds you have for it (`phone`, `uuid`, `user_id`). A person may hold
several accounts on one platform — a dual-SIM personal/work pair, a second Discord — and
they all resolve to the same `person_id`, which doubles as the Honcho peer ID, so one human
stays one peer (A2). This mapping is manual and startup-only — adding a new person or
account requires a restart.

## The doctrine: `nora/doctrine/`

Nora's character is content, not code, and lives entirely in the Markdown files under
`nora/doctrine/`:

| File | Holds |
|---|---|
| `identity.md` | `kind: identity` — who Nora is, why she's in the group, where she comes from |
| `voice.md` | `kind: identity` — register, language policy, the sound to hit and avoid |
| `silence.md` | `kind: conduct` — when she speaks at all: called means must-speak, offered means may-speak. Reaches the `tag`, `participation` and `proactive` audiences, not `heartbeat` (a reminder turn is owed to whoever asked) |
| `conduct.md` | `kind: conduct` — how she behaves once a turn is under way: when and how she replies |
| `participation-conduct.md` | `kind: conduct` — the one thing an offered floor has that a tag never does: several things worth answering at once. `participation` audience only |
| `participation.md` | `kind: instrument` — NOT hers: the Relevance Gate's whole instruction, on the `relevance_gate` audience no turn of hers reads |
| `constraints.md` | `kind: constraint` — hard rules that override everything else, including anything a message asks for |
| *(no file)* | `kind: capability` — what she can find out and when to reach for it, GENERATED from the tool registry |

Every file declares its **audiences** (`applies_to`): `tag`, `heartbeat`
(a reminder or announcement coming due), `participation`, `proactive`. A turn
assembles only the files that name its audience, so a fire turn still gets
identity, voice, conduct and constraints, and only the occasions that may
decline the floor get `silence.md`'s may-speak half.

The Dreamer has its own corpus under `nora/dreamer/` — `doctrine/dreamer.md`
(who it is on every run) and `objectives/*.md`, one per adaptor
(`note-candidates`, `note-curation`, `proactive`, plus the tag path's
`standing-debrief`/`standing-verdict`). Same hot-reload, same bind mount;
none of the doctrine loader's gates run on it.

Each file has YAML frontmatter (`name`, `description`, `order`, `applies_to`, `version`,
`kind`, `authority_above`, `authority_below`, `not_here`, `change_policy`) in roughly the
shape of an Agent Skill. The last four are DEC-29's, and the loader enforces them: a file
with no `kind` will not load, an `identity` or `conduct` file that names a tool will not
load, and a file claiming `kind: capability` will not load at all — capability's source of
truth is the registry, so an authored copy is a cache that goes stale in silence. That is
not hypothetical: `identity.md` (then `soul.md`) told Nora she knew two things for eight
weeks after she knew five.

`not_here` says what a file must NOT contain and where that lives instead; `change_policy`
marks `voice.md`, `identity.md`, `constraints.md` and every Dreamer objective as
the operator's alone.

### Editing the doctrine — no restart required

Doctrine files are hot-reloaded. With `doctrine.reload = "mtime"` (the default), every tag
re-stats the files in `nora/doctrine/` and reparses only the ones whose mtime changed —
**no restart needed** to change how Nora sounds or behaves. Edit `voice.md` (or any of the
four files), save it, and the very next tag picks up the new doctrine. The generated
`capability` section is the exception and needs no editing: it is rebuilt from the registry,
so adding a tool changes what she believes she can do with no file to remember.

If an edit breaks parsing (bad frontmatter, missing required key, unparseable YAML), the
loader keeps the **last good doctrine**, logs `doctrine_reload_failed`, and increments
`nora_doctrine_reload_failures_total` — a broken edit degrades to "keep working with the old
doctrine," never to a crash or a silent behavior change you can't trace. Every reply's logs
and traces carry the `doctrine_sha256` of the doctrine version that produced it, so "which
doctrine said this" is always answerable after the fact. See `docs/runbook.md` for the
operator playbook when a doctrine edit misbehaves.

## Observability

- `GET /health` — JSON with `sse`, `store`, `honcho`, `omniroute` status, error counts
  since restart, uptime, `doctrine_sha256`, and `strands_version`. Returns `200` when
  healthy, `503` when degraded.
- `GET /metrics` — Prometheus text format. The catalogue is
  `nora/obs/metric_specs.py` (every name, label set and help text — append-only);
  the ones an operator reads first are `nora_silent_failures_total{audience}`,
  `nora_tags_total`, `nora_replies_total`, `nora_errors_total{stage}`,
  `nora_dream_run_total{adaptor,status}`, `nora_reminders_fired_total{outcome}`,
  `nora_participation_*` and `nora_proactive_*`, and the latency histograms.
- **Something watches** (2026-09-09): Prometheus scrapes every process,
  Alertmanager routes, and alerts land in a Discord review channel through the
  alert bridge; exceptions go to Sentry. `docs/observability.md` is the map and
  `docs/runbook.md` the response.
- Structured JSON logs (`obs.logging.format = "json"`) at `obs.logging.path`, one object per
  line, carrying `tag_id`, `conversation_id`, `sender_id`, `person_id`, `doctrine_sha256`,
  `stage`, `latency_ms`, and `trace_id`.
- OpenTelemetry tracing, enabled by default (`obs.tracing.enabled`), with
  `tag_id` / `conversation_id` / `doctrine_sha256` as span attributes.

## Operating notes

- **Failures are silent to the group by design, never silent to the operator.** If the LLM
  or signal-cli errors while handling a tag, Nora logs it, increments
  `nora_silent_failures_total`, and says nothing in the group. `nora_silent_failures_total /
  nora_tags_total` is tracked as the silent-failure rate (target < 1%) — see
  `docs/runbook.md`.
- **SSE reconnects automatically** with exponential backoff.
- **Honcho is best-effort** — if it's down, Nora still replies, just without durable recall.
- Groups only. DMs and sync messages from Nora's own device are ignored on ingest.
- **She may speak uncalled only where a room is granted it.** Reminders,
  participation and proactive rows are all re-screened against `policy.toml` at
  the moment they would fire, so trust withdrawn after the fact still bites.

## Unattended browser/OpenCLI runtime

The host also runs a persistent desktop for browser-based social reads. It is
deliberately separate from the Nora container, and Nora runs fine with all of
it down — with it down, `social_fetch` simply answers `unavailable`.

- **Xtigervnc on display `:2`** (1920x1080, Openbox) as the systemd *user*
  service `~/.config/systemd/user/nora-social-desktop.service`, started at boot
  via user lingering. The virtual display is load-bearing: OpenCLI's Browser
  Bridge extension drives real Chrome window APIs, and GNOME RDP reports zero
  monitors while no client is connected, so window creation failed there. RDP
  is now optional administration only — it is not the browser runtime, and the
  old RDP-based workaround is retired.
- **Headed Chrome on `DISPLAY=:2`** (`--ozone-platform=x11`, the existing
  authenticated profile, which holds the Instagram and X sessions) as
  `~/.config/systemd/user/nora-social-chrome.service`.
- **OpenCLI 1.8.7** (installed under `~/.hermes/node`, daemon on
  `localhost:19825`) with the Browser Bridge extension connected to that
  Chrome. `opencli doctor` passes, and the extension reconnects on its own
  after a Chrome restart.
- **The social bridge** (`deploy/social-bridge/nora-social-bridge.service`,
  `python -m nora.infra.social` on `127.0.0.1:19826`) — the narrow host-side
  seam the container calls.

A VNC viewer connects over **Tailscale only** (`100.91.183.127:5902`, TLS) and
may disconnect freely — Xtigervnc, Chrome and OpenCLI all stay alive. The
viewer is for occasional visual access and login renewal, not for operation.
The VNC password lives only in the service's private configuration; it is never
committed.

The Nora surface is one constrained, read-only tool, `social_fetch`, covering
X and Instagram (DEC-151): a post (X only), a profile, an account's posts, and
search — and no posting, liking, following, commenting or deleting, enforced
by the tool's closed vocabulary and re-enforced by the bridge's own allowlist
host-side, so a write verb is unreachable even under prompt injection. It
ships inert behind `[social] enabled`. Instagram post-by-URL is still
unsupported (OpenCLI 1.8.7 has no caption-by-URL command), and Threads support
waits on a native OpenCLI adapter (1.8.7 has none). See `docs/runbook.md` §10
for operating the desktop.

## Inspecting the buffer

```bash
docker compose exec db psql -U nora -d nora -c '\d messages'
docker compose exec db psql -U nora -d nora -c 'SELECT count(*) FROM messages'
docker compose exec db psql -U nora -d nora -c "SELECT sender_name, body FROM messages WHERE is_nora"
```

(Or from the host: `psql postgresql://nora@127.0.0.1:5435/nora` — the `db` service
publishes 5432 on loopback as 5435.)

## Operator runbook

See `docs/runbook.md` for what to do when Nora goes quiet, a doctrine edit breaks her, the
watchdog alerts, or you need to roll back to v1.

## Deployment

Nora runs as a **Docker container** — `Dockerfile` + `docker-compose.yml` at the repo
root. This replaced the systemd-unit plan in `deploy/` (kept for reference/rollback
only — see `deploy/README.md`); the `nora` user, egress restriction, and watchdog
polling described there now map onto container equivalents below. **signal-cli runs in this
same compose project** (`docker-compose.yml`, built from `deploy/signal-cli/`), on
host networking, and Nora talks to it over `127.0.0.1:18080`. It used to run as a
bare JVM on the host; it was containerised in this phase, and `--receive-mode` is
load-bearing — without it the daemon answers every RPC correctly and never opens
its websocket to Signal.

### Build and start

```bash
cd ~/workspace/nora
cp config.example.toml config.toml        # if you don't already have a real one
cp identity.example.toml identity.toml    # map real Signal numbers → person_id

export OMNIROUTE_API_KEY=<your-key>       # required — compose refuses to start without it

docker compose build
docker compose up -d
docker compose ps                         # should show "healthy" after ~20s
```

The container uses `network_mode: host` — Nora is a purely localhost agent (signal-cli
`:18080`, Honcho `:8000`, OmniRoute `:20128`), so host networking is simpler and more
honest than reproducing three port-forwards. `GET http://127.0.0.1:9100/health` and
`/metrics` are reachable directly on the host, no port mapping needed.

`config.toml`, `identity.toml`, and `nora/doctrine/` are bind-mounted **read-only** into
the container from the repo — they are never baked into the image. `./data` is mounted
read-write for `nora.log` (and media blobs when custody is on); the record store itself
lives in the `db` service's `db-data` volume.

### Editing the doctrine in the container

Same workflow as bare-metal: edit `nora/doctrine/*.md` on the host and save. Because
`nora/doctrine/` is a live bind mount and `doctrine.reload = "mtime"` re-stats on every tag,
the running container picks up the change on the next tag — **no rebuild, no
restart**.

### Logs and metrics

```bash
docker compose logs -f nora               # structured JSON logs (also in ./data/nora.log)
curl -s http://127.0.0.1:9100/health | python3 -m json.tool
curl -s http://127.0.0.1:9100/metrics
```

### Watchdog

The SSE-silence watchdog is implemented as data, not a process to containerize:
`/health` reports `sse.last_event_age_seconds`, and `nora_last_event_age_seconds` is a
live gauge in `/metrics`.

**Nothing polls those by hand any more.** Prometheus scrapes the gauge and
`PlatformSilent` alerts on it, delivered to the Discord review channel — see
`docs/observability.md`. The old shell watchdog was deleted on 2026-09-09: its one
condition is a rule now, and its alert channel was `echo >&2`, which even scheduled
would only have produced a failed systemd unit for nobody to read.

### Rollback

Fast path — retag the previous phase's image and restart, no rebuild:

```bash
docker tag nora:p0 nora:latest
docker compose up -d --no-build
```

Every phase tags an image (`nora:p0`, `nora:p1a`, …), so there is always a tagged point to
return to — `docker images nora` lists what's available to retag. This takes seconds and
needs no network access.

Fallback — rebuild from a source state that has no tagged image (e.g. the pre-rewrite v1
codebase). This needs network access and **`docker build --network=host`** — the default
BuildKit bridge can't resolve `deb.debian.org` on this host, so a plain `docker compose
build`/`up --build` stalls:

```bash
docker compose stop nora
git checkout <ref>
docker build --network=host -t nora:latest .   # unstamped: she answers «I can't
                                               # check» to 咩version. Use
                                               # scripts/build-release-image.sh V<n>
                                               # to rebuild a tagged release.
docker compose up -d --no-build
```

### Versions, and the note she posts after an upgrade

Nora's version is **baked into the image at build time** — plain `V1`, `V2`, `V3`, no
semver. Build with the wrapper rather than `docker build` directly:

```bash
uv run python scripts/render_release_notes.py
# Review/edit docs/releases/V3.md, then explicitly release it:
uv run python scripts/release.py
```

JSON fragments accumulate user-visible changes between releases. They are
source material for the manual `docs/releases/V<n>.md` record, not a deploy
trigger and not an automatic tag. Each feature, fix, behaviour/security change,
or meaningful limitation adds one fragment at implementation time; internal
work adds an explicit `type: "none"` fragment. See `release-fragments/README.md`.

That stamps `nora/version.json` from git (the running version, plus each earlier tag's
annotation and commit subjects), passes the same value as `--build-arg NORA_VERSION`, and
carries the `--network=host` this host needs. The Dockerfile **fails the build** if the
stamp is missing or disagrees with the build argument, so the version she reports can
never be a version she is not running. There is no runtime GitHub lookup: the container
has no git checkout and may not reach github.com, and a release API would describe the
repository rather than this image.

Two things follow from that stamp:

- **`nora.000 你而家咩version`** — she reads it off the artifact. Asking about an earlier
  version (`V2 有咩?`) reads that tag's annotation. A version the manifest has no record
  of is «there is no such version»; an image built *without* the wrapper has no stamp at
  all, and she says she can't check rather than claiming she has no versions.
- **The upgrade note.** On the first boot of a new version she posts the tag's annotation
  to every group `policy.toml` allows her into — **once, ever, per room** — relayed in her
  own words rather than pasted. A restart or a crash-loop never repeats it. A version
  tagged without a message (`git tag V3`, no `-m`) has nothing to relay, so she says
  nothing; annotate the tag and rebuild. Turn the whole thing off with `[version]
  announce = false`.

Either way, `.env` supplies `OMNIROUTE_API_KEY` to Compose automatically — nothing needs
exporting by hand. The record store (the `db` service's volume) is untouched by either
path — schema changes are versioned Alembic revisions applied by `run_migrations` at
startup, not additive `CREATE TABLE IF NOT EXISTS` statements. See `docs/runbook.md` §3
for the full incident playbook, including the persistence-phase rollback step.
