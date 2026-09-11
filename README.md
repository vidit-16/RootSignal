# RootSignal

**A metric moved. RootSignal finds where, prices what it cost, says how confident it is — and states what it refuses to conclude.**

Root-cause analytics for sales and supply operations. Given a movement in a
business metric, it identifies the segment responsible, separates a genuine
performance change from a shift in demand mix, estimates the money involved,
reports confidence against named criteria, and recommends the next thing to
check.

It is built to be *checkable*. Every headline number below is reproduced by a
committed script or test, and the system declines to answer questions its data
cannot support.

---

## What it actually produces

Given nothing but a fill-rate movement across every region and category — no hint
of Bengaluru, of fruit, or of supply:

```bash
python scripts/detect_signals.py
```

> **BLR | Fruits** — fill rate moved **−0.2889 (−29.1%)**
>
> Consistent with a **fulfilment or supply constraint**: fulfilled units fell
> while ordered units did not. Fulfilled units fell 16.2%; **ordered units rose
> 18.2%**; available stock fell 47.7%; stockout rate rose.
>
> Estimated impact **8,970.60**, from units not fulfilled relative to the
> segment's prior fill rate, valued at its realised average selling price.
>
> Confidence: **high — 5 of 6 criteria met.**
> Met: movement stands out (19.9× the segment's typical swing) · segment carries
> 26.8% of the movement · 4 other metrics agree · the alternative is not
> supported · movement is directional. Not met: **it has moved this way for one
> period, and the threshold is two**.
>
> *Alternative considered:* demand weakened and fulfilment simply followed it.
> Orders rose 18% while shipments fell 16%, so that reading is not available.
>
> **Recommended investigation:** review inventory availability and replenishment
> for BLR | Fruits, starting with the SKUs carrying the largest unfulfilled volume.

The interesting part is the criterion it will not claim. The alternative is
dismissed on evidence, but the engine refuses to call a one-week step a
persistent trend — because on the period a step change happens, it isn't one
yet. Five of six, on the one criterion only time can settle.

## What makes it different

**It shows its working, not a score.** Confidence is six named criteria you can
read and disagree with, not a tuned number. Impact ships with its assumptions
attached — no substitution, no recovery, associated revenue rather than measured
loss.

**It separates performance from mix.** A fill rate can fall while every segment
improves, purely because demand moved toward segments that fill less well. Rate
effect, mix effect and interaction are decomposed so they reconstruct the
observed movement *exactly* — and the code refuses to return effects that do not
add back.

**It refuses rather than guesses.** An adapter declares which analyses its data
can support and which it cannot, with the missing fact named. The LLM layer
never calculates: it rewrites numbers the deterministic layer already computed,
and a guard verifies every figure and rejects causal phrasing.

**It runs on data it was not built for.** The same pipeline, unchanged, runs on a
million real invoice lines from a UK retailer — which broke three things the
generated data never could.

## Measured results

Reproduced by the committed test suite and evaluation scripts.

| | Result |
| --- | --- |
| **Scenario detection** | 4 situations planted (supply constraint, demand decline, mix shift, and a control where nothing happens). **3 of 3 correctly classified** at a medium confidence floor; **2 of 3 with zero false alarms and silence on the control** at a high floor |
| **Real public data** | UCI Online Retail II, **1,044,420 invoice lines over 739 days**. 3 defects found in the published data, 2,590 unsellable lines quarantined, then **zero validation errors** |
| **Forecast accuracy** | `seasonal_mean_7` beats naive by **+28.9%** WAPE on daily net sales and **+19.1%** on units; `ses` leads orders at **+22.2%**. On the real dataset, **+38.8%** across 49 folds — the same model wins on both |
| **SQL / Python parity** | The commercial mart is implemented twice and a test asserts the two agree across **3,663 rows and 11 metric columns**. The SQL watchlist and the Python engine — sharing no code — independently flag the same two disrupted segments |
| **Data quality** | **8 errors** detected across 10 raw tables; cleaning imputes 3 fields, drops 2 exact duplicates, quarantines 1 impossible line; **zero errors** after |
| **Plan vs actual** | Attainment spans **87.4% to 125.6%** across 64 region/category/channel segments, evenly split 32 above plan and 32 below. KAM quota attainment runs **89.8% to 106.0%** across four managers, three ahead and one behind |
| **Reproducibility** | Identical results on **numpy 1.26 under Windows and numpy 2.5 under Linux** — same signal, same impact to the cent, same forecast improvement. The container resolves the top of the declared dependency range rather than a lockfile, which is how the numpy 2.x break was found |
| **Tests** | **278 automated tests**, passing on both dependency sets. One asserts that no output ever claims causation |

## Quick start

Either install it:

```bash
pip install -e ".[dev,dashboard]"
python scripts/generate_sample_data.py --output-dir data/raw/generated
```

Or don't, and check the claims instead:

```bash
docker build -t rootsignal . && docker run --rm rootsignal pytest
```

Then, in rough order of what is worth seeing first:

```bash
python scripts/detect_signals.py
```

```bash
python scripts/evaluate_signals.py
```

```bash
streamlit run app/streamlit_app.py
```

```bash
python scripts/run_external_dataset.py
```

The first prints the worked example above; the second measures whether the
engine tells the four scenarios apart; the third opens the dashboard; the fourth
runs the same pipeline against real public data.

<details>
<summary>The rest</summary>

```bash
python scripts/explain_signals.py        # signals written up in plain English
python scripts/evaluate_forecasts.py     # forecast accuracy, five models
python scripts/build_excel_reports.py    # six operational workbooks
python scripts/run_sql_query.py          # the seven analytical SQL queries
pytest                                   # the full suite
```

</details>

## Architecture

```text
CSV / Excel / DB  ·  or a declared adapter for an outside dataset
       |
       v
Ingestion -> Validation -> Cleaning (quarantine-first, audited) -> Consolidation
       |
       v
Business data model  —  grains and composite keys enforced in SQL and Python
       |
       +--> KPI engine
       +--> Trend / variance analysis
       +--> Forecasting (rolling-origin backtested)
       +--> Driver decomposition (exact; rate and mix separated)
       +--> Impact estimation (assumptions attached)
       |
       v
Evidence package  —  evidence, pattern, impact, confidence criteria, alternative
       |
       +--> Dashboard  ·  Excel  ·  SQL
       +--> Optional LLM rewrite, numerically verified
       |
       v
Recommended investigation
```

**Stack:** Python, pandas, NumPy, SQL (SQLite/PostgreSQL), Streamlit, Plotly,
pytest, GitHub Actions, and an optional OpenAI-compatible LLM that the system
works fully without. Power BI is deliberately out of scope.

## What's built

| Layer | What it does | Docs |
| --- | --- | --- |
| Data model + SQL schema | Dimensions and facts with grains and composite keys enforced in both SQL and Python | [data_model.md](docs/data_model.md) |
| Sample-data generator | Deterministic 60-day dataset (seed 42) with multi-SKU baskets, controlled defects, and four labelled scenarios | — |
| Ingestion | CSV, Excel and database loading by table name | — |
| Validation | Columns, keys, ranges, referential integrity, cross-table reconciliation | — |
| Cleaning | Quarantine-first repair with a full audit trail | [cleaning.md](docs/cleaning.md) |
| Consolidation | Grain-safe enrichment and the commercial mart | [consolidation.md](docs/consolidation.md) |
| KPI engine | Deterministic sales, order, fill-rate, mix, growth and variance metrics | [kpis.md](docs/kpis.md) |
| Forecasting | Five deterministic daily models with rolling-origin backtesting | [forecasting.md](docs/forecasting.md) |
| Trend and variance | Day/week/month movement against prior period, target and forecast under one schema | [analysis.md](docs/analysis.md) |
| Driver decomposition | Exact attribution to segments, with rate and mix separated for ratios | [decomposition.md](docs/decomposition.md) |
| Impact estimation | Fulfilment shortfall valued at realised prices, every assumption stated | [signals.md](docs/signals.md) |
| RootSignal engine | Evidence, pattern, impact, confidence and recommended investigation, ranked | [signals.md](docs/signals.md) |
| Scenario evaluation | Measures whether the engine tells four planted situations apart | [signals.md](docs/signals.md) |
| SQL layer | Staging views, commercial mart and seven business queries, all executed by tests | [sql.md](docs/sql.md) |
| Excel reporting | Six operational workbooks, each opening with what its figures mean | [reporting.md](docs/reporting.md) |
| Dashboard | Five Streamlit pages over a Streamlit-free, tested data layer | [dashboard.md](docs/dashboard.md) |
| Presentation | One map from schema keys to language a business reader already has | [dashboard.md](docs/dashboard.md#the-words-on-the-screen) |
| Explanation | Written briefings with no API key, plus an optional verified LLM rewrite | [explanation.md](docs/explanation.md) |
| External data | Adapters that declare what a dataset can and cannot answer | [external_data.md](docs/external_data.md) |
| Pipeline contract | End-to-end test from generation through mart reconciliation | [pipeline_contract.md](docs/pipeline_contract.md) |
| Container | Dashboard, scripts and the full suite runnable without installing anything | [docker.md](docs/docker.md) |

## What it refuses to do

Enforced in code and covered by tests, not stated as intentions.

- **Claim causation.** Patterns are described as *consistent with* a cause, and a
  test rejects causal phrasing in any output.
- **Return effects that do not reconcile.** A rate decomposition that fails to
  reconstruct the observed movement raises, naming the offending segments — a
  guard a real dataset earned, not a hypothetical one.
- **Answer without the facts.** Fulfilment analysis on a dataset with no order
  book is refused, with the missing fact named, rather than approximated from
  returns.
- **Let a language model do arithmetic.** Every figure in an LLM rewrite is
  checked against the computed value, and the briefing falls back to the
  deterministic text — recording why — if it fails.
- **Average a rate.** Ratios are rebuilt from summed components at every level,
  never averaged up from a finer grain.

[external_data.md](docs/external_data.md) records what running on real data
broke, including a decomposition that silently reconstructed a movement that
never happened, and why a widely used public supply-chain dataset was evaluated
and rejected as synthetic.

## Not yet built

A second external adapter is scoped and deliberately deferred — see
[external_data.md](docs/external_data.md#three-candidates-checked-against-the-model).

<details>
<summary>One-paragraph summary</summary>

RootSignal is a root-cause analytics system for sales and supply operations.
Given a movement in a business metric, it attributes the change to segments
exactly, separates genuine performance change from shifts in demand mix,
estimates the revenue involved with assumptions stated, and reports confidence
against six named criteria rather than a tuned score. It is validated against
four planted scenarios — classifying three of three correctly at a medium
confidence floor, with zero false alarms at a high one — and runs unchanged on
a million real invoice lines from a public dataset, where it beats a naive
forecast baseline by 38.8% across 49 backtest folds. Python, pandas, SQL,
Streamlit; 278 tests, including one asserting that no output ever claims
causation.

</details>
