# Third-Party Notices

## lieflat-charts

Vendored at commit `e05f777261a774c945bdd0157817ef68f3c4766d` from
https://github.com/larashero3-dotcom/lieflat-charts, under the PolyForm
Noncommercial License 1.0.0 (see `nora/compute/smith/skills/charts/LICENSE`).
Verified permitted for Nora's private, non-commercial, self-hosted use under
that license's "Personal Uses" clause (see
`docs/superpowers/specs/2026-08-12-nora-compute-design.md` §7).

Required Notice: Copyright the `lieflat-charts` authors
(https://github.com/larashero3-dotcom/lieflat-charts).

**Modification from upstream:** the Google Fonts-loaded Inter typeface
reference has been replaced with a system font stack in every vendored
template, so no vendored artifact reaches an external CDN for its type at
render time (Nora Compute spec §7/§10). Chart.js and Apache ECharts (loaded
via CDN in the Glance/interactive templates) are UNCHANGED from upstream and
remain third-party dependencies of those templates specifically:

| project | used by | license |
|---|---|---|
| Chart.js | Glance chart rendering | MIT |
| Apache ECharts | Glance + interactive network diagrams | Apache-2.0 |
