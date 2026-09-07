# lending.chrislawrence.ca — build plan

One narrow question: **are banks tightening, and what has followed when they did?**
Lending standards are the earliest of the credit clocks: loan officers tighten before
spreads blow out and usually before recessions arrive, while lending VOLUMES trail the
cycle by quarters. This site shows both ends of that pipeline and scores the leading one.

Site seven of the family, fifth econ-core consumer. Written 2026-09-07; every series
probed live from daedalus that day through econcore's fetchers.

## Verified sources

### United States (FRED, keyless, all probed)

| Series | What | Depth | Freq | Latest |
|---|---|---|---|---|
| `DRTSCILM` | SLOOS net % tightening C&I standards, large/mid | 1990-Q2 → | quarterly | 0.0 |
| `DRTSCIS` | Same, small firms | 1990-Q2 → | quarterly | +1.8 |
| `DRIWCIL` | SLOOS willingness to make consumer loans | **1982-Q2 →** | quarterly | -2.0 |
| `DRSDCILM` | SLOOS net % reporting stronger C&I demand | 1991-Q4 → | quarterly | +16.1 |
| `BUSLOANS` | C&I loans, all commercial banks | **1947-01 →** | monthly | $2,899B |
| `REALLN` | Real estate loans, all commercial banks | 1947-01 → | monthly | $5,810B |
| `TOTALSL` | Consumer credit outstanding (G.19) | **1943-01 →** | monthly | $5,167B |
| `TOTBKCR` | Bank credit, all banks (H.8) | 1973-01 → | weekly | $19,830B |
| `DRALACBS` / `DRBLACBS` | Delinquency: all loans / business | 1985 / 1987 → | quarterly | 1.42 / 1.27 |
| `DRCCLACBS` / `DRSFRMACBS` | Delinquency: cards / mortgages | 1991 → | quarterly | 2.85 / 1.86 |
| `CORALACBS` | Charge-off rate, all loans | 1985-Q1 → | quarterly | 0.55 |

### Canada (BoC Valet, keyless, all probed live)

| Series | What | Depth | Latest |
|---|---|---|---|
| `SLOS_BUS_LEND` | Senior Loan Officer Survey, overall business lending conditions, balance of opinion (positive = tightening) | 1999-Q2 → live | -1.04 |
| `SLOS_BUS_LEND_PC` / `_NP` | Price / non-price splits | 1999-Q2 → | -3.11 / +1.04 |
| `CREDIT` (group BOS_CHART_10) | Business Outlook Survey, firms reporting credit conditions tightened minus eased | 2000-Q3 → live | +1.0 |

Findings from the probe:

1. **DRIWCIL reaches 1982**, eight years deeper than the C&I standards series: the
   consumer-willingness question is the longest SLOOS record on FRED. Its sign runs the
   other way (positive = MORE willing), stated wherever it is drawn.
2. **Canada has a real SLOS on Valet** (quarterly balance since 1999, live), and the BOS
   credit-conditions balance gives the same question from the borrowers' side since
   2000. The Canada card becomes lender-said versus borrower-felt, both live, both
   keyless. Household SLOS splits exist only from 2017 and are skipped in v1.
3. The E2 historical credit aggregates on Valet are terminated (1971 → 2020-09, the BoC
   handoff) and cover consumer credit only; considered and skipped as peripheral to the
   standards question. The deep US volume series carry the volumes story instead.
4. Launch posture: standards neutral (0.0 net), demand recovering (+16.1), willingness
   flat, delinquencies near cycle lows. The quiet end of the site's own clock; rendered
   from computed tokens.

## The features: one scored table, one lag table

**Primary, scored: SLOOS tightening episodes** (DRTSCILM, 1990 →). Two clocks in the
family pattern:

- The EASING TROUGH: the most negative net percentage (loosest standards) in the eight
  quarters before the alarm; peak complacency, the lending analogue of credit's spread
  trough.
- The ALARM: net percentage at or above +20 for two consecutive quarters; episodes
  merging within nine clear months; NBER peaks assigned to the nearest episode inside
  [alarm - 6 months, last signal + 15 months]; outcome vocabulary identical to the
  siblings (recession / coincident / none_in_window / pending).
- Expected canonical reading (verify computed, then believe the table): 1990 fires at
  the survey's birth (the series begins mid-tightening; that row renders
  **left-censored**, stated, because the trough clock cannot see before 1990-Q2);
  2000-Q3 leads the 2001 recession, the clearest lead any credit-side indicator gets;
  2007-Q4 roughly coincident with 2007-12; 2020 trails (the recession caused the
  tightening); 2022-23 closes with no recession or stays pending; 1998 appears only if
  it sustains two quarters, else it is absent and the caption says why the LTCM blip
  does not qualify.
