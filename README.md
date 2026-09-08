# Are Banks Tightening?

Lending standards lead the credit cycle; lending volumes trail it. This page computes
both claims. Live at [lending.chrislawrence.ca](https://lending.chrislawrence.ca).

No framework, no build step, no package manager. Plain HTML, CSS and vanilla JS on an
nginx front, with a stdlib-Python updater sidecar. Part of the economic tracker
collection (diesel, debt, jobs, yield, housing, credit) on the shared
[`econ-core`](https://github.com/Lawrence908/econ-core/blob/main/CONTRACT.md) series contract.

## Layout

```
src/index.html    markup, styling, the TimeChart canvas engine, every render function
data/series.json  machine-fetched, rewritten wholesale each run, never hand-edited
data/meta.json    curated; deliberately near-empty (no hand-entered figure exists here)
data/recessions.json  vendored from econ-core; never edited here
api/server.py     updater, both computed engines, and read-only status API
api/econcore.py   vendored, stamped copy of the shared fetchers
```

## The series

Twenty series on the econ-core contract. The survey side: SLOOS C&I standards for
large/mid and small firms (quarterly since 1990-Q2), loan demand (1991-Q4), and the
consumer-willingness balance (1982-Q2, the deepest SLOOS record on FRED, sign inverted
and stated). The volume side: H.8 C&I and real estate loans monthly since 1947, G.19
consumer credit since 1943, weekly bank credit since 1973, with year-over-year variants
computed here. The outcome side: four delinquency rates and charge-offs, quarterly from
1985/1991. Canada: the Bank of Canada SLOS business balance (1999-Q2, live, lenders) and
the BOS credit-conditions balance (2000-Q3, live, borrowers), both keyless via Valet.

## Two computed tables

**The SLOOS table, scored.** Episodes of net tightening at or above +20 sustained two
quarters, dated at the easing trough (loosest reading in the prior two years) and the
alarm. On current data the survey era is clean: every recession since 1990 sits in the
table, none missed; the alarm led 2 of 4 (2001 by 11 months, 1990 by 3) and trailed the
two shock recessions (2008, 2020). The 1990 row is left-censored because the survey was
born mid-tightening, and late 1998 does not qualify under the printed two-quarter gate,
which is exactly the kind of absence a printed rule makes informative. The 2022-23
episode closed with no recession.

**The contraction table, deliberately unscored.** Three or more consecutive months of
negative year-over-year C&I loan growth, attached to the nearest NBER peak within two
years. The finding, computed: all 8 attachable contractions since 1948 began AFTER the
recession did, median lag +8 months. Falling loan books are a symptom, not a warning.
The two unattached rows (2023-24 and 2025) are C&I books shrinking with no recession
attached, which the page surfaces rather than smooths.

Neither rule needed tuning against the canonical record; both validated on the first
computed pass and ship in the payload, printed beside their tables.

## The updater

```bash
docker exec lending-updater python /app/server.py --once      # dry run
docker exec lending-updater python /app/server.py --refresh   # what cron runs
```

Host crontab, daily at 07:15 Pacific, log bounded monthly. Family guardrails: stale or
shrunken upstreams kept, failures carry forward with the error recorded, revisions
logged to `changelog.jsonl`. Survey balances never restate; the H.8 aggregates are
benchmarked routinely, so the revision log mostly shows the volume series doing their
normal thing. Fetch policy is econ-core's: keyless first (FRED CSV, Valet), keyed FRED
as fallback (`FRED_API_KEY` in `.env`, gitignored).

## Provenance

Assembled with Claude, made by Anthropic. Survey balances, published aggregates, and
computed history with both rules printed. No forecasts.

## Data and attribution

The MIT licence covers this repository's code. It does not cover the data, which is not
mine: every series belongs to the body that publishes it and carries that body's own terms.
Each series names its `source` and `source_url` so the original is always one click away.

US series are works of the Federal Reserve (H.8, G.19 and the Senior Loan Officer
Opinion Survey), not subject to copyright. Canadian survey data comes from the Bank of
Canada Valet API under its [terms of use](https://www.bankofcanada.ca/terms/).

Recession bands come from econ-core: the US from the NBER chronology via FRED `USREC`,
Canada from the C.D. Howe Institute Business Cycle Council chronology.

Series reached through FRED are redistributed by the Federal Reserve Bank of St. Louis
under [its terms of use](https://fred.stlouisfed.org/legal/), which ask that you cite the
original source and note that it was accessed via FRED.
