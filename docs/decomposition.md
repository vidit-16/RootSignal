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

This is not hypothetical. In the sample data, at the week the seeded supply
disruption begins:

| Segment | Fill rate before | after | Rate effect | Mix effect | **Total contribution** |
| --- | --- | --- | --- | --- | --- |
| BLR \| Fruits | 0.9641 | 0.7609 | **−0.0141** | +0.0229 | **+0.0040** |
| BLR \| Vegetables | 1.0000 | 0.7808 | **−0.0115** | +0.0064 | −0.0065 |

**BLR Fruits fulfils 20 points less of its demand than the week before, and its
total contribution is positive.** Demand moved away from it at the same time, and
the mix effect more than cancels the rate effect. Ranked on total contribution
it does not appear among the worst segments at all — it looks like a segment
that helped.

`explain_movement` therefore ranks on whichever component actually carries the
movement, via `dominant_component`, rather than defaulting to the total.

## Telling a real movement from reshuffling

`component_coherence` reports, for each component, its net movement against the
sum of its absolute movements:

```
coherence = |Σ effect| ÷ Σ|effect|
```

A component whose segment effects are individually large but cancel almost
exactly has not moved the business anywhere. At the disruption boundary:

| Component | Net | Gross | Coherence | Share of net movement |
| --- | --- | --- | --- | --- |
| rate_effect | −0.0261 | 0.0404 | **0.645** | 80.0% |
| interaction_effect | −0.0064 | 0.0099 | 0.652 | 19.7% |
| mix_effect | +0.0001 | **0.2107** | **0.0005** | 0.3% |

Mix has by far the largest gross movement and essentially zero net movement:
weekly demand reshuffles between sixteen fine segments without the total going
anywhere. Reading those large individual mix effects as findings would be
reading noise. The rate effect is smaller in gross terms but moved in one
direction across the business, and carries 80% of the net change.

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
weekly net sales movement of −33,914:

| Dimension | Top segment | Top contribution | Top-3 concentration |
| --- | --- | --- | --- |
| category | Fruits | −38,928 | **0.987** |
| kam_id | K004 | −15,980 | 0.921 |
| region_code | MUM | −20,513 | 0.862 |
| channel | General Trade | −20,116 | 0.800 |

Every row describes the same −33,914. Category concentrates it most, so this
movement is better understood as a category story than a regional one.

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
