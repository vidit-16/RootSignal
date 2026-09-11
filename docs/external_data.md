# Running on real public data

## Why

A system validated only against the data it generates has proved that it is
self-consistent, not that it works. The obvious objection to any portfolio
analytics project is that the data was built to suit it.

So the pipeline is run against **UCI Online Retail II**: real transactions from
a UK online retailer, December 2009 to December 2011. A million invoice lines,
40 countries, and the data quality of an operational system.

```bash
python scripts/run_external_dataset.py
```

The dataset downloads on first run (~44 MB, no account needed) and is cached.
**Nothing in the analytical layers changed to accommodate it.** Only an adapter
exists.

Source: <https://archive.ics.uci.edu/dataset/502/online+retail+ii> · CC BY 4.0.

## An adapter declares what its dataset cannot answer

This dataset carries sales and nothing else — no order book, no stock, no plan.
The temptation is to synthesise the missing facts so the whole pipeline runs.
That produces a system that appears to work and quietly answers questions the
data cannot support.

Instead the adapter states its capabilities, and unsupported analyses are
skipped with the reason:

```
Available: fact_sales
Absent:    fact_orders, fact_inventory, fact_targets, fact_kam_targets

Can answer:  sales_kpis, forecasting, trend_analysis, driver_decomposition
Cannot answer:
  - fulfilment_analysis needs fact_orders, which this dataset does not contain.
  - supply_signals needs fact_orders, fact_inventory, ...
  - target_variance needs fact_targets, ...
```

**Returns are the sharpest case.** This data has 22,951 return lines, and it
would be easy to map them to `cancelled_units` and compute something called a
fill rate. That number would then read as a supply failure when customers had
simply sent things back. Returns are reported as returns, and fulfilment
analysis is declared unavailable.

## What the real data broke

Three things, none of which the generated data could have found.

### The composite key does not survive real invoices

The business model keys a sales line on `(order_id, sku_id, sales_type)`, and
that key is enforced in both the SQL schema and the Python contracts. It held
throughout development because generated orders carry each product once.

**45,299 real invoice lines repeat a product already on the same invoice** —
usually with a different quantity, sometimes a different price. A real invoice
can genuinely list one product twice.

The adapter consolidates those lines, adding units and weighting price by
quantity so volume and revenue are unchanged, and records how many rows that
affected. The alternative — adding a line number to the key — would be a
reasonable modelling choice, and is noted here as one the project did not take.

### Cleaning had no rule for a line that cannot be a sale

Real transaction data carries samples, adjustments and corrections priced at
zero, and occasionally below it. The cleaning rules had been written against the
defects the generator seeds, and none of them covered this.

`fact_sales` lines priced at or below zero, or with no units, are now
quarantined: they cannot be repaired into a sale without inventing a price
nobody paid. **2,590 lines** were quarantined on the real dataset. The rule is
inert on generated data, which contains none.

### An empty fact left a column untyped

With no order fact, the mart's outer join produced object-dtype columns that
were then filled and used in arithmetic. It warned rather than failed, and would
have degraded quietly. Values are now coerced before filling.

## What it found

| | Generated | Real |
| --- | --- | --- |
| History | 60 days | **739 days** |
| Sales lines | 7,134 | **1,044,420** |
| Backtest folds | 4 | **49** |
| Scored observations | 28 | **343** |

**Validation and cleaning**, on the data exactly as published:

- 3 errors found — negative prices and a reconciliation failure
- 2,590 unsellable lines quarantined
- **0 errors after cleaning**

**Forecast accuracy** on daily revenue, 49 folds:

| Method | WAPE | vs naive |
| --- | --- | --- |
| **seasonal_mean_7** | **0.4127** | **+38.8%** |
| seasonal_naive_7 | 0.4157 | +38.4% |
| ses | 0.5320 | +21.1% |
| moving_average_7 | 0.5329 | +21.0% |
| naive | 0.6745 | baseline |

`seasonal_mean_7` wins here as it does on the generated data — two unrelated
datasets, the same conclusion. The **absolute** error is far higher (0.41
against 0.076) because real daily retail revenue is enormously more volatile:
the retailer is closed some days and Christmas dominates the year. The
improvement over the baseline is the transferable result; the error level is a
property of the business.

**Driver decomposition** on the latest month's +354,517 movement attributes
**83.5% to the United Kingdom**, with the Netherlands, Australia and Singapore
offsetting slightly — a concentration a reader could act on.

### The return rate, and two ways of reading it wrongly

Returns are the one non-sales metric this dataset genuinely supports, so the
rate decomposition was extended to cover them. A return rate behaves exactly
like a fill rate arithmetically — a weighted average of segment rates — so it
splits into a rate effect, a mix effect and an interaction the same way. It is
not a fill rate and is never reported as one; the pattern vocabulary built for
fulfilment does not apply, because the reasons goods come back are not in this
data and naming them would be invention.

Getting a defensible number out of it took two corrections, and both are
recorded because both produced a confident and wrong answer first.

