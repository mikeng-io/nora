# The ranker

## Trigger and frame

A run begins with a person asking, an open plan approaching its useful
decision window, an interest burst above its baseline or an outside change to
a candidate the group already cares about. Triggers are computed by code and
become stimuli; Cognition decides whether to spend a proposal.

The frame freezes the recommendation type, attendee set, event time, origins,
destination audience, eligible source revisions and the question. Later
stages cannot quietly change it. Replay freezes strictly before the actual
choice and excludes that choice and its outcome.

Candidate generation follows attendee routes and the type's sources rather
than one centre point. The model extracts candidate facets once per description
revision. Any personalised query or cached description inherits its source
restrictions and never becomes a public catalogue item.

## Filter and score

Hard constraints remove candidates with their reason recorded. Surviving
candidates are scored on normalised likes, similarity, logistics, coverage,
timing, cost, novelty and any model-judged suitability that computation
cannot supply. The recommendation type supplies feature weights.

Likes aggregate by average-without-misery so one well-supported strong
objection can matter without every lukewarm preference becoming a veto. A
small fairness credit can favor people whose preferences lost recent closed
decisions; tie strength never makes one person's taste count more.

Logistics combines the worst attendee's burden with the mean for efficiency.
Coverage answers whether the frozen attendees can actually participate. Known
incompatibility fails where full participation is required; unknown ownership,
roster or compatibility is unknown, never zero coverage or a smaller
denominator.

Unknown features stay unknown. Their weight redistributes over known features,
the candidate carries the uncertainty, and a candidate whose decisive feature
is unknown does not rank first. Code, not a model's confidence, computes these
numbers.

## Diversify and explain

Maximal marginal relevance selects a small shortlist by score discounted for
similarity to options already chosen. It produces different kinds of answer
rather than several near-identical candidates.

Explanations name the features and evidence that contributed most, with any
assumptions or blocked unknowns. The model may phrase them only from those
recorded reasons and only where their evidence may be shown.

The run writes a Standing decision containing the options, criteria, reasons,
unknowns and turn reference. It remains open until the group chooses, then
closes on the supported choice. The speaking path uses the same decision and
does not create another ranked list in prompt prose.

## Evidence and replay

Replay uses real past choices and only evidence Nora actually held before
each one. Both valid time and recorded/system time matter: a backfilled old
post, later perception, edited source, current profile or current catalogue
cannot enter the frozen run.

Historical profile, source, Rollup, place and candidate revisions must be
reconstructable or the arm is unavailable with its missing inputs reported.
Current disclosure still applies; history never restores permission.

The full score is compared with popularity and similarity-only baselines on
held-out decisions, with feature ablation where useful. Ranking quality,
acceptance and ignore rates answer different questions, and unresolved
outcomes stay out of their denominators.
