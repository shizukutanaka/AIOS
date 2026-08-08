"""aictl fair scheduler — live least-service-first admission (IMPROVEMENTS.md item M).

Pass 190 shipped `core/fairness.py`, which *reports* Jain's index but makes no
decision. This module is the decision half: given per-entity service already
received, it says whether a tenant should yield right now so a starved one can
proceed.

**This is not VTC.** The VTC paper (arXiv:2401.00588, OSDI '24) is the
motivating work, but its exact virtual-counter update — specifically the
input/output token weighting and the precise counter-lift rule for a newly
arriving client — could not be verified from this environment: arxiv.org and
every secondary source carrying the formula are egress-blocked, the same wall
Pass 190 documented. Rather than ship a guessed formula under the paper's
name, this implements the two properties that *are* consistently reported
across sources and are textbook on their own:

  * **Least-service-first.** Order by cumulative service received, not by
    arrival, so a heavy tenant cannot crowd out a light one.
  * **New-arrival lift.** A client with no history starts at the current
    minimum rather than at zero. Starting at zero would let a fresh (or
    deliberately recycled) identity monopolize the queue until it caught up
    with everyone else — the obvious way to game least-service-first.

Service is measured in tokens, weighted so output costs more than input,
because decode is the scarce resource. The weight is configurable and its
default is an *engineering choice*, not a number from the paper — the
docstring says so rather than implying a citation it cannot support.

Advisory-by-default, like everything else here: `should_admit` is a pure
function over data `TokenMeter` already records, and nothing calls it until
`fair_share_policy` is turned on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aictl.core.metering import TokenBucket

# Output tokens cost more than input: prefill is parallel, decode is
# sequential and holds KV cache for the life of the request. 2.0 is a
# conservative engineering default and is overridable — it is NOT the weight
# from the VTC paper, which could not be read from this environment.
DEFAULT_OUTPUT_WEIGHT = 2.0

# How far above the fair share an entity must be before it is asked to yield.
# 1.0 would defer anyone even marginally ahead, which thrashes; 2.0 means
# "using more than double your share while someone is starved".
DEFAULT_YIELD_RATIO = 2.0


@dataclass
class AdmissionDecision:
    """Whether an entity should proceed now, and the arithmetic behind it."""
    admit: bool
    entity_id: str = ""
    service: float = 0.0          # weighted tokens this entity has consumed
    fair_share: float = 0.0       # weighted tokens per entity if split evenly
    ratio: float = 0.0            # service / fair_share (0 when no data)
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "admit": self.admit,
            "entity_id": self.entity_id,
            "service": round(self.service, 2),
            "fair_share": round(self.fair_share, 2),
            "ratio": round(self.ratio, 3),
            "reason": self.reason,
        }


def weighted_service(bucket: "TokenBucket",
                     output_weight: float = DEFAULT_OUTPUT_WEIGHT) -> float:
    """Service received by one entity, in output-weighted tokens."""
    return float(bucket.prompt_tokens) + float(bucket.completion_tokens) * output_weight


def new_arrival_service(buckets: list["TokenBucket"],
                        output_weight: float = DEFAULT_OUTPUT_WEIGHT) -> float:
    """Starting service credited to an entity with no history.

    The current minimum, not zero. At zero, a fresh identity outranks every
    established one until it catches up — so anyone could reset their priority
    by rotating API keys. Crediting the minimum gives a genuine newcomer
    immediate access without handing out an exploitable advantage.
    """
    if not buckets:
        return 0.0
    return min(weighted_service(b, output_weight) for b in buckets)


def should_admit(
    entity_id: str,
    buckets: list["TokenBucket"],
    *,
    output_weight: float = DEFAULT_OUTPUT_WEIGHT,
    yield_ratio: float = DEFAULT_YIELD_RATIO,
) -> AdmissionDecision:
    """Decide whether `entity_id` should proceed now.

    Defers only when the entity is using more than `yield_ratio` times an even
    split *and* some other entity is below it — deferring when nobody is
    starved would throttle a single tenant for no one's benefit, which is not
    fairness, just a slower system.

    Never raises, and admits on every uncertainty: an unknown entity, a single
    tenant, or no usage data all yield admit=True. A fairness mechanism that
    fails closed would deny service on missing data, which is far worse than
    the unfairness it prevents.
    """
    if not entity_id or not buckets:
        return AdmissionDecision(admit=True, entity_id=entity_id,
                                 reason="no usage data — nothing to be unfair about")

    services = {b.entity_id: weighted_service(b, output_weight) for b in buckets}
    if len(services) < 2:
        return AdmissionDecision(admit=True, entity_id=entity_id,
                                 reason="single entity — fair share is undefined")

    total = sum(services.values())
    if total <= 0:
        return AdmissionDecision(admit=True, entity_id=entity_id,
                                 reason="no tokens consumed yet")

    # An entity with no bucket is a genuine newcomer: credit it the minimum
    # rather than zero, so identity rotation cannot buy priority.
    mine = services.get(entity_id)
    if mine is None:
        mine = new_arrival_service(buckets, output_weight)

    fair_share = total / len(services)
    starved = min(services.values())

    # Only meaningful if somebody is actually behind this entity.
    if mine <= starved:
        return AdmissionDecision(admit=True, entity_id=entity_id, service=mine,
                                 fair_share=fair_share,
                                 ratio=mine / fair_share if fair_share else 0.0,
                                 reason="least-served entity — always admitted")

    # Compare against the least-served entity, NOT against the even share.
    # With N entities the share-ratio is bounded above by N, so with two
    # tenants a hog taking 98% of all tokens scores 1.96 and slips under a
    # threshold of 2.0 — the gate would be a no-op in the commonest
    # multi-tenant case. Measuring "how many times more than the starved
    # entity" is unbounded and expresses the actual intent.
    #
    # The floor keeps a bucket sitting at zero from deferring every other
    # tenant at once: without it, base=0 makes any nonzero usage infinitely
    # over the limit and the whole system stalls behind one idle account.
    base = max(starved, fair_share * 0.01)
    ratio = mine / base if base > 0 else 0.0

    if ratio > yield_ratio:
        return AdmissionDecision(
            admit=False, entity_id=entity_id, service=mine,
            fair_share=fair_share, ratio=ratio,
            reason=(f"has used {ratio:.1f}x the least-served entity "
                    f"({mine:.0f} vs {starved:.0f} weighted tokens)"),
        )

    return AdmissionDecision(admit=True, entity_id=entity_id, service=mine,
                             fair_share=fair_share, ratio=ratio,
                             reason=(f"within {yield_ratio:.1f}x of the least-served "
                                     "entity"))


def rank_by_service(buckets: list["TokenBucket"],
                    output_weight: float = DEFAULT_OUTPUT_WEIGHT) -> list[str]:
    """Entity ids ordered least-served first — the scheduling order itself."""
    return [b.entity_id for b in
            sorted(buckets, key=lambda b: weighted_service(b, output_weight))]
