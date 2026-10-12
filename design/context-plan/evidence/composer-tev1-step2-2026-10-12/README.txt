Step 2 of the composer sequence (design/context-plan/15 §6): off-the-shelf Tev1 on norty's
Ollama 0.35 /v1/systemone as Nora's prompt composer, 2026-10-12/13. No training, no labels,
no LLM judge. Counts only here; every turn, state and per-turn answer stays on norty in
~/composer-labels/step2/.

Pipeline (norty, python3 stdlib, reuses ../composer-labels-2026-10-12/{extract,aggregate}.py):
  prep.py      <replying_to> from the raw traces, sender listed in <room_members>
  sample.py    stratified order (greeting, rarest evidence bundle, none; round-robin) for arm A
  run.py       one request per turn, concurrency 1; arm A = one Noul per present unit (cards.json),
               arm B = D1 Choice (10 turn kinds) + D2 Choice (depth); state per §4
  analyze.py   §4a rules (thresholds by class x 0.5/1/2, deps, floor, media-code override,
               fail closed) -> aggregate.json / aggregate.txt
  latency.py   what drives latency (D1 vs D2; N questions x state size) -> latency-part*.{log,json}

Ground truth is mechanical: tool calls -> bundle/parts, a media code in the message or last 5
lines, >= 3 distinctive n-grams shared by the reply/tool args and a block, a line older than the
last 5 reused by the reply (depth). Positive evidence only: it cannot say whether voice, banter
or identity doctrine was needed, and a turn with no trace counts for size/latency only.
163 of 200 turns have evidence (471 items, one per bundle and source).

Models: tev1:0.8b and tev1:4b ship with num_ctx 2050; 8 Nouls over a real state already exceed
it (HTTP 400), so both ran as additive variants with `PARAMETER num_ctx 16384`
(tev1-08b-ctx16k, tev1-4b-ctx16k), same weights (0.8B: identical probabilities on the 2 arm-B requests
compared against tev1:0.8b; 4B not compared); both were `ollama rm`-ed at the end.

Runs: B/0.8B all 200; B/4B the newest 76 (hit its 40 min cap; not resumed per the operator; partial,
not stratified); A/0.8B the first 36 of the stratified order; A/4B not run (operator).

Incidents: one ROCm "illegal memory access" while both Tev1 sizes were resident; afterwards the
runner answered every request with HTTP 500 "scoring returned an invalid candidate probability"
until it was reloaded (`ollama stop`). Seen again after long arm-A requests; run.py reloads and
retries once (1 of 36 arm-A turns retried). bge-m3 (the live service) answered throughout.
Prompt caching: an identical repeated request takes ~0.7 s vs ~7 s cold, so every latency
figure is a first-seen state (latency.py adds a nonce).

Tokens are node 09's chars->tokens fit, not tokenizer counts.
