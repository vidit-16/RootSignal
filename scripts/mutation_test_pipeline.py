"""Mutation testing for the pipeline with bugs it could realistically ship.

scripts/mutation_test.py swaps operators and nudges constants, which suits the
KPI layer. Most of the pipeline's mistakes would not look like that: a
rounding rule swapped for Spark's own, a NULL ordering dropped, a CSV option
lost, a spending cap that only logs. Each mutant below is one such change,
applied alone; the suite that should notice is run, and the file restored.

Run it where Spark and PostgreSQL are both available:

    docker compose up -d warehouse
    docker compose run --rm pipeline python scripts/mutation_test_pipeline.py

A surviving mutant is either a gap in the tests or a change no test here can
see. The second kind is listed with its reason, not hidden.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rootsignal.console import use_utf8_output

ROOT = Path(__file__).resolve().parents[1]
JOB = "jobs/conform_online_retail.py"
SPARK_TESTS = ("tests/test_spark_conform.py",)
WAREHOUSE_TESTS = ("tests/test_warehouse.py",)


@dataclass(frozen=True)
class Mutant:
    name: str
    path: str
    old: str
    new: str
    tests: tuple[str, ...]


MUTANTS = [
    # The Spark job
    Mutant("Spark's own round() instead of pandas rounding", JOB,
           "return F.rint(column * F.lit(scale)) / F.lit(scale)", "return F.round(column, places)", SPARK_TESTS),
    Mutant("A C-prefixed invoice is no longer a return", JOB,
           '(F.col("units") < 0) | F.upper(F.col("order_id")).startswith("C")', '(F.col("units") < 0)', SPARK_TESTS),
    Mutant("A negative quantity is no longer a return", JOB,
           '(F.col("units") < 0) | F.upper(F.col("order_id")).startswith("C")',
           'F.upper(F.col("order_id")).startswith("C")', SPARK_TESTS),
    Mutant("Customer IDs keep the trailing .0", JOB,
           'F.regexp_replace("customer_id_raw", r"\\.0$", "")', 'F.col("customer_id_raw")', SPARK_TESTS),
    Mutant("List price is the mean, not the median", JOB,
           'F.expr("percentile(unit_price, 0.5)")', 'F.avg("unit_price")', SPARK_TESTS),
    Mutant("Zero prices count toward the list price", JOB,
           'sales.where(F.col("unit_price") > 0)', "sales", SPARK_TESTS),
    Mutant("Prices are recomputed even with nothing to combine", JOB,
           "if repeated == 0:", "if repeated < 0:", SPARK_TESTS),
    Mutant("A blank description is not UNKNOWN", JOB,
           'F.when(first_word == "", "UNKNOWN").otherwise(first_word)', "first_word", SPARK_TESTS),
    Mutant("CSV read line by line", JOB, '.option("multiLine", True)', '.option("multiLine", False)', SPARK_TESTS),
    Mutant("CSV quotes escaped the Spark default way", JOB, """.option("escape", '"')""", "", SPARK_TESTS),
    Mutant("Whitespace matched as ASCII only", JOB, 'r"(?U)^\\s*(\\S+)"', 'r"^\\s*(\\S+)"', SPARK_TESTS),
    Mutant("First value taken in any order", JOB,
           'return F.min_by(F.col(column), F.when(F.col(column).isNotNull(), F.col("_row")))',
           "return F.first(F.col(column), ignorenulls=True)", SPARK_TESTS),
    # The SQL translation
    Mutant("Week starts at the month", "src/rootsignal/sql/dialect.py",
           "CAST(DATE_TRUNC('week', \\1) AS DATE)", "CAST(DATE_TRUNC('month', \\1) AS DATE)", WAREHOUSE_TESTS),
    Mutant("REAL left as PostgreSQL's 4-byte float", "src/rootsignal/sql/dialect.py",
           'out = _REAL.sub("DOUBLE PRECISION", out)', "out = out", WAREHOUSE_TESTS),
    Mutant("Untranslated SQLite passed through silently", "src/rootsignal/sql/dialect.py",
           "        if match:\n            raise ValueError(", "        if False:\n            raise ValueError(",
           WAREHOUSE_TESTS),
    Mutant("CHECK constraints sent to Redshift", "src/rootsignal/sql/dialect.py",
           "    out = _strip_checks(out)\n", "", ("tests/test_aws.py",)),
    Mutant("The warehouse truncates instead of rounding", "src/rootsignal/sql/dialect.py",
           "AS $$ SELECT round(value::numeric, places)::double precision $$;",
           "AS $$ SELECT trunc(value::numeric, places)::double precision $$;",
           WAREHOUSE_TESTS),
    # The queries
    Mutant("Target variance sorted without a tie-breaker", "sql/analytics/target_variance.sql",
           "ORDER BY variance_pct NULLS FIRST, p.region_code, p.category, p.channel;",
           "ORDER BY variance_pct;", WAREHOUSE_TESTS),
    Mutant("Weekly movers sorted without a tie-breaker", "sql/analytics/weekly_movers.sql",
           "ORDER BY c.week_start, c.contribution, c.region_code, c.category;",
           "ORDER BY c.week_start, c.contribution;", WAREHOUSE_TESTS),
    Mutant("Stockout rate averaged over integers", "sql/analytics/supply_watchlist.sql",
           "ROUND(AVG(CAST(i.stockout_flag AS REAL)), 4)", "ROUND(AVG(i.stockout_flag), 4)", WAREHOUSE_TESTS),
    # Loading
    Mutant("The old warehouse is dropped outside the load's transaction", "src/rootsignal/sql/warehouse.py",
           "            cursor.execute(f'CREATE SCHEMA \"{schema}\"')",
           "            conn.commit()\n            cursor.execute(f'CREATE SCHEMA \"{schema}\"')", WAREHOUSE_TESTS),
    Mutant("Clean zone integers written as 64-bit", "src/rootsignal/pipeline/stages.py",
           '"INTEGER": pa.int32()', '"INTEGER": pa.int64()', ("tests/test_pipeline_stages.py",)),
    Mutant("Report tables keep pandas Periods", "src/rootsignal/pipeline/publish.py",
           "            out[column] = series.dt.start_time.dt.date\n", "            pass\n",
           ("tests/test_pipeline_stages.py",)),
    # The lake and AWS
    Mutant("A folder picks up its siblings", "src/rootsignal/pipeline/lake.py",
           'if item["Key"] == base or item["Key"].startswith(base + "/"):', "if True:", ("tests/test_lake.py",)),
    Mutant("Teardown reports a Glue job it did not remove", "src/rootsignal/pipeline/aws.py",
           "        glue.get_job(JobName=GLUE_JOB)\n        glue.delete_job(JobName=GLUE_JOB)",
           "        glue.delete_job(JobName=GLUE_JOB)", ("tests/test_aws.py",)),
    Mutant("Redshift's spending cap only logs", "src/rootsignal/pipeline/aws.py",
           'breachAction="deactivate"', 'breachAction="log"', ("tests/test_aws.py",)),
    Mutant("Redshift may scale past its base size", "src/rootsignal/pipeline/aws.py",
           "maxCapacity=base_capacity,", "maxCapacity=base_capacity * 4,", ("tests/test_aws.py",)),
    Mutant("SET and SELECT sent as separate calls", "src/rootsignal/pipeline/aws.py",
           'statements = [f"SET search_path TO {schema}", sql] if schema else [sql]',
           "statements = [sql]", ("tests/test_aws.py",)),
]

# Mutants no test in this repository can catch, and why. They are still run, so
# the day a test starts catching one, this list is visibly out of date.
EXPECTED_SURVIVORS: dict[str, str] = {}


def run(mutant: Mutant) -> bool:
    """Apply one mutant, run its tests, restore the file. True if killed."""
    path = ROOT / mutant.path
    original = path.read_text(encoding="utf-8")
    count = original.count(mutant.old)
    if count != 1:
        raise SystemExit(f"{mutant.name}: expected the original text once in {mutant.path}, found {count}")
    try:
        path.write_text(original.replace(mutant.old, mutant.new), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", "-o", "addopts=", *mutant.tests],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    finally:
        path.write_text(original, encoding="utf-8")
    # Only a failing test is a kill. pytest also exits non-zero when a file is
    # missing or nothing was collected, and counting that would inflate the score.
    if result.returncode not in (0, 1):
        raise SystemExit(f"{mutant.name}: pytest exited {result.returncode}\n{result.stdout[-2000:]}")
    if result.returncode == 0 and " passed" not in result.stdout:
        raise SystemExit(f"{mutant.name}: its tests all skipped, so the result would mean nothing")
    return result.returncode == 1


def main() -> int:
    use_utf8_output()
    killed, survived = [], []
    for mutant in MUTANTS:
        (killed if run(mutant) else survived).append(mutant)
        print(f"{'killed  ' if mutant in killed else 'SURVIVED'}  {mutant.name}", flush=True)

    print(f"\n{len(killed)}/{len(MUTANTS)} killed")
    unexplained = [mutant for mutant in survived if mutant.name not in EXPECTED_SURVIVORS]
    for mutant in survived:
        reason = EXPECTED_SURVIVORS.get(mutant.name, "no test catches this")
        print(f"  survived: {mutant.name}. {reason}")
    stale = [name for name in EXPECTED_SURVIVORS if name in {mutant.name for mutant in killed}]
    for name in stale:
        print(f"  now killed, remove from EXPECTED_SURVIVORS: {name}")
    return 1 if unexplained or stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
