# Excel reporting

## Excel reports; it does not compute

Every figure in these workbooks is produced by the analytical layers and tested
there. Nothing is recalculated during export. If a number in a workbook
disagreed with the same number in the mart, the workbook would be the thing that
is wrong, and a test asserts they agree.

That boundary is the point. It would be easy to write a spreadsheet builder that
re-derives fill rate from whatever columns are to hand, and it would quietly
produce a second definition of every metric — the failure this project spends
its SQL parity test preventing.

```bash
python scripts/build_excel_reports.py --list
python scripts/build_excel_reports.py --current-period 2026-02-23 --comparison-period 2026-02-09
```

## Every workbook opens with what its figures mean

Spreadsheets get forwarded, split apart and pasted into decks, and they arrive
stripped of whatever context surrounded them. Each workbook therefore opens on a
**Read me** sheet stating what it contains, when it was produced, and what its
figures do and do not mean.

This matters most for the signal report. An impact estimate that travels without
its basis will be read as a measured loss, and the four assumptions behind it all
push the number **high**. They are printed on the cover, not buried in a column
header.

## The reports

| Workbook | Sheets | Answers |
| --- | --- | --- |
| `daily_sales_tracker` | Daily tracker | How did trade run today, against yesterday and against plan? |
| `sales_performance` | Region, category, channel, KAM, versus plan | Where is the business performing, and against what? |
| `fill_rate_report` | Versus target, service detail, week over week | Which segments are meeting the service level, and which are slipping? |
| `primary_secondary_report` | Region, channel, category | How does sell-in split against sell-through? |
| `forecast_report` | Accuracy and backtest per metric, daily series | How accurate is the forecast, and on what evidence? |
| `root_signal_report` | Signals, evidence, confidence criteria, impact | What deserves investigating, and how far does the evidence go? |

### The signal report carries its own reasoning

The conclusion and the reasoning travel together, on four sheets:

- **Signals** — the movement, the likely driver, the impact, the confidence, and
  the recommended investigation.
- **Evidence** — every surrounding observation, before and after, that the
  reading rests on.
- **Confidence criteria** — all six named criteria per signal, each with the
  number behind it and whether it was met.
- **Impact** — the estimate with its components: ordered units, the baseline fill
  rate, the shortfall, and the realised selling price.

A reader who disagrees with a signal can see exactly which step to argue with.
A confidence level with no criteria attached would be unanswerable.

## Formatting decisions

Number formats follow what a column **means**, not what type it happens to be
stored as. A fill rate is a rate whether or not it is a float, and showing it as
currency because both are floats would be misleading. Columns are matched by
name — `_pct` and `_share` render as percentages, `_rate` to three decimals,
`_units` and `_count` as integers, `_sales` and `_target` as currency.

An empty result still produces a sheet carrying its column headers, so a reader
can tell the difference between a report that found nothing and a report that
failed to run.

Sheet names are truncated to Excel's 31-character limit and stripped of the
characters it rejects, rather than failing at save time.

## Limitations

1. **These are exports, not a BI tool.** There are no pivot tables, charts or
   slicers. The workbooks are a readable snapshot of computed results, and a
   reader wanting to explore rather than read should use the dashboard or the
   SQL queries.
2. **No formulas are written.** Cells contain values, not expressions, so a
   reader cannot change an input and watch the numbers move. That is deliberate:
   a formula in a spreadsheet is a third implementation of a metric.
3. **Every workbook is a full rebuild.** Nothing is appended or refreshed in
   place, so any manual edit to a previous file is overwritten.
4. **The signal report covers one metric and grain at a time** — fill rate across
   region and category by default. Other views need another run.