- The cross-family sentence, computed: standards are the earliest credit clock (compare
  credit.chrislawrence.ca, where the spread alarm led once in a century), but they still
  sit inside housing's and yield's longer leads. Sequencing across four tables now.

**Secondary, lagging: bank-credit contractions** (BUSLOANS, 1947 →). Not scored as a
predictor, because it is not one; rendered as a compact lag table proving the point:

- A contraction is three or more consecutive months of negative year-over-year C&I loan
  growth; for each: start, end, deepest YoY, and the lag in months from the nearest NBER
  peak within a two-year radius (positive = the contraction began after the recession
  did, which is the expected sign nearly everywhere).
- Known cases the computation should reproduce: mid-1970s, 1991-93, 2002-04, 2009-11,
  the 2021 PPP unwind (a distortion, expected to attach to 2020 or stand unattached and
  say so), and 2023-24. If the median lag is positive, "volumes trail" is computed, not
  asserted.

## Architecture

Clone credit wholesale. nginx front `lending` (host port **8133**, verified free) +
stdlib sidecar `lending-updater`. Vendor econ-core; jobs guardrails; the episode engine
is credit's with the trough sign flipped (min becomes the easing trough) plus a
left-censor flag when the trough window precedes the first observation; a second small
`build_contractions` function for the lag table. Host cron daily 07:15 PT (SLOOS lands
quarterly on Fed schedule, H.8 Fridays, G.19 monthly, delinquencies quarterly; most runs
verify), monthly log truncation. `data/meta.json` near-empty; no curated figure, no hand
ritual. Vintages reserved; SLOOS itself is not revised, H.8 aggregates are, and the
revision card says which is which.

## Page

Same bones as credit (TimeChart with threshold rule, tokens, tiles, chip, hub footer):

1. Header, the question, chip: standards posture (net %, signal or not), demand
   balance. Amber when the rule is signalling.
2. Tiles: C&I standards, consumer willingness (sign explained), C&I loan growth YoY,
   delinquency all loans, and "alarms that led" from the computed stats.
3. Main chart: DRTSCILM + DRTSCIS quarterly 1990 →, zero line, dashed +20 threshold
   line, bands. Demand (DRSDCILM) as a dashed toggle so the supply-demand cross of the
   loan market is one chart.
4. The SLOOS table with its rule printed, the left-censored 1990 row stated, computed
   footnotes (newest row from data; the cross-family sequencing sentence with links).
5. Willingness chart: DRIWCIL 1982 →, sign convention in the caption, bands.
6. Volumes: BUSLOANS, REALLN, TOTALSL as year-over-year lines, monthly from 1948, bands,
   plus the compact contraction lag table beneath.
7. The outcome side: four delinquency rates quarterly 1985/1991 →, charge-offs in the
   payload, caption naming the current lows as the complacency echo.
8. Canada: SLOS business balance (1999 →) and BOS credit conditions (2000 →), lender
   versus borrower, C.D. Howe bands, positive-equals-tightening convention stated.
9. Revisions card (SLOOS never, H.8 routinely; says so), sources card with every
   series, depths, sign conventions, fetch policy, econ-core note, provenance line.

## Deploy checklist (identical to credit's, values changed)

Port 8133; `sites/lending.caddy`; services.yml entry with dashy + kuma blocks;
`cf-access.sh create lending.chrislawrence.ca --policy public` + retry; cron +
truncation; screenshots (mobile fullPage, desktop, SLOOS table) + layout audit +
console check; `ls -l data/`; commit; push private `Lawrence908/lending`.

## Anti-goals

- No hand-maintained derived data; both tables compute or do not ship.
- No pretending the volumes table predicts anything; its whole point is the lag.
- No splicing, no sign-flipping without the convention printed, no filling the
  pre-1990 SLOOS gap from the Fed's non-FRED historical tables in v1 (a possible later
  curated extension, jobs-style, explicitly out of scope now).
- No forecasts, no "credit impulse" model, no emdashes in page copy.

## Acceptance

- All series land with zero errors on a cold start, contract-validated.
- The SLOOS table reproduces the canonical reading above (2001 led, 2020 trailed, 1990
  left-censored) or the discrepancy is investigated until the table is believed; the
  rule is then frozen and printed.
- The contraction lag table shows a positive median lag, computing "volumes trail", or
  the page honestly reports whatever sign it shows.
- Kill `FRED_API_KEY`: everything still refreshes (keyless CSV and Valet are primary).
- Both containers healthy, public 200, Kuma green, screenshots committed, zero console
  errors, no horizontal scroll, repo pushed, no machine-owned files in git.
