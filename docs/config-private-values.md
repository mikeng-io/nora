# Private configuration references

Runtime settings may name host environment variables for fourteen deployment
values. `config.example.toml` shows the source syntax; `.env.example` lists the
names with empty values. Fill only the refs you declare, in the host `.env`.
A declared ref must resolve even when its feature is off. An empty `.env.example`
is not a usable runtime environment.

| Source field | Environment name |
|---|---|
| `agent.handles_env.signal` | `NORA_HANDLE_SIGNAL` |
| `agent.handles_env.whatsapp` | `NORA_HANDLE_WHATSAPP` |
| `agent.handles_env.x` | `NORA_HANDLE_X` |
| `agent.handles_env.threads` | `NORA_HANDLE_THREADS` |
| `agent.handles_env.discord` | `NORA_HANDLE_DISCORD` |
| `platforms.signal.account_env` | `NORA_SIGNAL_ACCOUNT` |
| `social.instagram.summons.handle_env` | `NORA_INSTAGRAM_SUMMONS_HANDLE` |
| `participation.shadow_channel_env` | `NORA_PARTICIPATION_SHADOW_CHANNEL` |
| `relevance_gate.shadow_channel_env` | `NORA_RELEVANCE_SHADOW_CHANNEL` |
| First `speech.tts.sidecars[].guild_env` | `NORA_TTS_GUILD_PRIMARY` |
| Second `speech.tts.sidecars[].guild_env` | `NORA_TTS_GUILD_SECONDARY` |
| `tools.mcp.pulse.base_url_env` | `NORA_PULSE_BASE_URL` |
| `documents.public_url_env` | `NORA_DOCUMENTS_PUBLIC_URL` |
| `doctrine.canary_env` | `NORA_DOCTRINE_CANARY` |

A literal and a ref for the same field collide. The handles maps may mix
networks only when they are disjoint. No interpolation happens in other fields;
budgets remain ordinary typed TOML, including `note-candidates`, `note-curation`
and `proactive`. Existing consumers receive the same effective fields. Handles
remain display text and do not grant access.

Run `nora config validate --schema-only --config config.example.toml` to check
source types and cross-field constraints without host values. Its success means
`schema valid; runtime env unresolved`. It does not return a runtime configuration,
load identity/policy, contact a provider or establish runtime readiness. Ordinary
`nora config validate` resolves actual refs; `require_secrets=False` relaxes only
the model API key. Source `get`, `set` and `unset` never write resolved values.
Loader failures expose fixed rule codes and validation ordinals; raw values,
dynamic keys and parser diagnostics are withheld.

Analytics refuses startup with missing, empty or invalid policy when enabled.
STT and voice-wake refuse a missing config file; neither has a development
opt-out. Missing optional speech sections still use their existing defaults.

## Future owner migration

The planned layout keeps sanitized settings in `nora-config`, private values in
`/srv/nora/.env`, and people/permissions in `/srv/nora/data`. The existing settings
will select `identity_file = "/app/data/identity.toml"` and
`policy_file = "/app/data/policy.toml"`. The main process needs nested read-only
file mounts under its writable `/app/data`; other consumers receive only files
they read. The developer example retains the current relative paths until that
mount change ships with the renderer/wrapper in the next PR.

This PR does not install a private repository, alter host files or migrate the
running Stack. The existing wrapper does not yet support private-ref preflight
with each service's resolved environment. Do not activate the example on the
release Stack until that wrapper, mount/readback and strict-validator work is
reviewed and installed. Rehearsal and owner cutover remain UNVERIFIED.
