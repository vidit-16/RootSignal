# The dashboard

## Five pages over a testable core

```bash
pip install -e ".[dashboard]"
streamlit run app/streamlit_app.py
```

| Page | Question |
| --- | --- |
| Home | How did the latest complete period run, and what changed on the way in? |
| Sales | How is trade running, cut by region, category, channel and manager? |
| Supply | Where is fulfilment slipping, and which SKUs carry the unserved demand? |
| Forecasting | How accurate is the expected baseline, measured out-of-sample? |
| Root Signals | What deserves investigating, and how far does the evidence go? |

## The pages hold layout; the analysis is elsewhere

Everything a page displays is prepared by `rootsignal.dashboard.views`, which
**imports no Streamlit**. That is a deliberate boundary, and a test asserts it.

Without it, none of the dashboard's figures could be checked without starting a
web server, and analysis would quietly accumulate inside page scripts where
nobody tests it. With it, the whole data layer is covered by ordinary tests and
the pages contain layout.

Nothing in the view layer computes a metric either. Each view composes the
period summaries, variance analysis, the forecast backtest and the signal
engine. A dashboard that re-derived fill rate from whatever columns were to hand
would become a second definition of it — the failure the SQL parity test exists
to prevent.

## Charts

Colour is assigned by the job it does, from a validated categorical palette used
**in a fixed order and never cycled**. Colour follows the entity, so filtering a
region out does not repaint the ones that remain. A ninth series folds into
"Other" rather than inventing a hue outside the validated set.

**There are no dual-axis charts.** Two measures on two scales invite a reader to
see a relationship that the scaling invented. Two measures means two charts.

Status colours — good, warning, serious, critical — are reserved for state and
never reused as "series 4". They always travel with a label, because a status
shown as colour alone is unreadable to a substantial share of viewers and
invisible in print. The gap-to-target chart reinforces sign with position as
well as hue: a bar's side of zero says as much as its colour.

## Two bugs that only rendering revealed

Both passed every unit test and were obvious the moment the page was opened.

**A zigzag instead of a trend.** The regional fill-rate chart was fed the
region-by-category frame, so four category rows per region per week were drawn
as a single line. Region-level series are now built at region grain, where the
fill rate is rebuilt from summed units rather than averaged up from a finer one.

**Charts side by side disagreeing about which weeks exist.** Inventory summaries
do not drop partial periods, so the stock chart carried a final short week that
the commercial chart beside it excluded. Inventory is now restricted to the
periods the commercial view covers.

Neither was a failure of the analytics. Both were a page asking a correct
function for the wrong thing.

## Caching

Expensive work — loading, cleaning, backtesting, signal assembly — is cached per
argument. Streamlit keys that cache on the decorated wrapper, so a change to the
underlying analytics or to the files on disk does **not** invalidate it on its
own. The sidebar carries a **Reload data** control for exactly that case.

## Limitations

1. **Read-only.** Nothing can be edited, annotated or saved from the dashboard.
2. **Single dataset at a time.** The sidebar points at one directory; there is no
   comparison between datasets or environments.
3. **Forecast backtests run on demand** and take a few seconds on first load for
   each metric. They are cached afterwards.
4. **Segment-level forecasts are not shown**, because they have not been
   evaluated. The forecasting page is company-wide and daily.
5. **Signals are shown for one metric and grain at a time** — fill rate across
   region and category by default. Other views need another selection.
