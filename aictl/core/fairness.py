"""aictl fairness — advisory multi-tenant fair-share report (IMPROVEMENTS.md item M).

Pure-function report over aictl.core.metering's already-collected per-entity
token usage. Advisory only: this module makes no scheduling, admission, or
priority decisions, and nothing on the live request/serving path (proxy,
governor, broker) calls it. It exists purely so `aictl tco fairshare` can
answer "is usage across my tenants/apikeys actually balanced?".

Metric: Jain's Fairness Index (https://en.wikipedia.org/wiki/Fairness_measure)
over each entity's share of cumulative total_tokens. The VTC paper's exact
weighted virtual-counter formula (arXiv:2401.00588, prefill/decode-weighted
cost) could not be independently verified at design time (source PDF
unreachable); Jain's index is the well-grounded, easily-verified alternative
research surfaced, and needs no new tracking beyond what TokenMeter already
records. A rolling window (vs. all-time cumulative total_tokens) is a
documented future extension, not built here -- tokens_today/tokens_this_month
reset on rotation, which would make the index jump discontinuously across
midnight/month boundaries and misrepresent a fairness *report*.

Prefix-cache locality (DLPM, arXiv:2501.14312) is NOT blended into this
report: aictl.runtime.prefix_route.PrefixRouteTracker is confirmed
endpoint-keyed only, with no entity/tenant dimension anywhere in it today.
Fabricating a per-tenant locality score would mean inventing data that
doesn't exist -- see `locality_note` on the report instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aictl.core.metering import TokenBucket

LOCALITY_NOTE = (
    "Prefix-cache locality is not tracked per-tenant today "
    "(runtime.prefix_route.PrefixRouteTracker is endpoint-keyed only); "
    "a future pass could add tenant-tagged prefix stats before any "
    "fairness x locality blend."
)


@dataclass
class FairnessReport:
    jains_index: float | None
    entity_count: int
    total_tokens: int
    entities: list[dict] = field(default_factory=list)
    locality_note: str = LOCALITY_NOTE


def compute_fairness(buckets: list["TokenBucket"]) -> FairnessReport:
    """Compute a Jain's-fairness-index report over entities' total_tokens.

    `buckets` is typically `TokenMeter().list_usage()`. Entities with zero
    total_tokens are included (share 0.0, classified "starved") rather than
    filtered -- a registered-but-unused entity is exactly what "starved"
    should surface.
    """
    n = len(buckets)
    if n == 0:
        return FairnessReport(jains_index=None, entity_count=0, total_tokens=0, entities=[])

    totals = [max(0, b.total_tokens) for b in buckets]
    grand_total = sum(totals)
    expected_share = 1.0 / n

    if grand_total == 0:
        jains_index = 1.0  # everyone equally at zero -- trivially "fair"
    else:
        # Jain's fairness index: (sum x_i)^2 / (n * sum x_i^2), range (1/n, 1].
        jains_index = (grand_total ** 2) / (n * sum(t ** 2 for t in totals))

    entities = []
    for bucket, total in zip(buckets, totals):
        share = (total / grand_total) if grand_total > 0 else 0.0
        if share < 0.5 * expected_share:
            classification = "starved"
        elif share > 2.0 * expected_share:
            classification = "over_share"
        else:
            classification = "fair"
        entities.append({
            "entity_id": bucket.entity_id,
            "entity_type": bucket.entity_type,
            "total_tokens": total,
            "share": round(share, 4),
            "classification": classification,
        })

    # Most over-shared first -- the entities worth looking at.
    entities.sort(key=lambda e: -e["share"])

    return FairnessReport(
        jains_index=round(jains_index, 4),
        entity_count=n,
        total_tokens=grand_total,
        entities=entities,
    )
