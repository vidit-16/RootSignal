# Running it in a container

## Why

The project already installs with one `pip` command, so an image is not needed
to make it run. It exists for a narrower reason: **a reader should be able to
re-run the checks that produced the numbers, without first agreeing to install
anything.**

That turns the README's claims from assertions into something a sceptic can
execute in two commands.

```bash
docker build -t rootsignal .
docker run --rm rootsignal pytest
```

## What is in the image

```bash
docker run --rm -p 8501:8501 rootsignal                 # the dashboard
docker run --rm rootsignal pytest                       # all 278 tests
docker run --rm rootsignal python scripts/detect_signals.py
```

Or with compose:

```bash
docker compose up                        # the dashboard on :8501
docker compose run --rm checks           # the test suite
```

The sample dataset is **generated during the build** rather than copied in. It
is deterministic from seed 42, so the image carries exactly the data the
committed numbers were measured against, and a build that breaks the generator
fails rather than shipping stale CSVs.

The container runs as an unprivileged user. Nothing in the project needs root.

## What the image found

Building it surfaced a problem that local development could not, which is most
of the argument for having it.

**The image resolves different dependencies than a developer machine.**
`pyproject.toml` allows `numpy>=1.26,<3`. Local development had settled on
numpy 1.26.4 and pandas 2.2.2; a fresh install in the image resolves
**numpy 2.5.3 and pandas 2.3.3**.

All 278 tests pass on both. But under numpy 2.x the forecasting layer emitted
380 deprecation warnings from a single line:

```python
start = history.index.max() + pd.Timedelta(days=1)
```

The warning says the behaviour "will raise an error in the future". The code was
not wrong — plain `Timestamp + pd.Timedelta` warns on these versions, with no
RootSignal code involved — but relying on it meant the forecasting layer had a
dated fuse in it. It is now generated in one call instead:

```python
return pd.date_range(history.index.max(), periods=horizon + 1, freq="D")[1:]
```

Same days, no arithmetic, no warning.

**A second deprecation, and this one is not ours to defuse.** Three warnings
remain under numpy 2.5, all from the test that checks a step change is nearly
invisible in the period straddling it:

```python
straddling = starts - pd.Timedelta(days=starts.weekday())
```

The message is the same shape as the one above -- the 'generic' unit for NumPy
timedelta is deprecated and "will raise an error in the future" -- but the
frame it is raised from is `pandas/_libs/tslibs/timedeltas.pyx`, inside pandas
itself. There is no spelling of `pd.Timedelta(days=n)` that avoids it.

So it is recorded rather than fixed or silenced. When numpy promotes the
warning to an error, pandas 2.3.3 is what stops working, and the repair has to
come from pandas. The container is where it will show up first, because the
container is what resolves the top of the range -- which is the same reason it
caught the forecasting fuse while local development could not.

**The results are identical across both.** The worked example returns
`BLR | Fruits, -0.2889, fulfilment_constraint, 8,970.60, high` on numpy 1.26
under Windows and on numpy 2.5 under Linux, and `seasonal_mean_7` improves on
the naive baseline by 28.88% in both. That is a stronger reproducibility claim
than a single machine can make, and it is the reason the dependency floors are
ranges rather than pins.

## What is deliberately not here

**No lockfile.** Pinning every transitive dependency would have hidden the numpy
finding above, which was worth more than the reproducibility it would have
bought. The floors in `pyproject.toml` state what the project actually requires;
the image proves the range is honest by resolving the top of it.

**No published image.** There is nothing to deploy. The Dockerfile is the
artifact, and it builds in about a minute.

**No multi-stage build.** The image is roughly 1 GB, and nearly all of it is
pandas, pyarrow, streamlit and plotly — the dependencies themselves, not build
tooling a second stage could discard. A multi-stage build would add a layer of
indirection to save a few percent.

## Notes

- `data/raw/external` and `data/raw/generated` are excluded from the build
  context. The external dataset is 44 MB and downloads on demand; the generated
  one is rebuilt inside the image.
- Compiled bytecode is excluded too, by `**/`-anchored patterns. A bare
  `__pycache__` matches only the context root, so nested ones were copied in and
  the image shipped modules compiled on the developer's machine. `COPY`
  preserves mtimes, so CPython would load that bytecode in preference to the
  source beside it -- the image testing the host's build rather than its own.
- `docker compose up` mounts `./reports`, so workbooks written by
  `build_excel_reports.py` land in the working tree.
- The explanation layer runs fully without an API key and records why it fell
  back to the deterministic briefing. Set `OPENAI_API_KEY` only to exercise the
  optional rewrite, which is checked against the computed numbers either way.
