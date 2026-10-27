#!/usr/bin/env python3
"""Generate the Falcon demo logbook database.

Builds a complete Falcon (schema v19) SQLite file from the schema in
schema.sql and a deterministic, hand-curated sample of flights.

The logbook is demo data: one fictional pilot, five years, nine aircraft
types, real airports, airlines, routes and registrations. Crew names are made
up, so no crew member is a real person.

Usage:
    python3 tools/generate_demo_db.py \
        --airports-csv ../app/assets/data/airports.csv \
        --out falcon_demo.sqlite
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import random
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
FIRST_DAY = datetime(2021, 11, 1, tzinfo=UTC)
LAST_DAY = datetime(2026, 10, 27, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

# type code -> (description for the type cache, aircraft attributes)
# (multi_engine, ifr, complex, pressurised, tailwheel, helicopter, float)
TYPE_ATTRS = {
    "C172": (0, 1, 0, 0, 0, 0, 0),
    "BE58": (1, 1, 1, 0, 0, 0, 0),
    "SW4": (1, 1, 1, 1, 0, 0, 0),
    "B738": (1, 1, 0, 1, 0, 0, 0),
    "B748": (1, 1, 0, 1, 0, 0, 0),
    "B789": (1, 1, 0, 1, 0, 0, 0),
    "A20N": (1, 1, 0, 1, 0, 0, 0),
    "A35K": (1, 1, 0, 1, 0, 0, 0),
    "CONC": (1, 1, 0, 1, 0, 0, 0),
}

# type -> list of (country code, registration suffix)
FLEET = {
    "C172": [("C", "GPFW"), ("C", "GPGG"), ("C", "GQLU"), ("C", "GZSA"), ("C", "GZYZ")],
    "BE58": [("G", "BAHN"), ("G", "BTFT"), ("G", "PSVS")],
    "SW4": [("VH", "UUB"), ("VH", "HWR"), ("VH", "SEZ"), ("VH", "SRQ")],
    "B738": [("EI", "DWK"), ("EI", "EMN"), ("EI", "EXD")],
    "B748": [("D", "ABYA"), ("D", "ABYC")],
    "B789": [("G", "ZBKA"), ("G", "ZBKB"), ("G", "ZBKC"), ("G", "ZBKD"), ("G", "ZBKK")],
    "A20N": [("G", "TTNA"), ("G", "TTNB"), ("G", "TTNC"), ("G", "TTND"), ("G", "TTNE")],
    "A35K": [("G", "XWBA"), ("G", "XWBB"), ("G", "XWBC"), ("G", "XWBD"), ("G", "XWBE")],
    "CONC": [("G", "BOAA"), ("G", "BOAF"), ("G", "BOAG"), ("F", "BTSD")],
}

# Type rating simulator courses: (type, (year, month), sessions)
SIM_COURSES = [
    ("SW4", (2022, 10), 2),
    ("B738", (2023, 6), 3),
    ("A20N", (2024, 2), 3),
    ("B748", (2024, 8), 3),
    ("B789", (2025, 2), 3),
    ("A35K", (2025, 8), 3),
    ("CONC", (2026, 3), 3),
]

# Real airports used as bases.
HOME = {
    "C172": "CZBB",
    "BE58": "EGTK",
    "SW4": "YMEN",
    "B738": "EIDW",
    "B748": "EDDF",
    "B789": "EGLL",
    "A20N": "EGLL",
    "A35K": "EGLL",
    "CONC": "EGLL",
}

# route = (dep, arr, (fn_out, minutes_out), (fn_ret, minutes_ret), dep_hour_utc)
ROUTES = {
    "C172": [
        ("CZBB", "CYXX", (None, 35), (None, 35), 8.0),
        ("CZBB", "CYYJ", (None, 40), (None, 40), 9.5),
        ("CZBB", "CYCD", (None, 30), (None, 30), 11.0),
        ("CZBB", "CYPK", (None, 20), (None, 20), 13.0),
        ("CZBB", "CYSE", (None, 30), (None, 30), 14.5),
        ("CZBB", "KBLI", (None, 30), (None, 30), 16.0),
    ],
    "BE58": [
        ("EGTK", "EGJJ", (None, 110), (None, 115), 8.0),
        ("EGTK", "LFAT", (None, 85), (None, 90), 9.5),
        ("EGTK", "EHRD", (None, 120), (None, 125), 7.5),
        ("EGTK", "EBOS", (None, 95), (None, 100), 10.0),
        ("EGTK", "EGNS", (None, 105), (None, 110), 8.0),
        ("EGTK", "EGPE", (None, 150), (None, 155), 9.0),
        ("EGLK", "EGJJ", (None, 115), (None, 120), 10.5),
        ("EGLK", "LFAT", (None, 80), (None, 85), 12.0),
    ],
    "SW4": [
        ("YMEN", "YKII", ("SH952", 70), ("SH953", 70), 8.0),
        ("YMEN", "YWYY", ("SH802", 80), ("SH803", 85), 9.5),
        ("YMEN", "YMLT", ("SH810", 85), ("SH811", 90), 7.0),
        ("YMEN", "YFLI", ("SH902", 85), ("SH903", 85), 11.0),
        ("YMEN", "YDPO", ("SH860", 80), ("SH861", 85), 13.0),
        ("YMLT", "YKII", ("SH720", 60), ("SH721", 60), 8.5),
        ("YMLT", "YFLI", ("SH730", 55), ("SH731", 55), 15.0),
    ],
    "B738": [
        ("EIDW", "EGSS", ("FR202", 70), ("FR203", 75), 6.5),
        ("EIDW", "EGPH", ("FR814", 65), ("FR815", 70), 7.5),
        ("EIDW", "EGCC", ("FR552", 55), ("FR553", 60), 8.5),
        ("EIDW", "EGGD", ("FR504", 60), ("FR505", 65), 10.0),
        ("EIDW", "EHAM", ("FR310", 95), ("FR311", 100), 11.5),
        ("EIDW", "LFOB", ("FR22", 95), ("FR23", 100), 13.0),
        ("EIDW", "LEMD", ("FR12", 155), ("FR13", 160), 6.0),
        ("EIDW", "LEBL", ("FR638", 145), ("FR639", 150), 14.0),
        ("EIDW", "LIRF", ("FR96", 180), ("FR97", 185), 7.0),
        ("EIDW", "EDDL", ("FR1502", 110), ("FR1503", 115), 12.0),
        ("EIDW", "EKCH", ("FR408", 130), ("FR409", 135), 15.0),
        ("EIDW", "EBCI", ("FR47", 95), ("FR48", 100), 9.0),
    ],
    "A20N": [
        ("EGLL", "EHAM", ("BA428", 75), ("BA429", 80), 7.0),
        ("EGLL", "LFPG", ("BA304", 80), ("BA305", 85), 8.5),
        ("EGLL", "EDDF", ("BA902", 95), ("BA903", 100), 9.5),
        ("EGLL", "LEMD", ("BA456", 150), ("BA457", 155), 7.5),
        ("EGLL", "LEBL", ("BA478", 130), ("BA479", 135), 10.0),
        ("EGLL", "LIRF", ("BA560", 150), ("BA561", 155), 8.0),
        ("EGLL", "LSZH", ("BA712", 100), ("BA713", 105), 12.5),
        ("EGLL", "EIDW", ("BA824", 75), ("BA825", 80), 13.0),
        ("EGLL", "EGPH", ("BA1442", 80), ("BA1443", 85), 15.0),
        ("EGLL", "LEPA", ("BA490", 145), ("BA491", 150), 6.5),
        ("EGLL", "LPPT", ("BA500", 165), ("BA501", 170), 11.0),
        ("EGLL", "EKCH", ("BA812", 105), ("BA813", 110), 14.0),
        ("EGLL", "LOWW", ("BA700", 135), ("BA701", 140), 9.0),
    ],
    "B748": [
        ("EDDF", "KJFK", ("LH400", 485), ("LH401", 455), 10.0),
        ("EDDF", "KORD", ("LH430", 540), ("LH431", 500), 9.5),
        ("EDDF", "KLAX", ("LH450", 690), ("LH451", 640), 9.0),
        ("EDDF", "KSFO", ("LH454", 685), ("LH455", 635), 9.5),
        ("EDDF", "SBGR", ("LH506", 720), ("LH507", 700), 20.0),
        ("EDDF", "FAOR", ("LH572", 645), ("LH573", 660), 21.0),
        ("EDDF", "WSSS", ("LH778", 765), ("LH779", 780), 21.5),
        ("EDDF", "RJTT", ("LH714", 705), ("LH715", 730), 12.0),
        ("EDDF", "CYYZ", ("LH470", 500), ("LH471", 470), 13.0),
        ("EDDF", "VHHH", ("LH796", 700), ("LH797", 735), 21.0),
        ("EDDF", "KBOS", ("LH422", 450), ("LH423", 420), 16.0),
        ("EDDF", "KIAD", ("LH418", 500), ("LH419", 465), 17.0),
    ],
    "B789": [
        ("EGLL", "KJFK", ("BA117", 465), ("BA112", 415), 10.5),
        ("EGLL", "KLAX", ("BA269", 665), ("BA268", 600), 15.5),
        ("EGLL", "KSFO", ("BA285", 660), ("BA284", 605), 14.5),
        ("EGLL", "KORD", ("BA297", 525), ("BA296", 480), 11.0),
        ("EGLL", "KBOS", ("BA213", 430), ("BA212", 385), 13.5),
        ("EGLL", "KIAD", ("BA217", 470), ("BA216", 425), 12.5),
        ("EGLL", "CYYZ", ("BA93", 475), ("BA92", 430), 16.0),
        ("EGLL", "OMDB", ("BA107", 410), ("BA106", 455), 20.0),
        ("EGLL", "VHHH", ("BA31", 710), ("BA32", 765), 21.0),
        ("EGLL", "WSSS", ("BA11", 775), ("BA12", 790), 19.5),
        ("EGLL", "VABB", ("BA139", 535), ("BA138", 565), 13.0),
        ("EGLL", "FAOR", ("BA55", 660), ("BA56", 680), 18.5),
        ("EGLL", "SBGR", ("BA247", 705), ("BA246", 690), 20.5),
    ],
    "A35K": [
        ("EGLL", "KJFK", ("BA175", 460), ("BA178", 415), 9.0),
        ("EGLL", "KLAX", ("BA281", 670), ("BA282", 600), 14.0),
        ("EGLL", "KSFO", ("BA287", 665), ("BA286", 610), 15.0),
        ("EGLL", "KSEA", ("BA51", 590), ("BA50", 545), 12.0),
        ("EGLL", "KDEN", ("BA219", 595), ("BA218", 550), 11.5),
        ("EGLL", "KMIA", ("BA209", 575), ("BA208", 530), 10.5),
        ("EGLL", "KATL", ("BA227", 565), ("BA226", 515), 12.5),
        ("EGLL", "OTHH", ("BA123", 405), ("BA122", 440), 19.0),
        ("EGLL", "VIDP", ("BA257", 510), ("BA256", 540), 13.0),
        ("EGLL", "VOBL", ("BA119", 575), ("BA118", 605), 14.0),
        ("EGLL", "RJTT", ("BA5", 715), ("BA6", 745), 11.0),
        ("EGLL", "RKSI", ("BA17", 685), ("BA18", 715), 12.5),
        ("EGLL", "VTBS", ("BA9", 695), ("BA10", 745), 13.5),
        ("EGLL", "HKJK", ("BA65", 520), ("BA64", 530), 19.5),
        ("EGLL", "FACT", ("BA59", 705), ("BA58", 715), 18.0),
        ("EGLL", "SAEZ", ("BA245", 800), ("BA244", 785), 20.0),
        ("EGLL", "MMMX", ("BA243", 695), ("BA242", 680), 21.0),
    ],
    "CONC": [
        ("EGLL", "KJFK", ("BA001", 210), ("BA002", 205), 10.0),
        ("EGLL", "TBPB", ("BA273", 245), ("BA272", 250), 8.5),
        ("LFPG", "KJFK", ("AF001", 225), ("AF002", 220), 10.5),
    ],
}

# Phase windows: (first month, last month, type, trips per month)
PHASES = [
    ((2021, 11), (2022, 4), "C172", 3),
    ((2022, 5), (2022, 10), "BE58", 3),
    ((2022, 11), (2023, 6), "SW4", 3),
    ((2023, 7), (2024, 2), "B738", 4),
    ((2024, 3), (2024, 8), "A20N", 4),
    ((2024, 9), (2025, 2), "B748", 3),
    ((2025, 3), (2025, 8), "B789", 3),
    ((2025, 9), (2026, 3), "A35K", 3),
    ((2026, 4), (2026, 10), "CONC", 2),
]

CREW_NAMES = [
    "Steve Bytheway",
    "Neil Overall",
    "Mary Candles",
    "Colin Wardrobe",
    "Peter Sideboard",
    "Graham Teapot",
    "Susan Pillow",
    "Andrew Socket",
    "Rachel Curtain",
    "David Kettle",
    "Paul Biscuit",
    "Emma Lampshade",
    "Simon Bin",
    "Diane Carpet",
    "Mark Fence",
    "Lucy Radiator",
    "Tom Sprocket",
    "Hannah Bucket",
    "Ian Doorbell",
    "Fiona Mattress",
    "Nigel Cushion",
    "Claire Windows",
    "Robert Slippers",
    "Janet Toaster",
    "Michael Spoon",
    "Barbara Handbag",
    "Gary Bannister",
    "Emily Saucepan",
    "Kevin Doormat",
    "Sophie Trolley",
]

VERSION_LEDGER = [
    (1, '0.0.1', 'Initial schema with all tables.', 1),
    (2, '0.0.12+1', 'Added unique index uq_aircraft_registration on aircraft(country_code, registration).', 1),
    (3, '0.0.12+1', 'Added version ledger table for tracking schema history.', 1),
    (4, '0.1.0', 'Added owner_id column to user_limits table for multi-user sync support.', 1),
    (5, '0.1.0', 'Added created_at and updated_at columns to user_limits table for sync support.', 1),
    (6, '0.1.0', 'Schema version 6 baseline.', 1),
    (7, '0.1.0', 'Ensured created_at and updated_at columns exist on user_limits table.', 1),
    (8, '0.1.0', 'Ensured created_at and updated_at columns exist on user_limits table.', 1),
    (9, '0.1.0', 'Normalized corrupted millisecond timestamps to unix seconds across all sync tables.', 1),
    (10, '0.1.0', 'Added show_logbook_view column to flights_settings table for logbook table view.', 1),
    (11, '0.1.0', 'Added show_flight_number column to flights_settings table.', 1),
    (12, '0.1.0', 'Removed enable_analytics column from app_settings table.', 1),
    (13, '0.1.0', 'Widened sync_deletions.row_id from INTEGER to TEXT to support text primary keys (user_limits).', 1),
    (14, '0.1.0', 'Removed unused hangar_doors_sound column from app_settings.', 1),
    (15, '0.1.0', 'Removed unused last_selected_tab column from general_settings, added composite index on flights(owner_id, updated_at), and added created_at defaults to flights_settings and routes_settings.', 1),
    (16, '0.1.0', 'Renamed the date-based currencies feature to expiries and the metric-based user_limits feature to user_currency, including sidebar settings columns, nav keys, startup tab, sync deletions, and dashboard widget JSON.', 1),
    (17, '0.1.0', 'Added idx_flights_route index on flights(route_id).', 1),
    (18, '0.1.0', 'Converted the id columns of the 10 sync tables from BIGINT to UUID text to prevent cross-device primary key collisions.', 1),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def month_key(dt: datetime) -> tuple[int, int]:
    return (dt.year, dt.month)


def month_of(y: int, m: int) -> datetime:
    return datetime(y, m, 1, tzinfo=UTC)


def add_month(y: int, m: int, delta: int = 1) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


def in_phase(ym: tuple[int, int], phase) -> bool:
    return phase[0] <= ym <= phase[1]


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3440.065  # Earth radius in nautical miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def solar_elevation(lat: float, lon: float, when: datetime) -> float:
    """Rough solar elevation in degrees (good enough for day/night splits)."""
    n = when.timetuple().tm_yday
    decl = math.radians(23.44 * math.sin(math.radians(360 / 365 * (284 + n))))
    utc_hours = when.hour + when.minute / 60 + when.second / 3600
    hour_angle = math.radians(15 * (utc_hours + lon / 15 - 12))
    phi = math.radians(lat)
    sin_el = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_el))))


def night_split(
    dep_dt: datetime,
    minutes: int,
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> int:
    """Return the number of night minutes, sampled along a straight track."""
    if minutes <= 0:
        return 0
    steps = max(8, minutes // 5)
    night = 0
    for i in range(steps + 1):
        f = i / steps
        when = dep_dt + timedelta(minutes=f * minutes)
        lat = lat1 + (lat2 - lat1) * f
        lon = lon1 + (lon2 - lon1) * f
        if solar_elevation(lat, lon, when) < 0:
            night += 1
    return round(minutes * night / (steps + 1))


def to_epoch(dt: datetime) -> int:
    return int(dt.timestamp())


_ID_COUNTER = itertools.count(1)


def uuid_str(prefix: str = "row") -> str:
    """Stable id, so regenerating produces the same database."""
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"falcon-demo/{prefix}/{next(_ID_COUNTER)}")
    )


def airport_zone(tz_name: object) -> ZoneInfo:
    try:
        return ZoneInfo(str(tz_name))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Load airports
# ---------------------------------------------------------------------------


def load_airports(path: Path) -> dict[str, dict[str, object]]:
    airports: dict[str, dict[str, object]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            icao = (row.get("icao") or "").strip().upper()
            if not icao:
                continue
            try:
                lat = float(row.get("lat") or 0)
                lon = float(row.get("lon") or 0)
            except ValueError:
                continue
            elevation = row.get("elevation") or ""
            airports[icao] = {
                "icao": icao,
                "iata": row.get("iata") or None,
                "name": row.get("name") or icao,
                "city": row.get("city") or None,
                "subd": row.get("subd") or None,
                "country": row.get("country") or None,
                "lat": lat,
                "lon": lon,
                "elevation": int(elevation) if elevation.lstrip("-").isdigit() else None,
                "tz": row.get("tz") or None,
            }
    return airports


# ---------------------------------------------------------------------------
# Build the flight list
# ---------------------------------------------------------------------------


@dataclass
class Flight:
    date: datetime
    type: str
    dep: str
    arr: str
    flight_number: str | None
    minutes: int
    role: str
    crew: str | None
    simulator: bool = False


def pick_crew(rng: random.Random) -> str:
    return rng.choice(CREW_NAMES)


def build_flights(rng: random.Random) -> list[Flight]:
    flights: list[Flight] = []
    busy: dict[str, str] = {}

    def day_key(when: datetime) -> str:
        return when.strftime("%Y-%m-%d")

    def add(flight: Flight, allow_same_type: bool = False) -> bool:
        if not (FIRST_DAY <= flight.date <= LAST_DAY):
            return False
        key = day_key(flight.date)
        owner = busy.get(key)
        if owner is not None and (owner != flight.type or not allow_same_type):
            return False
        busy[key] = flight.type
        flights.append(flight)
        return True

    def free_date(start: datetime, max_shift: int = 3) -> datetime | None:
        for offset in range(max_shift + 1):
            candidate = start + timedelta(days=offset)
            if day_key(candidate) not in busy:
                return candidate
        return None

    def ga_trip(base_type: str, month_start: datetime) -> None:
        dep, arr, (_, out_min), (_, ret_min), hour = rng.choice(ROUTES[base_type])
        date = None
        for _ in range(30):
            candidate = month_start + timedelta(days=rng.randint(2, 26))
            if day_key(candidate) in busy:
                continue
            if (candidate.year, candidate.month) == (2026, 10) and candidate.day in (24, 26):
                continue
            date = candidate
            break
        if date is None:
            return
        out_dep = date.replace(hour=int(hour), minute=rng.choice([0, 10, 20, 30, 40, 50]))
        if not add(Flight(out_dep, base_type, dep, arr, None, out_min, "pilotInCommand", None)):
            return
        ret_dep = out_dep + timedelta(minutes=out_min + rng.choice([45, 60, 75]))
        if ret_dep.date() != date.date():
            ret_dep = date.replace(hour=18, minute=30)
        add(
            Flight(ret_dep, base_type, arr, dep, None, ret_min, "pilotInCommand", None),
            allow_same_type=True,
        )

    for y in range(2021, 2027):
        for m in range(1, 13):
            ym = (y, m)
            if ym < (2021, 11) or ym > (2026, 10):
                continue
            month_start = month_of(y, m)
            phase = next((p for p in PHASES if in_phase(ym, p)), None)
            assert phase is not None, f"no phase for {ym}"
            main_type, trips = phase[2], phase[3]

            # Long and short haul trips for the main type.
            for trip in range(trips):
                route = rng.choice(ROUTES[main_type])
                dep, arr, (fn_out, out_min), (fn_ret, ret_min), hour = route
                base_day = 2 + trip * 7 + rng.randint(0, 2)
                if ym == (2026, 10):
                    base_day = 2 + trip * 5
                out_date = free_date(month_start + timedelta(days=base_day - 1))
                if out_date is None:
                    continue
                if main_type in {"B748", "B789", "A35K", "CONC"}:
                    stay = rng.randint(2, 4)
                elif main_type in {"C172", "BE58"}:
                    stay = 0
                else:
                    stay = rng.randint(0, 1)

                out_dep = out_date.replace(
                    hour=int(hour), minute=rng.choice([0, 10, 20, 30, 40, 50])
                )
                training = main_type == "C172" and ym <= (2022, 4) and rng.random() < 0.6
                out_block = out_min + rng.randint(-4, 6)
                if not add(
                    Flight(
                        out_dep,
                        main_type,
                        dep,
                        arr,
                        fn_out,
                        out_block,
                        "student" if training else "pilotInCommand",
                        pick_crew(rng)
                        if training or main_type not in {"C172", "BE58"}
                        else None,
                    )
                ):
                    continue

                if stay == 0:
                    turn = rng.randint(60, 90)
                    ret_dep = out_dep + timedelta(minutes=out_block + turn)
                    if ret_dep.date() != out_date.date():
                        ret_dep = out_date.replace(hour=min(21, out_dep.hour + 3), minute=30)
                else:
                    ret_date = free_date(out_date + timedelta(days=stay), 2)
                    if ret_date is None:
                        continue
                    ret_hour = (hour + 2.5) % 24
                    ret_dep = ret_date.replace(
                        hour=int(ret_hour), minute=rng.choice([0, 10, 20, 30, 40, 50])
                    )
                add(
                    Flight(
                        ret_dep,
                        main_type,
                        arr,
                        dep,
                        fn_ret,
                        ret_min + rng.randint(-4, 6),
                        "student" if training else "pilotInCommand",
                        pick_crew(rng)
                        if training or main_type not in {"C172", "BE58"}
                        else None,
                    ),
                    allow_same_type=stay == 0,
                )

            # Club flying keeps the C172 current through the whole logbook.
            if main_type != "C172" and (m % 2 == 0 or main_type == "CONC"):
                ga_trip("C172", month_start)

            # Twin time on the Baron, now and then, once the pilot has the rating.
            if main_type not in {"C172", "BE58"} and m % 5 == 0:
                ga_trip("BE58", month_start)

            # From the Concorde phase, keep flying the A350 as well.
            if main_type == "CONC":
                route = rng.choice(ROUTES["A35K"])
                dep, arr, (fn_out, out_min), (fn_ret, ret_min), hour = route
                out_date = free_date(month_start + timedelta(days=rng.randint(3, 12)))
                if out_date is not None:
                    out_dep = out_date.replace(hour=int(hour), minute=rng.choice([0, 20, 40]))
                    add(
                        Flight(
                            out_dep,
                            "A35K",
                            dep,
                            arr,
                            fn_out,
                            out_min + rng.randint(-4, 6),
                            "pilotInCommand",
                            pick_crew(rng),
                        )
                    )
                    ret_date = free_date(out_date + timedelta(days=rng.randint(2, 4)), 2)
                    if ret_date is not None:
                        ret_dep = ret_date.replace(
                            hour=int((hour + 2.5) % 24), minute=rng.choice([0, 20, 40])
                        )
                        add(
                            Flight(
                                ret_dep,
                                "A35K",
                                arr,
                                dep,
                                fn_ret,
                                ret_min + rng.randint(-4, 6),
                                "pilotInCommand",
                                pick_crew(rng),
                            )
                        )

    # A final trip in the last week so "recent flights" is current.
    add(
        Flight(
            datetime(2026, 10, 24, 9, 20, tzinfo=UTC),
            "A35K",
            "EGLL",
            "KJFK",
            "BA175",
            462,
            "pilotInCommand",
            pick_crew(rng),
        )
    )
    add(
        Flight(
            datetime(2026, 10, 26, 12, 40, tzinfo=UTC),
            "A35K",
            "KJFK",
            "EGLL",
            "BA178",
            418,
            "pilotInCommand",
            pick_crew(rng),
        )
    )

    # Type rating simulator sessions.
    for type_code, (sy, sm), sessions in SIM_COURSES:
        start = month_of(sy, sm)
        for i in range(sessions):
            when = free_date(start + timedelta(days=4 + i * 5 - 1), 5)
            if when is None:
                continue
            add(
                Flight(
                    when.replace(hour=9 + (i % 2) * 5),
                    type_code,
                    HOME[type_code],
                    HOME[type_code],
                    None,
                    240,
                    "student",
                    pick_crew(rng),
                    simulator=True,
                )
            )

    flights.sort(key=lambda f: f.date)
    return flights


# ---------------------------------------------------------------------------
# Assemble the database
# ---------------------------------------------------------------------------


def build(output: Path, airports_csv: Path, schema: Path) -> None:
    airports = load_airports(airports_csv)
    rng = random.Random(20261028)

    flights = build_flights(rng)
    used_icao = sorted({f.dep for f in flights} | {f.arr for f in flights})
    missing = [code for code in used_icao if code not in airports]
    if missing:
        raise SystemExit(f"airports missing from {airports_csv}: {', '.join(missing)}")

    # Settings and master data -------------------------------------------------
    now_epoch = to_epoch(NOW)
    aircraft_rows: list[tuple] = []
    aircraft_ids: dict[str, str] = {}
    for type_code, registrations in FLEET.items():
        multi, ifr, complex_, pressurised, tail, heli, float_ = TYPE_ATTRS[type_code]
        for cc, reg in registrations:
            reg_id = uuid_str()
            aircraft_ids[f"{cc}-{reg}"] = reg_id
            aircraft_rows.append(
                (
                    reg_id,
                    type_code,
                    cc,
                    reg,
                    multi,
                    ifr,
                    complex_,
                    pressurised,
                    tail,
                    heli,
                    float_,
                    0,
                    0,
                    None,
                    now_epoch,
                )
            )
        # Simulator entry for the types that have a type rating course.
        if type_code not in {course[0] for course in SIM_COURSES}:
            continue
        sim_id = uuid_str()
        sim_key = f"{type_code} SIM"
        aircraft_ids[sim_key] = sim_id
        aircraft_rows.append(
            (
                sim_id,
                type_code,
                "",
                sim_key,
                multi,
                ifr,
                0,
                1,
                0,
                0,
                0,
                1,
                0,
                None,
                now_epoch,
            )
        )

    crew_rows: list[tuple] = []
    crew_ids: dict[str, str] = {}
    for name in CREW_NAMES:
        crew_id = uuid_str()
        first, last = name.split(" ", 1)
        crew_ids[name] = crew_id
        crew_rows.append((crew_id, name, first, last, None, 0, now_epoch, None, now_epoch))

    # Saved routes: one per direction actually flown.
    used_routes: dict[tuple[str, str, str], int] = {}
    flight_rows: list[tuple] = []

    def route_key(dep: str, arr: str, fn: str | None) -> tuple[str, str, str]:
        return (dep, arr, fn or "")

    day_order: dict[str, int] = {}
    for flight in flights:
        dep_airport = airports[flight.dep]
        arr_airport = airports[flight.arr]
        local_dep = flight.date.replace(tzinfo=airport_zone(dep_airport["tz"]))
        dep_dt = local_dep.astimezone(UTC)
        stored_date = datetime(
            flight.date.year,
            flight.date.month,
            flight.date.day,
            10,
            tzinfo=UTC,
        )
        minutes = max(15, flight.minutes)
        arr_dt = dep_dt + timedelta(minutes=minutes)

        if flight.simulator:
            night = 0
            approach = None
            takeoff_day, takeoff_night = 0, 0
            landing_day, landing_night = 0, 0
            instrument = None
            ground_inst = minutes
        else:
            night = night_split(
                dep_dt,
                minutes,
                float(dep_airport["lat"]),
                float(dep_airport["lon"]),
                float(arr_airport["lat"]),
                float(arr_airport["lon"]),
            )
            night = min(night, minutes)
            takeoff_night = 1 if solar_elevation(float(dep_airport["lat"]), float(dep_airport["lon"]), dep_dt) < 0 else 0
            takeoff_day = 1 - takeoff_night
            landing_night = 1 if solar_elevation(float(arr_airport["lat"]), float(arr_airport["lon"]), arr_dt) < 0 else 0
            landing_day = 1 - landing_night
            instrument = 0
            if flight.type not in {"C172"} and rng.random() < 0.35:
                instrument = rng.randint(5, 25)
            ground_inst = None
            approach = None

        # Approach log: most sectors fly an instrument approach, GA mostly visual.
        if not flight.simulator and approach is None:
            if flight.type in {"C172", "BE58"}:
                if rng.random() < 0.25:
                    approach = rng.choice(["VOR", "NDB", "RNAV"])
            elif rng.random() < 0.72:
                approach = rng.choice(["ILS", "ILS", "ILS", "RNAV", "RNP"])

        approach_details = None
        if approach:
            runway = rng.choice(["09", "18", "22", "26", "27L", "27R", "33"])
            approach_details = f"{approach} {runway}"

        date_key = stored_date.strftime("%Y-%m-%d")
        day_order[date_key] = day_order.get(date_key, 0) + 1
        order = day_order[date_key] - 1

        key = route_key(flight.dep, flight.arr, flight.flight_number)
        if flight.simulator:
            route_id = None
            aircraft_id = aircraft_ids[f"{flight.type} SIM"]
            aircraft_registration = f"{flight.type} SIM"
            flight_type = "details"
            route_text = None
        else:
            route_id = None
            if flight.flight_number:
                if key not in used_routes:
                    used_routes[key] = len(used_routes) + 1
                route_id = used_routes[key]
            if flight.type == "CONC":
                if (flight.flight_number or "").startswith("AF"):
                    cc, reg = ("F", "BTSD")
                else:
                    cc, reg = rng.choice([pair for pair in FLEET["CONC"] if pair[0] == "G"])
            else:
                cc, reg = rng.choice(FLEET[flight.type])
            aircraft_id = aircraft_ids[f"{cc}-{reg}"]
            aircraft_registration = f"{cc}-{reg}"
            flight_type = "route"
            route_text = f"{flight.dep} - {flight.arr}"

        crew_name = flight.crew
        crew_id = crew_ids[crew_name] if crew_name else None

        if flight.simulator:
            is_ifr = None
        elif flight.type == "C172":
            is_ifr = 1 if approach else 0
        else:
            is_ifr = 1

        created = min(NOW, dep_dt + timedelta(days=1, hours=2))
        flight_rows.append(
            (
                uuid_str(),
                to_epoch(stored_date),
                order,
                aircraft_id,
                flight_type,
                flight.role,
                crew_id,
                crew_name,
                route_text,
                route_id,
                flight.flight_number,
                aircraft_registration,
                minutes,
                minutes - night,
                night,
                "utc",
                dep_dt.strftime("%H:%M"),
                arr_dt.strftime("%H:%M"),
                None,
                None,
                instrument,
                None,
                ground_inst,
                approach if approach in {"NDB", "VOR", "LOC", "SDF", "TACAN", "ASR"} else None,
                approach if approach in {"ILS", "GLS", "RNP", "MLS", "PAR"} else None,
                approach_details,
                1 if not flight.simulator else None,
                1 if not flight.simulator else None,
                takeoff_day if not flight.simulator else None,
                takeoff_night if not flight.simulator else None,
                landing_day if not flight.simulator else None,
                landing_night if not flight.simulator else None,
                None,
                None,
                None,
                1 if flight.simulator else 0,
                is_ifr,
                None,
                None,
                to_epoch(created),
                None,
                to_epoch(created),
            )
        )

    # ------------------------------------------------------------------ write
    if output.exists():
        output.unlink()
    conn = sqlite3.connect(output)
    try:
        conn.executescript(schema.read_text())
        conn.execute("PRAGMA user_version = 19")

        conn.executemany(
            "INSERT INTO version_ledger (version, first_app_release, schema_change, minimum_supported) VALUES (?,?,?,?)",
            VERSION_LEDGER,
        )

        conn.executemany(
            "INSERT INTO airports (icao, iata, name, city, subd, country, lat, lon, elevation, tz) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    a["icao"],
                    a["iata"],
                    a["name"],
                    a["city"],
                    a["subd"],
                    a["country"],
                    a["lat"],
                    a["lon"],
                    a["elevation"],
                    a["tz"],
                )
                for a in airports.values()
            ],
        )

        # Settings ------------------------------------------------------------
        widgets = [
            {"id": "default-1", "type": "totalTime", "order": 0, "config": {}},
            {"id": "default-2", "type": "timePeriod", "order": 1, "config": {"days": 90}},
            {"id": "default-3", "type": "hoursInType", "order": 2, "config": {}},
            {"id": "default-4", "type": "mostFlown", "order": 3, "config": {"category": "rego"}},
            {"id": "default-5", "type": "nextDue", "order": 4, "config": {}},
            {"id": "default-6", "type": "currencyProgress", "order": 5, "config": {"currencyId": "currency-recency"}},
        ]

        conn.execute(
            "INSERT INTO app_settings (id, storage_mode, theme_mode, backups_to_keep, backup_frequency_days, "
            "last_backed_up, accent_color, font_family, theme_key, oled_dark_mode, has_seen_storage_explanation, "
            "background_launch_pending, hangar_doors_splash, created_at, updated_at) "
            "VALUES (1,'local','dark',3,7,?,'blue','inter','falcon_default',0,1,0,1,?,?)",
            (now_epoch, now_epoch, now_epoch),
        )
        conn.execute(
            "INSERT INTO general_settings (id, startup_tab, language, airports_csv_hash, coordinates_format, "
            "dashboard_widgets_json, created_at, updated_at) VALUES (1,'dashboard','system',?,'dd',?,?,?)",
            (
                hashlib.md5(airports_csv.read_bytes()).hexdigest(),
                json.dumps(widgets, separators=(",", ":")),
                now_epoch,
                now_epoch,
            ),
        )
        conn.execute(
            "INSERT INTO sidebar_settings (id, item_order, expanded_on_launch, created_at, updated_at) "
            "VALUES (1,?,1,?,?)",
            (
                "dashboard,flights,routes,aircraft,crew,expiries,currency,reports,airports,tools",
                now_epoch,
                now_epoch,
            ),
        )
        conn.execute(
            "INSERT INTO flights_settings (id, pilot_name, default_flight_type, date_format, flight_time_format, "
            "calculate_time_mode, logbook_country, show_summary_card, show_day_night, show_routes_in_wizard, "
            "show_logbook_view, show_flight_number, created_at, updated_at) "
            "VALUES (?, 'Alex Whitfield','route','dd mmm yyyy','decimal1','utc',NULL,1,1,1,0,1,?,?)",
            (uuid_str(), now_epoch, now_epoch),
        )
        conn.execute(
            "INSERT INTO crew_settings (id, name_display_format, sort_order, show_badge, first_name_initial_only, "
            "nickname_only, archive_enabled, show_table_view, created_at, updated_at) "
            "VALUES (?,'firstLast','firstLast',1,0,0,1,0,?,?)",
            (uuid_str(), now_epoch, now_epoch),
        )
        conn.execute(
            "INSERT INTO aircraft_settings (id, sort_order, sort_direction, archive_enabled, default_country_code, "
            "created_at, updated_at) VALUES (?,'registration','ascending',1,'G',?,?)",
            (uuid_str(), now_epoch, now_epoch),
        )
        conn.execute(
            "INSERT INTO expiry_settings (id, green_threshold, orange_threshold, show_sidebar_badge, created_at, updated_at) "
            "VALUES (?,90,30,1,?,?)",
            (uuid_str(), now_epoch, now_epoch),
        )
        conn.execute(
            "INSERT INTO routes_settings (id, show_table_view, created_at, updated_at) VALUES (?,0,?,?)",
            (uuid_str(), now_epoch, now_epoch),
        )

        conn.executemany(
            "INSERT INTO aircraft (id, type, country_code, registration, multi_engine, ifr, complex, pressurised, "
            "tailwheel, helicopter, float_plane, simulator, archived, owner_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            aircraft_rows,
        )
        conn.executemany(
            "INSERT INTO crew_members (id, name, first_name, last_name, note, archived, created_at, owner_id, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            crew_rows,
        )
        conn.executemany(
            "INSERT INTO aircraft_type_settings (type, multi_engine, ifr, complex, pressurised, tailwheel, simulator, "
            "helicopter, float_plane, owner_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL,?)",
            [
                (t, a[0], a[1], a[2], a[3], a[4], 0, a[5], a[6], now_epoch)
                for t, a in TYPE_ATTRS.items()
            ],
        )

        conn.executemany(
            "INSERT INTO saved_routes (id, flight_number, departure_icao, arrival_icao, distance_nm, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                (
                    route_id,
                    fn,
                    dep,
                    arr,
                    round(
                        haversine_nm(
                            float(airports[dep]["lat"]),
                            float(airports[dep]["lon"]),
                            float(airports[arr]["lat"]),
                            float(airports[arr]["lon"]),
                        ),
                        1,
                    ),
                    now_epoch,
                    now_epoch,
                )
                for (dep, arr, fn), route_id in used_routes.items()
            ],
        )

        conn.executemany(
            "INSERT INTO flights (id, date, day_order, aircraft_id, flight_type, crew_role, crew_member_id, "
            "crew_members_name, route, route_id, flight_number, aircraft_registration, total_time, day_time, night_time, "
            "calc_time_mode, time_start, time_stop, hobbs_start, hobbs_stop, actual_instrument, simulated_instrument, "
            "ground_instrument, approaches2d, approaches3d, approach_details, takeoffs, landings, takeoff_day, "
            "takeoff_night, landing_day, landing_night, water_takeoffs, water_landings, custom_columns, is_simulator, "
            "is_ifr, icus_day_time, icus_night_time, created_at, owner_id, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            flight_rows,
        )

        # Expiries -------------------------------------------------------------
        expiries = [
            ("Class 1 Medical", datetime(2027, 3, 14, tzinfo=UTC)),
            ("ATPL Licence", datetime(2031, 8, 22, tzinfo=UTC)),
            ("A350 Type Rating", datetime(2027, 8, 30, tzinfo=UTC)),
            ("B787 Type Rating", datetime(2027, 7, 15, tzinfo=UTC)),
            ("B747 Type Rating", datetime(2027, 6, 1, tzinfo=UTC)),
            ("B737 Type Rating", datetime(2027, 5, 20, tzinfo=UTC)),
            ("A320 Type Rating", datetime(2027, 4, 12, tzinfo=UTC)),
            ("Concorde Type Rating", datetime(2027, 2, 28, tzinfo=UTC)),
            ("SEP (Land)", datetime(2026, 12, 15, tzinfo=UTC)),
            ("CRM Refresher", datetime(2026, 11, 14, tzinfo=UTC)),
            ("LPC", datetime(2027, 1, 9, tzinfo=UTC)),
        ]
        conn.executemany(
            "INSERT INTO expiries (id, name, due_date, created_at, owner_id, updated_at) VALUES (?,?,?,?,NULL,?)",
            [(uuid_str(), name, to_epoch(due), now_epoch, now_epoch) for name, due in expiries],
        )

        # Currency -------------------------------------------------------------
        currencies = [
            ("currency-recency", "30-day recency", "landingCount", "rolling30", 3.0, 0, "requirement"),
            ("currency-instrument", "Instrument recency", "instrumentTime", "rolling365", 3.0, 1, "requirement"),
            ("currency-30day", "30-day flight time", "totalTime", "rolling30", 100.0, 2, "limit"),
            ("currency-annual", "Annual flight time", "totalTime", "calendarYear", 900.0, 3, "limit"),
        ]
        conn.executemany(
            "INSERT INTO user_currency (id, title, metric, period, threshold, sort_order, is_active, type, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,1,?,?,?)",
            [
                (cid, title, metric, period, threshold, order, kind, now_epoch, now_epoch)
                for cid, title, metric, period, threshold, order, kind in currencies
            ],
        )

        conn.commit()
    finally:
        conn.close()

    print(f"Wrote {output} ({output.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"  flights: {len(flights)}")
    print(f"  aircraft: {len(aircraft_rows)}")
    print(f"  crew: {len(crew_rows)}")
    print(f"  saved routes: {len(used_routes)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--airports-csv",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "app" / "assets" / "data" / "airports.csv",
        help="Path to the Falcon airports.csv asset",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "schema.sql",
        help="Path to schema.sql",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "falcon_demo.sqlite",
        help="Output database path",
    )
    args = parser.parse_args()
    if not args.airports_csv.exists():
        raise SystemExit(f"airports CSV not found: {args.airports_csv}")
    build(args.out, args.airports_csv, args.schema)


if __name__ == "__main__":
    main()
