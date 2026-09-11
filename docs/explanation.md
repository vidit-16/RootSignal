# The explanation layer

## It works without an API key, and that is the design

Everything the system knows about a movement is already in the evidence package
by the time anything is written. Composing that into English needs arithmetic
and grammar, not a model. `write_briefing` does it deterministically, with no
key, no network, and no dependency beyond what the project already installs.

A language model can rephrase the result more fluently. It **cannot add
anything**, because there is nothing to add — it works from the same package and
is checked against it afterwards.

The two paths differ in fluency, not capability:

| | Without a key | With a key |
| --- | --- | --- |
| Facts available | All of them | The same ones |
| Figures | Computed | The same, restated |
| Alternative explanation | Present | Present |
| Caveats | Present | Present |
| Prose | Composed from templates | Rephrased |

```bash
python scripts/explain_signals.py              # no key needed
python scripts/explain_signals.py --rewrite    # uses a model, if configured
```

`--base-url` accepts any OpenAI-compatible endpoint, so the rewrite can run
against a hosted API or a model on your own machine.

## What the briefing says

A signal is written up in six parts: what moved, what the surrounding numbers
did, what that points to, what it is worth, how far the evidence goes, and what
to check next. The alternative explanation and the caveats are part of the
write-up rather than footnotes — a hedge that gets tidied away in the prose was
never a hedge.

The confidence section names **which check failed**, not just the level. "High:
5 of 6 checks passed, and the one it did not pass is that the competing
explanation does not fit" tells a reader far more than "high".

## The guard is the point

An instruction not to invent numbers is a request. A model told to stay within
the figures it was given will usually comply, and occasionally will not.

So the output is verified rather than trusted:

1. **Every figure** in the generated text is matched against the numbers in the
   evidence package, allowing for rounding and for rates written as percentages.
2. **Causal phrasing** is rejected — "caused by", "due to", "the root cause" and
   the rest. The analysis reports what evidence is *consistent with*, and the
   write-up must not quietly upgrade that.

If either check fails, the rewrite is **discarded** and the deterministic
briefing returned instead, with a note saying what was rejected and why. A
reader can tell that a rewrite was attempted and why it was not used.

This is what turns "the model never calculates business truth" from a claim into
a guarantee. Tests cover both directions: an invented figure is caught,
arithmetic the model performed itself is caught, and a faithful rewrite passes.

### Dates nearly broke it

The first version of the guard shredded dates. Under a number pattern,
`2026-02-23` becomes `202`, `-23`, `202`, and every fragment was reported as an
invented figure — so the guard rejected its own faithful output. Periods are
named in almost every sentence the system writes, so dates are now removed
before figures are looked for, and a bare four-digit year is treated as a date
rather than a business figure.

That bug surfaced by running the guard against the deterministic briefing, which
is worth doing precisely because that text is known to be faithful. **A guard
that rejects known-good text is broken regardless of what else it catches.**

## What the model is given

The finished analysis, and nothing else. No tables, no raw data, no schema. The
prompt carries the briefing plus an explicit list of the figures that may be
used, and a test asserts that no fact table name or internal key appears in it.

The system prompt and the guard list the same forbidden phrases, and a test
asserts they agree. Instruction and enforcement drifting apart would leave a gap
where the model was never told about something the guard rejects.

## Where briefings appear

- `scripts/explain_signals.py` in the terminal
- The **What to look into** page of the dashboard, under each signal
- A **Briefing** sheet in the Excel signal report

All three are composed from the same evidence the tables show, so prose and
figures cannot drift apart.

## Limitations

1. **The guard checks figures and phrasing, not meaning.** A rewrite that used
   every number correctly while emphasising the wrong thing would pass.
2. **Rounding tolerance is one percent.** A model writing 8,325 for 8,324.65 is
   accepted; that tolerance would also accept a genuinely different figure that
   happened to fall within it.
3. **Small integers are treated as structural.** "5 of 6 checks" and "three
   regions" are ordinary English, so integers up to twelve are not checked
   against the evidence.
4. **The rewrite is off by default.** It costs money, sends the briefing to a
   third party, and adds nothing but fluency, so it is opt-in rather than
   automatic.
