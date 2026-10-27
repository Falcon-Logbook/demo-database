# Falcon demo database

Demo data for Falcon: a complete, ready-to-open logbook you can use for screenshots,
documentation, testing and demos without touching your own flights.

This repo is data, not code. The app lives at [Falcon-Logbook/app](https://github.com/Falcon-Logbook/app).

---

## What is in it

| | |
| :--- | :--- |
| **Pilot** | Alex Whitfield (fictional) |
| **Period** | November 2021 to October 2026 |
| **Flights** | 486 sectors across 60 consecutive months |
| **Aircraft types** | C172, Baron 58, Metro 23, 737-800, A320neo, 747-8, 787-9, A350-1000, Concorde |
| **Aircraft** | 43 airframes, including seven full-flight simulators |
| **Crew** | 30 pilots, all invented |
| **Routes** | 134 saved routes |
| **Hours** | about 1,885 hours, with 685 hours at night |
| **Airports** | the full Falcon airport database, 28,291 entries |
| **Extras** | 11 expiries, 4 currencies, a filled-in dashboard |

The logbook moves from club flying in a Cessna 172, through twin time in a Baron and a regional
Metro, into the airlines: Ryanair 737s out of Dublin, British Airways A320s out of Heathrow,
Lufthansa 747-8s out of Frankfurt, then the BA 787, A350 and a final run of Concorde sectors.

---

## What is real and what is not

This is demo data, so treat every row as an illustration rather than a record.

- Airports, their coordinates and time zones come from the Falcon airport list, so they are real.
- Airlines, their routes and flight numbers, and aircraft registrations are real enough to look
  right in a screenshot.
- The pilot, the crew, the dates, the times and the order of flights are all invented. No crew
  member is a real person.

---

## How to use it

### In the Falcon app

Download `falcon_demo.sqlite` and point Falcon at it from Settings -> Data, then select the
database file. Work on a copy: the app writes to the file it opens.

The file is also a valid Falcon backup. To use the app's restore flow, copy it into the `Backups`
folder beside the active database and name it `falcon_backup_<timestamp>.db`, then restore it from
the app.

### With the MCP server

```bash
falcon_mcp --db /path/to/falcon_demo.sqlite
```

Then ask things like "how many night hours did I fly in the last 90 days?" or "what is my
most-flown route?".

### With any SQLite client

The database uses the standard Falcon schema (version 19, app 0.0.14+). `schema.sql` has the full
DDL. Times are integer minutes, dates are unix seconds, and a boolean is 0 or 1.

| File | What it is |
| :--- | :--- |
| `falcon_demo.sqlite` | The ready-to-use demo logbook |
| `schema.sql` | Falcon schema version 19 |

---

## Notes on the data

- Times are stored in minutes, as Falcon stores them (`total_time`, `day_time`, `night_time`).
- `time_start` and `time_stop` are UTC clock times; the flights use UTC calculation mode.
- Day and night come from the sun's elevation sampled along the route, so night hours are longer on
  winter sectors and on overnight eastbound flights.
- Flight dates are stored at 10:00 UTC so the calendar date renders correctly in most time zones.
- Route distances are great-circle distances between the two airports.
- Simulator sessions are ordinary logbook entries with `is_simulator = 1`, including the type
  rating courses for the Metro, 737, A320, 747, 787, A350 and Concorde.

---

## Credits

Airport data from [mwgg/Airports](https://github.com/mwgg/Airports), MIT licensed. Airline names,
flight numbers and aircraft registrations belong to their respective owners and appear here only
so the demo data looks like the real thing. Everything is provided as-is for testing, documentation
and marketing.
