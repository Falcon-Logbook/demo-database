#!/usr/bin/env python3
"""Check falcon_demo.sqlite against the claims this repo makes about it.

Fails when the file drifts from the repository:

- PRAGMA user_version is not the schema version the file claims, or schema.sql
  disagrees with it
- version_ledger is not one row per version from 1 to that version
- the airports table does not match the airports CSV it is given, or any
  current row carries a retired_at marker
- the counts README.md quotes (flights, months, hours, routes, airframe,
  simulators, crew, expiries, currencies) no longer match the file

Standard library only, and it reads nothing outside this repo, so it runs
without the app checked out. Pass --airports-csv to check airport coverage
against the app's current list; without it the row count is compared to the
count recorded below.

Usage:
    python3 tools/check_demo_db.py
    python3 tools/check_demo_db.py \
        --airports-csv ../app/assets/data/airports.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# What the repo promises. Keep these in step with README.md and schema.sql.
EXPECTED_SCHEMA_VERSION = 24
EXPECTED_AIRPORTS = 28343  # app/assets/data/airports.csv at app 0.0.15
EXPECTED_FLIGHTS = 486
EXPECTED_MONTHS = 60
EXPECTED_FIRST_MONTH = "2021-11"
EXPECTED_LAST_MONTH = "2026-10"
EXPECTED_TOTAL_MINUTES = 113116  # about 1,885 hours
EXPECTED_NIGHT_MINUTES = 41104  # about 685 hours
EXPECTED_ROUTES = 134
EXPECTED_AIRCRAFT = 43
EXPECTED_SIMULATORS = 7
EXPECTED_CREW = 30
EXPECTED_EXPIRIES = 11
EXPECTED_CURRENCIES = 4


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks += 1
        if ok:
            print(f"  ok    {name}{f' ({detail})' if detail else ''}")
            return
        self.failures.append(name)
        print(f"  FAIL  {name}{f' ({detail})' if detail else ''}")

    def equal(self, name: str, actual: object, expected: object) -> None:
        self.check(
            name,
            actual == expected,
            f"expected {expected}, found {actual}",
        )


def count_csv_airports(path: Path) -> int:
    """Count the ICAOs in an airports CSV, using the generator's rule."""
    count = 0
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (row.get("icao") or "").strip():
                count += 1
    return count


