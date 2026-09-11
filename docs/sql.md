# The SQL layer

## SQL here is executed, not illustrated

Every `.sql` file in this repository is run by the test suite. The schema is
created, the cleaned data is loaded under it with foreign keys enforced, the
staging and mart views are registered, and each analytical query is executed and
checked. A query that stopped working would fail CI rather than sit in the repo
looking plausible.

```
sql/
  schema.sql        tables, composite keys, constraints
  staging/          enriched views over the cleaned facts
  marts/            the commercial mart
  analytics/        business questions
```

`src/rootsignal/sql/database.py` builds the database and runs the queries, so
the SQL is reachable from Python, from tests, and from the dashboard.

## One definition, two implementations

The commercial mart exists in Python (`build_commercial_mart`) and in SQL
(`mart_commercial_daily`). **A test asserts they agree row for row**, across
3,109 rows and eleven metric columns, to within 1e-9.

This is the guarantee that matters. Without it, a fill rate quoted from a
dashboard could disagree with one quoted from a query, and neither would be
obviously wrong. Introducing the classic double-counting error into the SQL —
`COUNT(order_id)` instead of `COUNT(DISTINCT order_id)` — fails the parity tests.

### One column is compared to the cent

`aov` is asserted equal within one cent rather than exactly. NumPy rounds exact
halves to even; SQLite rounds them away from zero. An average landing precisely
on a half-cent therefore differs by 0.01, in either direction, on about 5% of
rows. The calculation is identical and only the display tie-break differs, so
the test asserts the thing that matters at the precision that matters.

### Multi-SKU orders need their own fixture

The generated data puts a single SKU on every order, so `COUNT(order_id)` and
`COUNT(DISTINCT order_id)` return the same number on it. A distinct-count
regression in the mart would pass unnoticed on the real dataset — and did, when
first tested. Three tests therefore load a small synthetic dataset whose single
order spans two SKUs, so the protection is genuinely exercised.

## Staging

| View | Mirrors | Notes |
| --- | --- | --- |
| `stg_sales` | `enrich_sales` | Adds product, customer, manager, geography; derives gross sales and discount |
| `stg_orders` | `enrich_orders` | Resolves `kam_id` through the customer master, since a manager owns customers rather than orders |
| `stg_inventory` | — | Kept separate: inventory sits at SKU x warehouse x day |
| `stg_targets` | — | Commercial plan and KAM quotas |

Every join is many-to-one on a unique dimension key, so no view changes the
grain of its fact. Tests assert the row counts are unchanged.

## The mart

`mart_commercial_daily` sits at **date x region x category x channel x
sales_type**, matching `DEFAULT_COMMERCIAL_GRAIN`.

Two rules are enforced in the SQL itself:

- **Facts are aggregated separately, then joined on the grain.** Joining
  `fact_sales` to `fact_orders` directly would multiply rows whenever an order
  carries several SKUs.
- **Ratios are computed from summed components at this grain**, never rolled up
  by averaging a finer-grained ratio.

The grain key is built as a `UNION` of both aggregates rather than a `FULL OUTER
JOIN`, which keeps the statement portable and handles the real case of an order
placed but never fulfilled, which produces no sales row at all.

## Analytical queries

| Query | Question |
| --- | --- |
| `daily_sales_tracker` | How is trade running today, against yesterday and against plan? |
| `fill_rate_by_segment` | Which region and category combinations are meeting the service target? |
| `primary_secondary_mix` | How is sell-in splitting against sell-through, by region and month? |
| `target_variance` | Where is the plan being missed, and by how much? |
| `kam_scorecard` | How is each key account manager tracking against quota? |
| `weekly_movers` | Which segments moved the business week over week, and by what share? |
| `supply_watchlist` | Where did fulfilment fall while demand held up? |

Run one with:

```bash
python scripts/run_sql_query.py supply_watchlist
```

### The watchlist cross-validates the signal engine

`supply_watchlist` is the fulfilment-constraint pattern expressed in SQL: a
windowed query that flags segments whose fill rate fell at least 5 points while
demand held within 5% of the prior week.

It shares **no code** with the Python RootSignal engine, which reaches its answer
through decomposition and evidence gathering. For the disruption week both
independently return **BLR Vegetables and BLR Fruits** as the top rows, and a
test asserts the agreement. Two implementations converging is real corroboration
that the business logic produces the result, not one particular implementation
of it.

Demand holding up is the discriminator in the query, as it is in the engine: a
segment fulfilling less of a shrinking order book is a different problem from
one that cannot keep up with a growing one, and only the second is a supply
story.

## Portability

SQLite is used because it needs no server and makes the SQL runnable anywhere
the tests run. The statements stay close to portable SQL: window functions,
common table expressions and standard aggregates, with no SQLite-specific
extensions beyond `DATE(..., 'weekday 0', '-6 days')` for Monday-start weeks and
`STRFTIME` for month keys. A PostgreSQL port would replace those two idioms.

Week boundaries are asserted to match the Python period layer exactly, so
weekly figures tie out between the two implementations.

## Limitations

1. **Weekly queries report `days_observed` rather than excluding partial
   weeks.** The Python layer drops incomplete periods by default; the SQL
   surfaces the day count and leaves the judgement to the reader. A partial week
   compared against a full one still misleads if that column is ignored.
2. **There is one mart.** Period aggregation happens inside each analytical
   query rather than in a set of pre-aggregated weekly and monthly marts. That
   keeps period logic in one place per query at the cost of some repetition.
3. **No incremental build.** Views are recomputed on every query. At this data
   size that is irrelevant; at production scale the marts would be materialised.
4. **The parity guarantee covers the commercial mart.** Forecasting, decomposition
   and signal assembly exist only in Python, and the SQL layer does not attempt
   to reproduce them.
