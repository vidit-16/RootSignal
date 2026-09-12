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
| `fulfilment_constraint` | Fulfilment did not keep pace with demand — the fill rate fell | A supply or fulfilment problem |
| `demand_softness` | Orders fell while the fill rate held: fulfilment tracked demand down | Weaker demand |
| `portfolio_mix_shift` | The movement is carried by mix rather than rate | A change in what was sold, not how well |
| `unclassified` | No supporting metric moved decisively | Nothing yet; look closer |

### Two ways this rule was wrong

Both errors are recorded because both were found by testing against data rather
than by reasoning about it, and both are easy to repeat.

**Shipping more units is not proof that fulfilment held up.** The first rule
required fulfilled units to *fall*. A segment whose demand surged 38% while its
shipments rose 9% was serving a far smaller share of its orders, and its fill
rate said so — but because it shipped more units than the week before, the rule
rejected it and the signal came back `unclassified`. That is the commonest shape
a supply constraint takes in a *growing* segment.

**A modest demand dip does not make a fulfilment collapse a demand story.** The
corrected rule still required demand to have held. On the current data:

| BLR Fruits, 2026-02-09 → 2026-02-23 | Before | After | Change |
| --- | --- | --- | --- |
| Ordered units | 180 | 169 | −6.1% |
| Fulfilled units | 175 | 119 | **−32.0%** |
| Fill rate | 0.972 | 0.704 | **−27.6%** |
| Available stock | — | — | −49.1% |

Demand dipped 6%; fulfilment fell 32%. Requiring demand to have held classified
this as `demand_softness`, which is plainly wrong.

The rule is now simply: **a falling fill rate is arithmetic proof that
fulfilment lagged demand.** The ratio cannot fall unless fulfilled units fell by
more than ordered units did. Demand softness is reserved for the case where
fulfilment tracked demand down and the ratio held.

