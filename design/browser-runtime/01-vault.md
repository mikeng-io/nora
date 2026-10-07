# Nora Vault — OpenBao and a fill broker

Status (2026-10-07): **accepted (ADR 0022), not built.** Part of `00-design.md`; the
decision is ADR 0022 Decision 3.

## 1. The problem

Browser use multiplies credentials: one per account per site, plus 2FA
seeds, plus session cookies that are credentials in their own right. Today
every secret is a line in the host `.env`, passed into a service key by key
by `docker-compose.yml`. That cannot hold dozens of per-site logins, cannot
say which room may use which, cannot audit a use, and puts every value in a
process environment.

The harder half is not storage. It is **keeping the value out of the
model**: out of the prompt, the tool arguments, the tool results, Langfuse
traces, `computer_events`, and any page read after the fill. Hermes Agent
built a password-blind vault and still leaked: its default `browser_exec`
tool, the one path without the redactor its CDP tool had, read the filled
field back in plaintext into its state database (issue #112693, fixed by
PR #113261; `02-research.md` §1). The store is the easy part.

## 2. OpenBao

- **OpenBao**, the MPL-2.0 Linux Foundation fork of Vault (Vault moved to
  BSL in 2023). API-compatible; transit, KV v2 and TOTP engines included.
- Runs on the host, not in the VM, not in the Nora container. Listens on
  localhost / docker0 only, TLS, an AppRole (or similar) for the broker alone.
- **Engines:**
  - **transit**: envelope encryption. Credential records are stored as
    ciphertext in Postgres; only the broker can ask OpenBao to decrypt. Fits
    the repo's typed, audited rows with provenance, and lets the same key
    encrypt browser-profile / cookie snapshots at rest.
  - **TOTP**: OpenBao generates the 2FA code; the seed never leaves it.
  - KV v2 is the simpler alternative to transit+Postgres; rejected for the
    record table because it splits the record from Nora's own audit, kept
    possible for the few service API keys that sbx substitutes.
- **Unseal.** A sealed OpenBao after every reboot is the cost. Manual unseal
  by the operator is the default; auto-unseal is a later decision. OpenBao
  has no TPM seal (`02-research.md` §6): norty's TPM would go through the
  PKCS#11 seal (untested), and a cloud KMS puts the unseal on a third party. An unseal key on norty's own disk defeats the point and is
  rejected. While sealed, every fill is `unavailable`, never a fallback.
- **Audit device** on, to a file the host rotates; plus a Postgres row per
  decrypt (who, which handle, which scope, which origin, approved by).

## 3. The credential record

| field | meaning |
|---|---|
| `handle` | `cred:<site>/<account>`, the only thing a model ever sees |
| `kind` | `password` / `totp` / `api_key` / `cookie_snapshot` |
| `origins` | the top-level origins a fill may happen on (`https://x.com`, `https://accounts.google.com`) |
| `scopes` | which rooms (scope hashes) may use it; empty means operator only |
| `approval` | `auto` / `ask` (per-use Signal prompt to the operator); `auto` only where `read_only` is set |
| `read_only` | set by the operator when the record is made: the account is used to read, never to post, buy or send. Never set by a model |
| `fields` | username, password ciphertext (transit), TOTP key ref |
| `created_by`, `rotated_at` | provenance |

- **Origin binding is the defence against injection:** a page that talks the
  model into "log in here" on `evil.example` gets `unavailable`, because the
  broker checks origins at fill time, not the model's claim. Password
  managers match URLs for the same reason. **Both** the top-level origin and
  the origin of the frame that owns the field must be in `origins`, so a
  cross-origin iframe (an ad, an embed) on a bound page gets nothing.
- **Origin is not enough on its own:** a prompt-injected post on `x.com`
  could ask for the password to be "filled" into the post composer, and the
  origin check would pass. So the broker also checks **what it is filling**
  (§4), and the scrubber cannot help here, because this is a write, not a
  read.
- **Approval (operator, 2026-10-07):** `auto` for credentials the operator
  marks `read_only`; `ask` for every other, that is anything that can post,
  buy or send.

## 4. The fill broker

- Lives in Nora's process (or a host sidecar), never in the VM.
- **The only path a credential takes into a page:** the sub-agent emits
  `fill_credential cred:<handle> @eN`; the broker resolves the handle, checks
  scope, origins and approval, decrypts through transit, and types the value
  into the element over CDP (`Input.insertText` after focusing the node).
  The result to the model is `filled` or `unavailable(reason)`.
- **The element is checked, not trusted.** A password goes only into an
  `input[type=password]`, a username only into an input whose `autocomplete`
  is `username` or `email`, and a TOTP code only into an input with
  `autocomplete="one-time-code"` or a numeric input inside a login form. In
  every case the input must sit in a `<form>` whose action resolves to a
  bound origin. Anything else is `unavailable(not_a_login_field)`. The
  decrypt row records the element's tag, type, autocomplete and frame.
- **Checked twice.** Frame id, origins and focus are read again immediately
  before `Input.insertText`, and the fill aborts if the frame navigated or
  focus moved in between.
- **Only the logged-in profiles.** The broker fills only into a Chrome that
  computerd started as the `browser` user (`00-design.md` §4), never into a
  room's anonymous profile, whose files the room's own shell can read.
- **Two kinds of secret, two paths:**
  - **API keys over HTTP**: sbx `secret set-custom` placeholder substitution
    on the host proxy (sandbox design §8). Unchanged.
  - **Browser logins**: the fill broker above.
- **Sessions first.** Most uses type nothing: the profile (or a restored,
  transit-encrypted cookie snapshot) is still logged in. The broker is for
  re-login when a session expires. `session_probe`'s rule, that the browser
  owns the session, carries over.

## 5. Redaction, on every read path

The lesson from Hermes: redact where data **leaves the browser**, not in one
tool. After a fill, the broker registers the value (and its obvious
encodings) with a scrubber that runs on:

- accessibility snapshots (password inputs' values are already masked;
  text inputs are not);
- any JS-eval / `extract` result, console logs, network logs, cookie dumps;
- screenshots: the field is masked before capture (CSS overlay) or the frame
  is dropped; the stream (00 §7) pauses during the fill;
- Langfuse spans and `computer_events` rows.

And: **no arbitrary JS eval on a page that holds a credentialed field**; the
sub-agent's eval tool is disabled from fill until navigation away.

A test that matters more than any other here: fill a known canary credential,
then run every read tool and search every trace and row for the canary. Zero
hits, or the build fails.

## 6. Nora's Google account

- **A dedicated account, not the operator's.** The profile sits next to a
  shell the model drives, pages can carry prompt injection, and Google may
  flag automated accounts. Losing it must be cheap.
- Its own phone number; 2FA by authenticator, seed in OpenBao TOTP; recovery
  codes printed and offline, not in OpenBao.
- **Sign in by hand** over noVNC (00 §6) on the `browser` user's display,
  operator only; Google rejects
  sign-in from a browser it reads as automated.
- Google web sign-in only; **Chrome Sync off** (it would sync history and
  passwords across devices), and the password manager off by policy
  (00 §4).
