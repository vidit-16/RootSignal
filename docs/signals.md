# The RootSignal engine

## What a signal is

A RootSignal is a metric movement that has been located, corroborated,
quantified and weighed, together with what should be looked at next.

It is explicitly **not** a diagnosis. Every signal states what its evidence is
*consistent with*, names the leading alternative explanation, and says whether
the data supports that alternative too. A test asserts that no signal output
contains "caused by", "due to", "the root cause" or similar phrasing.

## How a signal is assembled

```
period summary          which metric moved, and by how much   (analysis)
      |
      v
decomposition           which segments carry the movement     (decomposition)
      |
      v
evidence                what the surrounding metrics did      (signals.evidence)
      |
      v
pattern                 what that combination is consistent with
      |
      v
impact                  what it is worth, and on what basis   (impact)
      |
      v
confidence              how much the evidence supports it
      |
      v
priority                what to look at first
```

Every number is computed deterministically in Python. The prose fields are
assembled from those numbers by fixed templates. **No language model produces or
touches any figure in a signal.** An optional explanation layer would take
`signal.as_dict()` and write more fluently, but it would be restating these
numbers, never deriving them.

## Selecting segments

Signals are raised for the segments carrying the movement, chosen by the
decomposition component that actually drives it rather than by total
contribution. For a rate those differ sharply, and picking the wrong one drops
the real signal off the list entirely — see
[decomposition.md](decomposition.md).

## Evidence

For each candidate segment the engine records how the surrounding metrics moved
over the same two periods: ordered units, fulfilled units, cancellations, net
sales, order count, fill rate, and — where the grain allows — available stock,
stockout rate and receipts.

A change smaller than 5% is recorded as the metric holding its level rather than
moving. Weekly figures in a thin segment wander by a few percent without
anything having happened.

**Inventory aggregates differently from everything else.** Stock levels are
snapshots and are averaged across a period; receipts and movements are flows and
are summed. Summing a level would report a week's stock as seven times its
actual size. Inventory is also absent by customer, channel and manager, so at
those grains the engine records a note saying the evidence was unavailable
rather than quietly omitting it — a reader can then tell the difference between
evidence that pointed nowhere and evidence that never existed.

## Pattern classification

| Pattern | Recognised when | Consistent with |
| --- | --- | --- |
| `fulfilment_constraint` | Demand held or rose, and fulfilment did not keep pace | A supply or fulfilment problem |
| `demand_softness` | Ordered units fell, with fulfilment following | Weaker demand |
| `portfolio_mix_shift` | The movement is carried by mix rather than rate | A change in what was sold, not how well |
| `unclassified` | No supporting metric moved decisively | Nothing yet; look closer |

### Shipping more units is not proof that fulfilment held up

This distinction was wrong in the first implementation and is worth recording.

The original rule required fulfilled units to **fall** before a movement counted
as a fulfilment constraint. Applied to the sample data it misclassified the
clearest case in the dataset:

| BLR Fruits | Before | After | Change |
| --- | --- | --- | --- |
| Ordered units | 167 | 230 | **+37.7%** |
| Fulfilled units | 161 | 175 | +8.7% |
| Fill rate | 0.964 | 0.761 | **−21.1%** |
| Available stock | 28.8 | 18.3 | −36.3% |
| Stockout rate | 0.000 | 0.214 | — |

Demand surged 38%; fulfilment managed 9%. That segment served a far smaller
share of its orders than the week before, and its stock and stockout figures say
why. But because it *shipped more units*, the rule rejected it and the signal
came back `unclassified`.

The criterion is now whether fulfilment **kept pace with the demand placed on
it**, which is what a falling fill rate measures. That is the commonest shape a
supply constraint takes in a growing segment, and the naive rule misses exactly
that case. A regression test covers it.

### Both directions get a live alternative

`demand_softness` does not close the question. Suppressed demand looks identical
to weak demand: if stock ran short before orders fell, customers may simply have
stopped ordering what they could not receive. When stock fell or stockouts rose
alongside falling demand, the engine marks the supply alternative as **supported**
and says so, which lowers the signal's confidence.

## Impact

```
shortfall units = ordered_now x fill_rate_before − fulfilled_now
impact          = shortfall units x realised average selling price
```

Two choices matter here.

**It measures the deterioration, not all unfulfilled demand.** A segment that has
always fulfilled 90% of orders is not losing money every day by failing to reach
100%. What is worth quantifying is the gap that opened up. A broader
`estimate_unfulfilled_demand` exists for the perfect-fill-rate view and is
correspondingly weaker as evidence.

**It uses realised price, not list price.** Discounting is already reflected, so
the figure is not inflated with prices nobody paid.

Every estimate carries its basis and its assumptions, all of which push the
number **high**:

