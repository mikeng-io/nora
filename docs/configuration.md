# Configuration and credential delivery

`config.toml` carries deployment settings, `identity.toml` people, `policy.toml`
trust, and private `secret.toml` credential values. Identity and policy reload every
three seconds after validation. Config and secrets require controlled restart or
recreation. They are not hot reloaded. See the
[contract amendment](adr/0033-private-secrets-and-model-endpoints.md) (DEC-325).

## Source and schema

```toml
# secret.toml, private owner, mode 0600
[credentials]
PRIMARY_KEY = "operator-supplied-value"
NORA_BRIDGE_TOKEN = "operator-supplied-value"
```

Existing `*_env` fields remain explicit references. A present process variable wins
even when empty; an absent variable falls back to the file. Required empty values
fail without displaying values. No automatic `NORA_` prefix or global environment
mutation is used. `nora config validate` validates settings without secrets/network.
Do not pass secret values to `nora config set` or store them in config.toml.

Local Python consumers read the sibling secret file. Container consumers receive
native environment projections. `.env` participates only in deployment paths that
load it; host precedence is process environment, then legacy `.env`, then secret.
To retire `.env` overrides, remove the corresponding assignments under the deploy
lock after backup and validation. Empty assignments remain overrides.

## Model endpoints

```toml
[providers.primary]
base_url = "https://gateway.example.invalid/v1"
api_key_env = "PRIMARY_KEY"
api = "openai-chat-completions"

[providers.secondary]
base_url = "https://inference.example.invalid/v1"
api_key_env = "SECONDARY_KEY"
api = "openai-chat-completions"

[llm]
provider = "primary"
model = "chat-model"
# Keep existing token, timeout, reasoning and loop budgets.

[model_routes.dreamer]
provider = "secondary"
model = "background-model"
```

Provider names are arbitrary. Models are opaque endpoint IDs. Without provider,
a route uses the default endpoint; without model it uses the existing purpose
model or default model on that endpoint. Selecting another provider requires an
explicit model. Unknown providers and active unsupported formats are rejected.
OmniRoute remains supported through legacy llm base_url/api_key_env or a named
provider. There is no fallback routing. `anthropic-messages`, `openai-responses`
and `openresponses` are inactive format examples, not supported transports.

Route purposes: tag, participation, proactive, summons, browse, vision,
speech_format, reaction, standing, relevance_gate, lesson_check, dreamer,
subagent, extraction, eval_judge, context_filter. Image generation keeps its
existing OAuth provider lanes. Media describers may select a named provider;
legacy kimi/opencode-go keep their direct session-aware transport. Host model
measurement scripts accept `--config` to select the same purpose routes; explicit
legacy endpoint flags remain available without that option.

## Consumer and delivery map

