# Feedback and suggestion policy

## Link outcome to decision

A recommendation send carries a distinct recommendation kind and a link to
the Standing decision whose options it presents. The link covers every
delivered part on tag and fire paths. An arbitrary `proposal` label or bare
message id cannot identify one.

Communicative intent remains tell or ask: a request to choose is ask;
information is tell. Those intents continue to drive expectation-of-answer
rules, so an unnamed informational send is not negative merely because nobody
answered.

Responses are read against the original option and responder. Generic
engagement is not a pick, and a negative reaction without a clear object is
not dislike of every option. Decision closure and response episodes are
reconciled so one choice cannot count twice.

## Outcome vocabulary

- **Picked:** the decision closes on one of its options, the strongest signal.
- **Engaged:** replies or positive reactions discuss the options without a
  supported choice.
- **Redirected:** the room chooses something else, which is evidence about
  that other option, not a universal dislike of the shortlist.
- **Rejected:** an identified option is explicitly rejected, with any stated
  reason kept separate.
- **Ignored:** a closed silent episode where an answer was expected and
  platform availability was proved.
- **No reading:** open, unknown or unanswered episodes and ambiguous
  engagement update nothing.

Only a person's own explicit liking or dislike supplies personal response
evidence. A pick, attendance, assent to compromise, redirect or rejection
alone supplies none for that person. Profile writers interpret the original
Record; personalisation writes no posterior or second preference store.

## Learning whether to propose

Settled accepted outcomes add evidence for proposing; rejected, redirected or
proved-silent outcomes add evidence against it. Each linked send contributes
once, recomputing from eligible records if a source is revised.

For each unit, destination, recommendation type and context bucket, a Beta
posterior estimates whether a proposal will be picked or engaged. Cognition
samples it when a trigger fires and proposes only above the type's threshold.
Restricted outcomes cannot be smuggled into a unit-wide posterior.

The weekly cap is absolute and checked before sampling. A capped or held
trigger is not sent. Asking for a recommendation bypasses sampling, not the
cap, disclosure or action authority. Every sampled decision and its value is
logged for later offline evaluation.

Feature weights and evidence half-lives stay reviewed recommendation-type
data until a sufficiently large held-out replay shows learned values beating
them. Learning-from-picks is the exception because it estimates one response
rate per bucket rather than fitting a full ranking model on a handful of
outcomes.