def declared_schema_version(schema_path: Path) -> int | None:
    if not schema_path.exists():
        return None
    match = re.search(
        r"PRAGMA\s+user_version\s*=\s*(\d+)\s*;",
        schema_path.read_text(encoding="utf-8"),
        re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


def column_names(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    """Column name -> declared type, lowercased, for one table."""
    return {
        str(row[1]).lower(): str(row[2] or "").upper()
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def run(conn: sqlite3.Connection, check: Checker, airports_csv: Path | None) -> None:
    query = conn.execute

    print("schema")
    user_version = query("PRAGMA user_version").fetchone()[0]
    check.equal("PRAGMA user_version", user_version, EXPECTED_SCHEMA_VERSION)

    schema_declared = declared_schema_version(REPO_ROOT / "schema.sql")
    if schema_declared is None:
        check.check("schema.sql declares a version", False, "no PRAGMA user_version")
    else:
        check.equal(
            "schema.sql PRAGMA user_version",
            schema_declared,
            EXPECTED_SCHEMA_VERSION,
        )
        check.equal("schema.sql matches the file", schema_declared, user_version)

    integrity = [row[0] for row in query("PRAGMA integrity_check")]
    check.check("PRAGMA integrity_check", integrity == ["ok"], ", ".join(integrity))
    foreign_keys = query("PRAGMA foreign_key_check").fetchall()
    check.check(
        "PRAGMA foreign_key_check",
        not foreign_keys,
        f"{len(foreign_keys)} violations",
    )

    print("version ledger")
    versions = [
        row[0] for row in query("SELECT version FROM version_ledger ORDER BY version")
    ]
    check.equal(
        "ledger rows",
        len(versions),
        EXPECTED_SCHEMA_VERSION,
    )
    check.check(
        "ledger is gapless from 1",
        versions == list(range(1, EXPECTED_SCHEMA_VERSION + 1)),
        f"found {versions}",
    )
    empty_text = query(
        "SELECT COUNT(*) FROM version_ledger "
        "WHERE TRIM(first_app_release) = '' OR TRIM(schema_change) = ''"
    ).fetchone()[0]
    check.equal("ledger rows have release and change text", empty_text, 0)
    ledger_max = query("SELECT MAX(version) FROM version_ledger").fetchone()[0]
    check.equal("ledger ends at user_version", ledger_max, user_version)

    print("schema shape")
    check.equal(
        "app_settings.text_scale default",
        query("SELECT text_scale FROM app_settings").fetchone()[0],
        "system",
    )
    airports_columns = column_names(conn, "airports")
    check.check(
        "airports.retired_at exists",
        "retired_at" in airports_columns,
    )
    for required in ("lat", "lon"):
        nullability = None
        for row in query("PRAGMA table_info(airports)"):
            if str(row[1]).lower() == required:
                nullability = row[3]
        check.equal(f"airports.{required} is nullable", nullability, 0)
    check.equal(
        "flights.route_id is TEXT",
        column_names(conn, "flights").get("route_id"),
        "TEXT",
    )
    check.equal(
        "saved_routes.id is TEXT",
        column_names(conn, "saved_routes").get("id"),
        "TEXT",
    )
    for column in (
        "scheduled_departure_utc",
        "scheduled_arrival_utc",
        "scheduled_block_minutes",
        "schedule_source",
    ):
        check.check(
            f"flights.{column} exists",
            column in column_names(conn, "flights"),
        )

    print("airports")
    airports = query("SELECT COUNT(*) FROM airports").fetchone()[0]
    if airports_csv is None:
        check.equal("airport rows", airports, EXPECTED_AIRPORTS)
    else:
        csv_airports = count_csv_airports(airports_csv)
        check.equal("airport rows match the CSV", airports, csv_airports)
        stored = query("SELECT airports_csv_hash FROM general_settings").fetchone()
        stored = stored[0] if stored else None
        digest = None
        if isinstance(stored, str) and ":" in stored:
            digest = stored.split(":", 1)[1]
        expected_digest = hashlib.md5(airports_csv.read_bytes()).hexdigest()
        check.equal(
            "seed fingerprint matches the CSV",
            digest,
            expected_digest,
        )
    retired = query(
        "SELECT COUNT(*) FROM airports WHERE retired_at IS NOT NULL"
    ).fetchone()[0]
    check.equal("no current airport is retired", retired, 0)
    check.equal(
        "airports have coordinates",
        query(
            "SELECT COUNT(*) FROM airports WHERE lat IS NULL OR lon IS NULL"
        ).fetchone()[0],
        0,
    )

    print("flights")
    check.equal(
        "flights",
        query("SELECT COUNT(*) FROM flights").fetchone()[0],
        EXPECTED_FLIGHTS,
    )
    months = [
        row[0]
        for row in query(
            "SELECT DISTINCT strftime('%Y-%m', date, 'unixepoch') AS month "
            "FROM flights ORDER BY month"
        )
    ]
    check.equal("distinct months", len(months), EXPECTED_MONTHS)
    check.equal("first month", months[0] if months else None, EXPECTED_FIRST_MONTH)
    check.equal("last month", months[-1] if months else None, EXPECTED_LAST_MONTH)

    total, night = query(
        "SELECT COALESCE(SUM(total_time), 0), COALESCE(SUM(night_time), 0) FROM flights"
    ).fetchone()
    check.equal("total minutes", total, EXPECTED_TOTAL_MINUTES)
    check.equal("night minutes", night, EXPECTED_NIGHT_MINUTES)

    print("routes")
    check.equal(
        "saved routes",
        query("SELECT COUNT(*) FROM saved_routes").fetchone()[0],
        EXPECTED_ROUTES,
    )
    links = query("SELECT COUNT(*) FROM flights WHERE route_id IS NOT NULL").fetchone()[
        0
    ]
    orphan_links = query(
        "SELECT COUNT(*) FROM flights AS f "
        "LEFT JOIN saved_routes AS r ON r.id = f.route_id "
        "WHERE f.route_id IS NOT NULL AND r.id IS NULL"
    ).fetchone()[0]
    check.check("flights keep their route links", links > 0, f"{links} linked")
    check.equal("every route link resolves", orphan_links, 0)

    print("fleet, crew and extras")
    aircraft, simulators = query(
        "SELECT COUNT(*), COALESCE(SUM(simulator), 0) FROM aircraft"
    ).fetchone()
    check.equal("aircraft", aircraft, EXPECTED_AIRCRAFT)
    check.equal("simulators", simulators, EXPECTED_SIMULATORS)
    check.equal(
        "crew",
        query("SELECT COUNT(*) FROM crew_members").fetchone()[0],
        EXPECTED_CREW,
    )
    check.equal(
        "expiries",
        query("SELECT COUNT(*) FROM expiries").fetchone()[0],
        EXPECTED_EXPIRIES,
    )
    check.equal(
        "currencies",
        query("SELECT COUNT(*) FROM user_currency").fetchone()[0],
        EXPECTED_CURRENCIES,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=REPO_ROOT / "falcon_demo.sqlite",
        help="Database to check (default: falcon_demo.sqlite)",
    )
    parser.add_argument(
        "--airports-csv",
        type=Path,
        default=None,
        help="App airports.csv to compare the airport table against",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"database not found: {args.db}", file=sys.stderr)
        return 2
    if args.airports_csv is not None and not args.airports_csv.exists():
        print(f"airports CSV not found: {args.airports_csv}", file=sys.stderr)
        return 2

    print(f"Checking {args.db}")
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    check = Checker()
    try:
        run(conn, check, args.airports_csv)
    except sqlite3.DatabaseError as error:
        print(f"  FAIL  {error}")
        return 1
    finally:
        conn.close()

    failed = len(check.failures)
    print()
    if failed:
        print(f"{failed} of {check.checks} checks failed:")
        for name in check.failures:
            print(f"  - {name}")
        return 1
    print(f"All {check.checks} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