- Unfulfilled demand is valued at the price realised on fulfilled units.
- No substitution: a customer denied one product is assumed to buy nothing else.
- No recovery: demand served later still counts as a shortfall now.
- **This is associated revenue, not a measured loss.**

## Confidence

Confidence is a count of named criteria, each recorded with the number behind
it. It is deliberately not a tuned score — a single opaque number would be
impossible to argue with, and the point is to let someone reject one specific
step of the reasoning.

| Criterion | Met when |
| --- | --- |
| `movement_stands_out` | Movement ≥ 1.5x the segment's own typical swing |
| `movement_persisted` | Same direction for ≥ 2 consecutive periods |
| `segment_carries_the_movement` | Segment holds ≥ 15% of the movement in the ranking component |
| `other_metrics_agree` | ≥ 2 supporting observations move consistently |
| `alternative_not_supported` | The leading alternative is not supported by the data |
| `movement_is_directional` | The dominant component's coherence ≥ 0.30 |

5 or 6 met is `high`, 3 or 4 `medium`, below that `low`.

Movement is judged against **the segment's own history**, not a global
threshold, so a volatile segment must move further to earn the same confidence.

Concentration is measured **within whichever component the ranking used**. An
earlier version measured a segment's share of total contribution while ranking
on the rate effect; because the mix component carried a large gross movement
that cancelled to nothing, that denominator was inflated with noise and made
every segment look like a negligible part of the movement. The clearest signal
in the dataset scored 2.1% and failed the criterion. Measured within the rate
effect it is 34.8%.

**Confidence is not a probability and none of these criteria is a statistical
test.** It describes how much of the available corroboration lined up.

## Priority

```
priority = |impact| x confidence weight        (high 1.0, medium 0.6, low 0.3)
```

A ranking heuristic, not a measurement. A low-confidence signal is pushed down
rather than hidden, so a large but weakly evidenced movement still reaches the
reader. Both components are visible on every signal, so anything can be re-ranked
by impact alone or filtered by confidence.

## Worked output

Reproduce with:

```bash
python scripts/detect_signals.py --current-period 2026-02-16 --comparison-period 2026-02-09
```

| Segment | Movement | Likely driver | Impact | Confidence | Priority |
| --- | --- | --- | --- | --- | --- |
| BLR \| Fruits | −0.2032 | fulfilment_constraint | 9,173.86 | high (6/6) | 9,173.86 |
| BLR \| Vegetables | −0.2192 | fulfilment_constraint | 3,402.37 | high (5/6) | 3,402.37 |
| MUM \| Herbs | −0.0426 | demand_softness | 461.69 | medium (3/6) | 277.01 |

The top signal renders as:

> fill_rate in BLR | Fruits moved −0.2032 (−21.1%) for the 2026-02-16 period
> versus 2026-02-09. The movement in BLR | Fruits is **consistent with** a
> fulfilment or supply constraint: fulfilment did not keep pace with the demand
> placed on it. Supporting evidence: fulfilment grew more slowly than demand
> (+8.7%); ordered units rose (+37.7%); available stock fell (−36.3%); stockout
> rate rose. Estimated impact: 9,173.86 based on units not fulfilled relative to
> the segment's prior fill rate, valued at its realised average selling price.
> Confidence: high (6 of 6 criteria met). Alternative considered: demand weakened
> and the fall in fulfilment simply followed it — not supported by the order
> volumes. Recommended investigation: review inventory availability and
> replenishment for BLR | Fruits, starting with the SKUs carrying the largest
> unfulfilled volume.

Nothing in the call names Bengaluru, Fruits, Vegetables, or supply. The engine is
given a fill-rate movement across every region and category and reaches that
reading from the evidence. The third signal reaching a *different* conclusion on
the same run matters: the classifier discriminates rather than labelling
everything a supply problem.

## Limitations

1. **Association, not causation.** Every signal says what the evidence is
   consistent with. Nothing here tests a causal claim, and a corroborated
   pattern can still have a different explanation.
2. **Confidence is not a probability.** It counts criteria. A `high` signal is
   one where the available corroboration lined up, not one that is 90% likely.
3. **Impact is an estimate that runs high**, for the reasons listed with its
   assumptions. It should never be quoted as measured lost revenue.
4. **Patterns are a fixed, small vocabulary.** Pricing, competitive, seasonal and
   data-quality explanations are not modelled; movements driven by them land in
   `unclassified`, which is the honest answer but not a helpful one.
5. **Two periods at a time.** A signal compares one period with one other.
   Persistence is counted, but no trend is fitted.
6. **No significance testing.** A thin, volatile segment can clear the criteria
   on noise. The `movement_stands_out` criterion compares against that segment's
   own history, which helps, but it is not a test.
7. **Inventory evidence is unavailable by customer, channel and manager**, so
   signals at those grains rest on demand and fulfilment evidence alone and are
   correspondingly weaker.
