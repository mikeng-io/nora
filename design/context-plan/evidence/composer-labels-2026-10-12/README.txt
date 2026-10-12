First labelling pass for the prompt composer (design/context-plan/14, section 5 and
section 7 step 3), 2026-10-12. Counts only; every room message, reply, label rationale
and the spot-check sample stay on norty under ~/composer-labels/.

Pipeline (run on norty, in ~/composer-labels, python3 stdlib only):
  fetch.py 280      newest 280 `engagement` traces from Langfuse (127.0.0.1:3100)
  extract.py 200    newest 200 with a main-agent first generation -> units per turn
  label.py          one nora-judge call per turn through OmniRoute (127.0.0.1:20128),
                    temperature 0.1, reasoning_effort low, hard call budget
  aggregate.py      mechanical signals, per-unit rates, composed sizes, spot-check file

Outputs here: aggregate.txt / aggregate.json (per-unit and per-size counts),
extract-summary.json (selection, unit presence, segmentation gaps).

Reading the numbers:
- Tokens are ESTIMATES from node 09's fit (0.259 per non-CJK char, 0.906 per CJK
  char, intercept 2073). Resident tool schemas are not in the trace; their size is
  master 6e7199d0d's schema JSON length, and the remaining 649 of the intercept is
  kept as floor framing. Reconstruction against recorded input_tokens: median
  residual +289 tokens (1.4%), max 1261.
- "composed" = floor (header, message, core constraints, wrappers) + framing +
  units the labeller marked needed + transcript at the labelled extent (last_few =
  last 5 lines). It is what an oracle composer with these labels would send; it is
  not evidence that such a prompt answers as well.
- Mechanical signals are positive evidence only (tool called, media code, or >= 3
  distinctive shared n-grams between reply/tool args and a block). "agree%" is the
  share of mechanical positives the labeller also marked needed.
- Greeting-like = message <= 25 chars after removing mentions, no question mark, no
  media code, no tool call except send_reply (15 of 200 turns).
- The recorded window predates doctrine step 2: rooms/constraints.md units (except
  two predecessors, mapped by heading) and conduct.look_first never appear, and
  block.self_card is in the traces but not in the inventory.
- Labels are one draw of one model; the operator spot-check sets the disagreement rate.
