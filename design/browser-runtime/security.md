# Security and evidence

## Network and browser isolation

Each new scope gets the public-web TCP allow with private-range denies applied
and read back before use. The shared VM's policy does not transfer. A failed or
unavailable check withholds the wildcard allow.

Hostname-to-private-address rebinding is a distinct threat: a deny written only
against IP literals does not stop a public hostname resolving to a private
service. Shared HTTP egress therefore enforces public destinations at
connection time, including mapped, carrier-grade, link-local and native
loopback addresses. CONNECT and Upgrade paths are checked for real browser and
downloader compatibility; proxy bypass and non-TCP channels are not silently
exempt.

Chrome uses the intended proxy with native TLS verification and a consistent
fingerprint baseline. No secret substitution or method/path interception is
enabled for visited sites. A bot challenge is `unavailable`, never a fabricated
success, solver or silent switch to a stealth engine.

## Optional credential brokerage

Credentialed browsing is a separate design. A future vault keeps ciphertext in
a broker-controlled store and returns handles, never values, to the model.
Origin, frame, field, scope and approval are checked immediately before a fill;
anything else is unavailable. Read-only records may auto-approve; posting,
buying and sending always ask.

A fill types only into a validated login field on a bound origin and rechecks
frame and focus before insertion. The broker, not the page or model, decides
what credential and destination are legitimate. Sealed or unavailable vault
state fails closed.

Every read path after a fill is redacted: accessibility, script and network
output, screenshots, traces and audit rows. A canary credential must appear
nowhere. Password storage in the browser is disabled, and a credentialed field
never permits arbitrary script evaluation.

Anonymous scope requires none of this machinery. A personal or authenticated
profile is never attached for convenience or remote debugging.

## Measurement

Evidence distinguishes source checks from installed acceptance. A real journey
covers first use, repeated and cross-transport reuse, capture/export, idle stop,
disk-preserving wake, cancellation and restart. Generated wrong-call responses
cover foreign identity, bad authentication, capacity, corrupt registry and
partial provisioning without creating a positive-looking success.

Resource acceptance measures whole-VM cost: browser descendants, resident and
shared memory, swap, pressure and concurrent demand. Configured guest RAM or
host Chrome processes alone do not establish capacity or a safe concurrency
ceiling.

Challenge rate is measured on a fixed anonymous task set and reported with
attempts, failures and sample size. Vendor benchmarks, a real browser brand and
a residential exit do not prove anti-bot performance. No comparison model,
engine or paid experiment is required to establish the scoped browser itself.
