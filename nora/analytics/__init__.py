"""The mobility rollup — the loop that orchestrates `nora.domain.mobility`
(pure segmentation/clustering/labelling) against `nora.infra.sql.mobility`
(the derived-layer store): `nora.analytics.rollup.MobilityRollup`.
"""

from __future__ import annotations