| Old reference | New source | Process / delivery | Verification |
|---|---|---|---|
| llm.api_key_env and model keys | credentials referenced by named providers; model_routes | Nora SDK/raw clients, CLI and subjects; projected aliases | focused route tests, functional model occasion |
| DATABASE_URL / store.database_url_env | credentials / legacy derived Postgres URL | Nora, presence, analytics; native Compose URL and aliases | store health plus a read-only store operation |
| POSTGRES_PASSWORD | credentials | db native POSTGRES_PASSWORD | native DB readiness and read-only query |
| RUSTFS_ROOT_USER/PASSWORD | credentials | RustFS native env; Nora custody refs | generated synthetic blob write/read/remove under operator scope |
| DISCORD_CLIENT_ID/SECRET, AUTH_SECRET | credentials | portal AUTH_DISCORD_ID/SECRET, AUTH_SECRET | portal authentication using authorized operator session |
| NORA_BRIDGE_TOKEN | credentials | transport, alert, social and voice bridges; explicit Compose env | authenticated non-send bridge read |
| LIFE360_* and maps refs | credentials | presence and configured Nora geocoding; scoped aliases | collector authenticated refresh/readback |
| GOOGLE_OAUTH_*, Tavily/Exa, Steam, Giphy, Pulse, MiniMax | credentials | Nora integration constructors / explicit native env | enabled integration's read-only functional operation |
| NORA_OTP_*, MEDIA_ENCRYPTION_KEY | credentials | Nora; portal receives only OTP API bearer | OTP round trip / synthetic media round trip |
| SENTRY_DSN, LANGFUSE_OTLP_HEADERS | credentials | configured observability readers | accepted synthetic event / authenticated trace read |
| KIMI_CODE_API_KEY, OPENCODE_GO_API_KEY | credentials | direct media clients; existing headers retained | synthetic request transport test; authorized media readback |
| XAI_OAUTH_REFRESH_TOKEN, OPENAI_CODEX_REFRESH_TOKEN | credentials bootstrap only | Nora; existing state/*.json current chain | OAuth contract and keeper tests; state file retained |
| NORA_COMPUTER_TOKEN | credentials | host lifecycle resolves only this key; forwards only COMPUTERD_TOKEN | authenticated computer lifecycle status, then existing command canary |
| operational script credential reads | common resolver | host-local Python process | synthetic/offline input tests; never print resolved mappings |

Existing Compose owns native image mappings; `scripts/secret_delivery.py` adds
configured aliases only to Nora, presence and analytics. The installed Komodo
wrapper writes private `compose.env` and `compose.json` into `.secret-delivery/`
beside itself (normally `/etc/komodo/nora-deploy/.secret-delivery/`). Its control
directory must be writable and shared at the same path on the host and in
Periphery; the credential source runtime can remain read-only. Direct helper
calls default to `<runtime>/.secret-delivery/` and may select an explicit
`output_directory` instead. The generated directory is mode 0700 and its files
mode 0600. Do not publish Compose config,
Docker inspect environments, generated files or diagnostics. Native infra remains
outside the release-owned image cohort. No shared secret-file mounts are added.

## Host migration and installed files

Parent must run on the configured host alias **norty**; no host was accessed by
the builder. Native Komodo `nora-releases` owns deployment, not checkout mode.
The installed wrapper lives outside the fetched clone. Under its existing shared
deployment lock, back up and replace the installed `komodo_deploy.py` together
with sibling `secret_delivery.py`; retain `verify-deploy.py` and the wrapper's
existing controls. Update the installed computer `nora-computer` from this tree.
Validate `python3 -c "import tomllib"` in Periphery and the host, then compile
the candidate installed scripts before enabling them. Python 3.11+ is required;
if the installed Periphery lacks it, update/rebuild its existing image first; source merge alone
changes none of these installed files. Keep their existing ownership/access.

One repeatable migration command, run first without `--apply`:

```sh
python3 scripts/secret_delivery.py --runtime-root "$HOME/workspace/nora"
# Only after the dry-run succeeds, while holding the installed deploy lock:
python3 scripts/secret_delivery.py --runtime-root "$HOME/workspace/nora" --apply
```

Dry run parses all assignments and the candidate TOML without printing values.
Apply selects configured credential references and native service credentials; general
deployment variables such as AUTH_URL remain in .env. It makes a private backup
of config, legacy env, previous secret and the entire
state directory, then atomically writes mode-0600 secret.toml. It retains .env
and leaves all OAuth state untouched. Unsupported dotenv interpolation/encoding
fails for private reconciliation. This command is host-local, not a deployment.

Validate settings using the candidate image through the existing network-disabled
wrapper validator. Generate private delivery with `secret_delivery.prepare` in an
isolated mode-0700 candidate runtime and inspect only service/key names, never
values. Validate `docker compose config --quiet` against generated inputs with
output captured privately. Preserve the deployed runtime path and bind mounts.

Write a mode-0600 `config-reload.json` beside the installed wrapper with
`{"files":["secret.toml"]}` (include config.toml when changed), then use native
DeployStack after releasing the outer lock. The wrapper acquires its normal lock,
validates, reconciles and force-recreates credential readers. Editing host .env
alone never changes the environment of an old container. New secret contents
change effective Compose service inputs; the explicit reload handles boot-time
changes even where effective inputs compare equal.

OAuth login now defaults to secret.toml beside --config. Explicit --env-file
retains legacy output. Remove any old bootstrap override before using the new
login value; otherwise the explicit environment precedence wins. Restart through
native Stack. Never copy current rotating tokens into TOML or delete state.
Same bootstrap bytes keep the existing state fingerprint association.

## Secret-free functional readback and recovery

Record image cohort, receipt, process StartedAt and config staleness first. From
inside each recreated reader, load settings and print only provider names, model
IDs, API format and whether required credential references resolve to nonempty
values. Never print the config object, URLs with embedded authentication, secret
mappings, hashes or Docker environments. A fresh Python file read alone proves
neither process configuration nor functional behavior: require StartedAt after
promotion and a relevant successful operation from the running process.

For overnight acceptance parent must also verify the relevant actual paths:
Nora model occasion and its trace/model route, read-only DB query, authenticated
bridge read, presence refresh if enabled, portal login, computer status, and
image OAuth keeper refresh outcome without any paid image generation. Check
keeper metrics and current-chain persistence; health alone cannot prove these.
No live success is claimed by offline tests.

On failure restore backed-up private files under the installed lock and recreate
only affected readers through native Stack. Keep current OAuth state unless a
separate coordinated state recovery is required: restoring old rotating state
can replay a spent token. Backup scope includes state; restoring it is not an
automatic part of config rollback. Preserve native DB/object-store data paths.