The mirror of that also had to be fixed: falling shipments alone prove nothing,
since a segment shipping less because less was asked of it is serving its demand
perfectly well. Shipments falling only indicate a constraint when the order book
did not fall with them.

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
effect it is 33.4%.

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
python scripts/detect_signals.py
```

No periods are passed. The constraint occupies the last complete week, so the
default comparison — the latest complete week against the one before — is
already the comparison worth making.

| Segment | Movement | Likely driver | Impact | Confidence | Priority |
| --- | --- | --- | --- | --- | --- |
| BLR \| Fruits | −0.2889 | fulfilment_constraint | 8,970.60 | high (5/6) | 8,970.60 |
| BLR \| Vegetables | −0.2765 | fulfilment_constraint | 6,409.62 | high (5/6) | 6,409.62 |
| DEL \| Premium | −0.0217 | unclassified | 3,180.75 | low (2/6) | 954.22 |

The top signal renders as:

> fill_rate in BLR | Fruits moved −0.2889 (−29.1%) for the 2026-02-23 period
> versus 2026-02-16. The movement is **consistent with** a fulfilment or supply
> constraint: fulfilled units fell while ordered units did not. Supporting
> evidence: fulfilled units fell (−16.2%); ordered units rose (+18.2%);
> available stock fell (−47.7%); stockout rate rose. Estimated impact: 8,970.60
> based on units not fulfilled relative to the segment's prior fill rate, valued
> at its realised average selling price. Confidence: high (5 of 6 criteria met).
> Alternative considered: demand weakened and the fall in fulfilment simply
> followed it. **Demand did not weaken over this period, so a demand-led
> explanation is not supported by the order volumes.** Recommended
> investigation: review inventory availability and replenishment for
> BLR | Fruits.

Note the criterion it **fails**, and which one it does not. The alternative is
dismissed here on evidence — orders rose 18% while shipments fell 16%, so a
demand-led reading is not available. What the signal will not claim is that the
movement has **persisted**: it has moved this way for one period, and the
threshold is two. A step change cannot satisfy that on the period it happens,
and the engine does not pretend otherwise. It scores 5 of 6 rather than 6, on
the one criterion that only time can settle.

## Evaluation: can it tell situations apart?

Validating a diagnostic engine against one planted event only shows it finds
what it was pointed at. The dataset therefore carries **four deliberately
different situations**, and the engine is measured on whether it distinguishes
them:

| Scenario | Region | What was planted | Expected reading |
| --- | --- | --- | --- |
| `blr_supply_constraint` | BLR | Fulfilment and stock fall while demand holds | `fulfilment_constraint` |
| `hyd_demand_softness` | HYD | Orders fall away; fulfilment stays healthy | `demand_softness` |
| `del_mix_shift` | DEL | Demand tilts toward a structurally weaker category | `portfolio_mix_shift` |
| `mum_control` | MUM | **Nothing**, ever | Silence |
| `blr_quiet_period_control` | BLR | **Nothing yet** — five weeks before the constraint | Silence |

Ground truth travels with the data in `dataset_manifest.json`, so the evaluation
cannot drift from what was actually generated. Reproduce with:

```bash
python scripts/evaluate_signals.py
```

| Confidence floor | Correctly classified | Recall | False-alarm signals | Controls silent |
| --- | --- | --- | --- | --- |
| **high** | 2 of 3 | 0.67 | **0** | **2 of 2** |
| **medium** | **3 of 3** | **1.00** | 12 | 0 of 2 |

This is a precision/recall trade-off, measured rather than asserted. At a high
floor the engine raises nothing it cannot support and stays completely silent on
both control windows, at the cost of missing the mix shift — the subtlest of the
three. At a medium floor it classifies all three correctly and admits twelve
false alarms.

**The controls matter as much as the rest.** A detector that flags something
every week is not detecting anything, so a window with nothing planted is scored
on whether the engine says nothing. `min_confidence` exists for that: without it
the engine always returns its top segments, and a quiet week yields a ranked list
of ordinary noise.

There are two of them, and they are deliberately different. `mum_control` asks
whether a segment that never moves stays silent. `blr_quiet_period_control` asks
whether a segment that moves *later* stays silent until it does — Bengaluru is
where the supply constraint eventually lands, and that window closes five weeks
before it starts. A detector that smeared a real event backwards, or that found
meaning in the run-up to one, would pass the first and fail the second. They also
sit on different metrics, so a false positive confined to the fulfilment path or
the demand path cannot hide behind the other.

The second one earned its place immediately. It raises **six** false alarms at a
medium floor against `mum_control`'s three, so the rate is not uniform and the
single-control figure this table used to report was an estimate from one
observation. Two is still few. It is enough to show the spread is real.

### Choosing the metric matters

A demand decline is invisible in fill rate, because a segment that ships less of
a smaller order book has not changed how well it fulfils. The HYD scenario is
therefore evaluated on `ordered_units`, not `fill_rate`. That is ordinary
analytical practice rather than a concession: you diagnose a service problem
through a service metric and a demand problem through a volume one.

### Transition weeks are harder, and honestly so

The supply constraint begins mid-week. In the week that straddles it only five
days are affected, and a segment whose weekly order volume ordinarily swings by
a third can show a demand dip large enough to dominate that week's evidence. The
engine reports what the numbers say, which is why the evaluation compares fully
affected windows and why a single-period signal deserves less weight than a
sustained one. A test asserts this behaviour rather than leaving it to be
discovered.

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
8. **The evaluation covers five scenarios on one dataset.** Pricing, competitive,
   seasonal and data-quality explanations are not planted and not modelled, and
   a recall of 1.00 across three planted situations is not a claim about
   behaviour on situations that were never tested.
9. **Small segments rank low.** Ranking is by absolute contribution, so a 60%
   collapse in a thin segment can sit below a modest move in a large one. The
   HYD demand scenario is a 26-unit movement and appears at rank 2 of 4.
