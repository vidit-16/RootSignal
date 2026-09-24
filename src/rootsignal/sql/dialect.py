"""Run the one set of SQL files on PostgreSQL as well as SQLite.

The staging views, the mart and the analytical queries are written once. Where
the two engines disagree, this module rewrites the SQLite form into the
PostgreSQL one, and nothing else: every rule below exists because a specific
construct in sql/ needs it, and a construct with no rule is refused rather than
passed through to fail somewhere less obvious.

Rounding is the one difference not handled by rewriting. PostgreSQL has no
ROUND for double precision with a number of places, so the warehouse defines
one (see ROUND_FUNCTION) rather than every ROUND call in the repository being
wrapped in a cast.
"""

from __future__ import annotations

import re

# Week starting Monday. SQLite moves forward to the next Sunday (or stays on a
# Sunday) and steps back six days; DATE_TRUNC('week') is the ISO Monday, which
# is the same day.
_WEEK_START = re.compile(
    r"DATE\(\s*([\w.]+)\s*,\s*'weekday 0'\s*,\s*'-6 days'\s*\)", re.IGNORECASE
)
_YEAR_MONTH = re.compile(r"STRFTIME\(\s*'%Y-%m'\s*,\s*([\w.]+)\s*\)", re.IGNORECASE)
_PRAGMA = re.compile(r"^\s*PRAGMA\b[^;]*;\s*$", re.IGNORECASE | re.MULTILINE)
# SQLite's REAL is an 8-byte float. PostgreSQL's REAL is 4 bytes, which would
# lose cents on large revenue figures, so it becomes DOUBLE PRECISION.
_REAL = re.compile(r"\bREAL\b")

# Anything still SQLite-specific after translation.
_UNTRANSLATED = (
    re.compile(r"\bSTRFTIME\s*\(", re.IGNORECASE),
    re.compile(r"'weekday\s+\d'", re.IGNORECASE),
    re.compile(r"\bPRAGMA\b", re.IGNORECASE),
    re.compile(r"\bJULIANDAY\s*\(", re.IGNORECASE),
    re.compile(r"\bIFNULL\s*\(", re.IGNORECASE),
)

ROUND_FUNCTION = """
CREATE OR REPLACE FUNCTION round(value DOUBLE PRECISION, places INTEGER)
RETURNS DOUBLE PRECISION
LANGUAGE SQL IMMUTABLE STRICT
AS $$ SELECT round(value::numeric, places)::double precision $$;
"""


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


_CREATE_TABLE = re.compile(r"CREATE TABLE\s+(\w+)\s*\((.*?)\n\);", re.IGNORECASE | re.DOTALL)
_COLUMN = re.compile(r"^\s*(\w+)\s+(TEXT|INTEGER|REAL|DATE)\b", re.IGNORECASE)


def schema_columns(schema_sql: str) -> dict[str, list[tuple[str, str]]]:
    """Each table's columns and declared types, in order, from schema.sql.

    Used to write Parquet whose types match the tables exactly, which Redshift's
    COPY requires: it will not narrow a 64-bit integer into an INTEGER column.
    """
    tables = {}
    for name, body in _CREATE_TABLE.findall(_strip_comments(schema_sql)):
        columns = []
        for line in body.splitlines():
            match = _COLUMN.match(line)
            if match:
                columns.append((match.group(1), match.group(2).upper()))
        tables[name] = columns
    return tables


def _strip_checks(sql: str) -> str:
    """Remove CHECK (...) clauses, following parentheses to their close."""
    out, index = [], 0
    pattern = re.compile(r"\s+CHECK\s*\(", re.IGNORECASE)
    while True:
        match = pattern.search(sql, index)
        if not match:
            out.append(sql[index:])
            return "".join(out)
        out.append(sql[index:match.start()])
        depth, position = 1, match.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(sql[position], 0)
            position += 1
        index = position


def to_redshift(sql: str) -> str:
    """Rewrite SQL from this repository for Amazon Redshift.

    The PostgreSQL translation applies, plus what Redshift does not support:
    CHECK constraints and indexes are removed (validation enforces the checks
    before anything is loaded, and Redshift sorts and distributes instead of
    indexing), and TEXT becomes VARCHAR(1024), because Redshift's TEXT is
    VARCHAR(256) and would truncate a long product description.
    """
    out = to_postgres(sql)
    out = _strip_checks(out)
    out = re.sub(r"^\s*CREATE INDEX\b[^;]*;\s*$", "", out, flags=re.IGNORECASE | re.MULTILINE)
    out = re.sub(r"\bTEXT\b", "VARCHAR(1024)", out)
    return out


def to_postgres(sql: str) -> str:
    """Rewrite SQLite SQL from this repository into PostgreSQL.

    Raises ValueError if a SQLite-only construct survives, so a new query using
    one fails at translation rather than as a syntax error from the server.
    """
    out = _PRAGMA.sub("", sql)
    out = _WEEK_START.sub(r"CAST(DATE_TRUNC('week', \1) AS DATE)", out)
    out = _YEAR_MONTH.sub(r"TO_CHAR(\1, 'YYYY-MM')", out)
    out = _REAL.sub("DOUBLE PRECISION", out)

    code = _strip_comments(out)
    for pattern in _UNTRANSLATED:
        match = pattern.search(code)
        if match:
            raise ValueError(
                f"No PostgreSQL translation for {match.group(0)!r}. "
                "Add a rule to rootsignal.sql.dialect or write it portably."
            )
    return out
