# Driver decomposition

## Why this layer exists

Trend and variance analysis establish that a metric moved. This layer
establishes **where** the movement came from: which regions, categories,
channels, managers, customers or SKUs account for it, and how much of it each
one carries.

It answers "where did this happen". It does not answer "why". A segment that
accounts for most of a movement is where the movement is concentrated, not its
cause. The wording throughout is deliberate — *leading segment*, *accounts for*,
*concentrated in* — and never *caused by*.

## Contributions are calculated before anything is ranked

Ranking a metric level would simply surface the largest segments. That is a
different question: a large segment that barely moved explains nothing, and a
small one that collapsed may explain everything. Every function here computes
contributions first and sorts afterwards.

## Two metrics, two decompositions

The distinction below is the core of this layer.

### Additive metrics

Net sales, units, ordered units and order counts are sums over their segments,
so a movement splits cleanly:

```
contribution(segment) = value_after(segment) − value_before(segment)
```

These reconstruct the total exactly. A segment absent from one period is treated
as zero there, so a segment that appeared or disappeared contributes its full
value rather than dropping out of the reconciliation.

### Rates need more care

**A total fill rate can fall while every single segment's fill rate improves.**
That is not a paradox; it happens whenever demand shifts toward segments that
fill less well. Subtracting segment rates would report an improvement everywhere
and leave the decline unexplained.

A rate is a weighted average, where `w` is a segment's share of the denominator
and `r` is its own rate:

```
total rate = Σ w · r
```

so each segment's movement separates into three terms that add back exactly:

| Term | Formula | Meaning |
| --- | --- | --- |
| **Rate effect** | `w_before · (r_after − r_before)` | The segment genuinely fulfils a different share of what it is asked for |
| **Mix effect** | `(w_after − w_before) · r_before` | Demand moved toward or away from this segment |
| **Interaction** | `(w_after − w_before) · (r_after − r_before)` | The two moved together |

The distinction is operational, not academic. A rate effect means fulfilment
broke down and belongs with supply. A mix effect means the business sold a
different blend of things and belongs with demand planning. They call for
different responses and different owners.

A segment with no volume in one period has no rate there. Its rate is held equal
to the other period's, so its whole movement lands in mix — which is what
demand entering or leaving actually is.

## Ranking a rate on the wrong column hides the answer

A segment whose fill rate collapses can still show a **positive** total
contribution, if demand grew into it while it was failing. Ranked on the total
it reads as a segment that helped, and the collapse never surfaces at all.

`explain_movement` therefore ranks on whichever component actually carries the
movement, via `dominant_component`, rather than defaulting to the total.
`tests/test_decomposition.py` asserts the property on a constructed case rather
than on the sample data: which real segment exhibits it depends on that week's
demand mix, and a property this important should not be tested only when the
data happens to oblige.

## Telling a real movement from reshuffling

`component_coherence` reports, for each component, its net movement against the
sum of its absolute movements:

```
coherence = |Σ effect| ÷ Σ|effect|
```

A component whose segment effects are individually large but cancel almost
exactly has not moved the business anywhere. For the disruption window
(2026-02-16 to 2026-02-23) across region and category:

| Component | Net | Gross | Coherence | Share of net movement |
| --- | --- | --- | --- | --- |
| rate_effect | −0.0285 | 0.0671 | **0.424** | 74.7% |
| interaction_effect | −0.0090 | 0.0162 | 0.552 | 23.5% |
| mix_effect | +0.0007 | **0.2172** | **0.003** | 1.8% |

Mix has by far the largest gross movement and essentially zero net movement:
weekly demand reshuffles between sixteen fine segments without the total going
anywhere. Reading those large individual mix effects as findings would be
reading noise. The rate effect is smaller in gross terms but moved in one
direction across the business, and carries 75% of the net change.

The disrupted segments are where that rate effect sits:

| Segment | Fill rate before | after | Rate effect | Mix effect |
| --- | --- | --- | --- | --- |
| BLR \| Fruits | 0.9930 | 0.7041 | **−0.0180** | +0.0124 |
| BLR \| Vegetables | 0.9779 | 0.7014 | **−0.0218** | +0.0186 |
| BLR \| Herbs | 0.9412 | 0.9804 | +0.0017 | +0.0219 |
| BLR \| Premium | 0.9597 | 0.9630 | +0.0002 | +0.0065 |

Coherence is a description of how a component behaved. It is not a significance
test and does not carry a confidence level.

## Decompositions are alternative views, never additive

Each dimension gives a **complete, independent** decomposition of the same
movement. Region explains 100% of it. Category explains 100% of it. From
different angles.

**Adding a region contribution to a category contribution double-counts the
movement.** To go deeper, decompose within the leading segment rather than
summing across dimensions.

`compare_dimensions` reports which cut localises a movement best. For the latest
weekly net sales movement of −8,535:

| Dimension | Top segment | Top contribution | Top-3 concentration |
| --- | --- | --- | --- |
| channel | Modern Trade | −30,987 | **0.975** |
| kam_id | K001 | −30,553 | 0.956 |
| category | Premium | −7,341 | 0.920 |
| region_code | MUM | −21,523 | 0.908 |

Every row describes the same −8,535. Channel concentrates it most, so this
movement is better understood as a channel story than a regional one. Note that
the top contribution far exceeds the net movement in three of the four cuts:
segments are offsetting heavily, which is exactly when `contribution_share`
stops being meaningful.

## Shares, and when they are withheld

| Field | Meaning |
| --- | --- |
| `contribution` | The segment's movement, in metric units |
| `contribution_share` | Its share of the **net** movement |
| `share_of_absolute_movement` | Its share of **all** movement, ignoring sign |

`contribution_share` can legitimately exceed 100% when segments offset: if the
total fell by 100 because one segment fell 150 while another rose 50, the first
segment accounts for 150% of the net decline. That is correct and standard.

When offsetting becomes extreme — net movement under 5% of gross — the
denominator is a small residual of large opposing moves, and shares of it
describe arithmetic rather than business. `contribution_share` is then reported
as undefined. `share_of_absolute_movement` stays well defined, so magnitude
ranking still works.

## Limitations

1. **This layer locates movement; it does not explain it.** Co-movement between
   a segment and a total is not causation, and nothing here tests for one.
2. **Coherence is descriptive.** It separates directional movement from
   reshuffling. It is not a significance test, and a high-coherence component in
   a thin segment can still be noise.
3. **No significance testing at all.** A large contribution from a small,
   volatile segment ranks alongside one from a large, stable segment. Weighing
   that is the impact layer's job.
4. **Nested drill-down is manual.** Decomposing within a leading segment means
   building a summary at the finer grain and decomposing again. There is no
   automatic tree search, deliberately: an automatic one would invite reading
   whichever split looked most dramatic.
5. **Two periods only.** Contributions compare one period against one other. A
   movement sustained across several periods must be examined period by period.