**Filtering thin segments changes the answer.** Forty countries appear, and most
carry a handful of units where a rate swings from 75% to 0% without meaning
anything. The obvious response is to drop them. Dropping removes volume from the
denominator, which changes the weight of every country that remains, and so
changes the movement being explained. A first pass that filtered to countries
above 500 units reported the rate effect at 89% of the movement. That number
described a business with a different volume distribution from the real one.

Thin segments are now **folded** into `Other (below volume floor)`: their
numerators and denominators are added together, so the weights and the total are
untouched and the effects still reconstruct the observed movement exactly.

**The last month is not a month.** The file stops on 9 December 2011. Returns
keep arriving against goods bought in November, so December shows returns
against nine days of sales — a 24-point jump that is a calendar artifact rather
than a change in behaviour. The period completeness metadata already existed for
exactly this case; the external script now uses it.

With both corrected, comparing the last two complete months:

| Component | Net | Coherence | Share |
| --- | --- | --- | --- |
| **rate_effect** | **−0.0538** | **0.92** | **87.3%** |
| mix_effect | +0.0049 | 0.63 | 7.9% |
| interaction_effect | −0.0030 | 0.54 | 4.8% |

The return rate fell 5.19 points, and 87% of that is countries genuinely
returning less rather than demand moving between them. A coherence of 0.92 says
the rate effect moved in one direction across the business instead of cancelling
out. The United Kingdom carries it: 10.9% returned to 4.5%.

### A guard the real data earned

While checking that, the decomposition was found to reconstruct −0.051933
against an observed −0.051880. Small enough to dismiss as floating point. It was
not floating point.

The identity `total rate = Σ weight × rate` needs every unit of the numerator to
sit behind some denominator. **The Czech Republic returned goods in a month it
sold nothing.** That numerator counts toward the total rate, but its segment is
weighted at zero, so it drops out of the reconstruction. The effects would still
have looked entirely plausible — they would have explained a movement that did
not happen, which is worse than an error.

`decompose_rate` now verifies the reconstruction and refuses rather than
returning effects that do not add back:

```
Decomposing 'return_rate' does not reconstruct the movement:
observed -0.051880, effects sum to -0.051933.
Segments carry a 'return_rate' numerator with no 'sold_units' behind it:
['Czech Republic']. A volume floor (min_share) folds them into a segment
that has volume, which restores the identity.
```

The same cause forced a fix one layer up: `returns_by_period` joined returns
onto sales with a left join, which silently discarded returns arriving in a
period that sold nothing — **708 units**. It is an outer join now, and asserts
that no returned unit is lost across it.

## Why no public dataset carries everything

The obvious follow-up is whether a real dataset exists with all five facts, so
the whole pipeline could run on real data. It almost certainly does not, and the
reasons are structural rather than a matter of searching harder.

**Targets are trade secrets.** This is the hard blocker. Sales forecasts and
plans are legally protected as proprietary precisely because they reveal
strategy, cost structure and product direction to competitors. The
budget-versus-actual datasets that do circulate on Kaggle and Hugging Face are
explicitly dummy data built for teaching, not real company plans. A dataset can
have real transactions or plausible targets; not both.

**A fill rate publishes your failures.** In-full delivery needs ordered quantity
against shipped quantity, line by line. No company releases "we failed to
deliver 8% of what customers asked for". The closest public data reaches is the
*on-time* half of OTIF — DataCo Smart Supply Chain carries scheduled against
actual shipping days, and Olist carries estimated against actual delivery dates.
Neither carries in-full.

**Stock positions expose capacity.** Daily inventory by product and location
reveals warehouse capacity, supplier relationships and buying patterns, so it
stays internal.

### Which is why a generated dataset is the right tool, not a fallback

Even a complete real dataset would be unusable for the thing that matters most.
**A detector cannot be validated against data whose true answer is unknown.**
The four-scenario evaluation — three of three classified correctly at a medium
confidence floor, zero false alarms at a high one — is only possible because the
ground truth was planted. Run the engine on real data and you get a signal with
no way to score it.

So the two datasets answer two different questions, and neither could answer the
other's:

| Question | Answered by |
| --- | --- |
| Does the pipeline survive messy real data? | Online Retail II |
| Does the engine tell causes apart? | Generated data, with known ground truth |

The generated dataset is not a stand-in for real data that could not be found.
It is the only kind of data that can measure whether a diagnosis is correct.

## Limitations

1. **Half the system is not exercised.** Fill rate, inventory, targets, KAM
   performance and the signal engine all need facts this dataset lacks. The
   generated dataset remains the only one that exercises the full pipeline.
2. **Two dimensions are approximations.** Country stands in for region, and
   category is derived from the first word of a product description. Both are
   labelled as approximations in the capability notes.
3. **One external dataset.** Two would be better evidence of generality than
   one, and the adapter interface exists so that adding another is a mapping
   rather than a rewrite.
4. **The download needs network access**, so it is a script rather than part of
   the test suite. The adapter tests use a small in-memory fixture shaped like
   the real file, including its defects.
