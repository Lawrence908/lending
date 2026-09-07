#!/usr/bin/env python3
"""lending.chrislawrence.ca data updater and read-only status API.

One narrow question: are banks tightening, and what has followed when they
did? Standards lead the credit cycle; lending volumes trail it. This serves
both ends of that pipeline and the two computed tables that keep the claims
honest: a scored table of SLOOS tightening episodes, and an unscored lag
table of C&I loan contractions whose whole point is that the lag is positive.

Everything live on the page comes from series.json, machine-owned and
rewritten wholesale each run. data/meta.json and the vendored recessions.json
are never touched by automation. No curated figure, no hand ritual.

Guardrails, inherited from jobs: stale or shrunken upstreams are kept rather
than written, a failed fetch carries the previous series forward and records
the error, and revisions to already-published observations land in
changelog.jsonl. SLOOS balances are never restated; the H.8 loan aggregates
are benchmarked routinely, so most revision entries here are the volumes
series doing their normal thing.

HTTP here is read-only. Runs happen via host cron calling
`docker exec lending-updater python /app/server.py --refresh`.
"""

import json
import os
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import econcore

FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")

SERIES_FILE = os.path.join(DATA_DIR, "series.json")
CHANGELOG = os.path.join(DATA_DIR, "changelog.jsonl")
STATE_FILE = os.path.join(DATA_DIR, "updater-state.json")
RECESSIONS_FILE = os.path.join(DATA_DIR, "recessions.json")

CURATED = ["meta", "recessions"]
SHRINK_TOLERANCE = 0.9
CHANGELOG_IN_PAYLOAD = 100

# The tightening rule. Ships in the payload so the page states the rule that
# produced the table; a different rule gives a different table. Frozen after
# one tuning pass against the canonical record (see BUILD-PLAN.md).
EPISODE_RULE = {
    "series": "us_std_ci_large",
    "basis": "quarterly net percentage of banks tightening C&I standards",
    "threshold_net_pct": 20.0,
    "sustain_quarters": 2,
    "merge_gap_months": 9,
    "trough_lookback_months": 24,
    "window_before_months": 6,
    "window_after_months": 15,
    "statement": ("A tightening episode is a stretch of quarters with the "
                  "net percentage of banks tightening C&I standards at or "
                  "above +20, at least two quarters in a row; stretches "
                  "separated by fewer than nine clear months merge into one. "
                  "Each episode is dated two ways: the EASING TROUGH, the "
                  "loosest reading in the two years before the alarm (peak "
                  "complacency), and the ALARM, the first quarter past the "
                  "threshold. An NBER peak from six months before the alarm "
                  "to fifteen months after the last signal quarter is "
                  "assigned to the nearest episode; lead time runs from the "
                  "alarm, because unlike spreads, standards themselves are "
                  "the early clock. A trough window reaching before the "
                  "survey's 1990 start renders as left-censored."),
}

CONTRACTION_RULE = {
    "series": "us_ci_loans",
    "basis": "year-over-year percent change, monthly",
    "sustain_months": 3,
    "merge_gap_months": 6,
    "attach_radius_months": 24,
    "statement": ("A contraction is three or more consecutive months of "
                  "negative year-over-year C&I loan growth; stretches "
                  "separated by fewer than six clear months merge. Each is "
                  "attached to the nearest NBER peak within two years, and "
                  "the lag is measured from that peak: positive means the "
                  "contraction began after the recession did. This table is "
                  "deliberately not a predictor; its point is the sign of "
                  "the lag."),
}

_payload_cache = {"stamp": None, "body": None}
_state = {"last_run": None, "results": []}
_lock = threading.Lock()


# --------------------------------------------------------------------------
# the series list
# --------------------------------------------------------------------------

def _fred(series_id):
    return lambda: econcore.fred_series(series_id, FRED_KEY)


def _valet(series_name):
    return lambda: econcore.valet_series(series_name)


SLOS_URL = "https://www.bankofcanada.ca/publications/slos/"
BOS_URL = "https://www.bankofcanada.ca/publications/bos/"

FETCHED = [
    {
        "id": "us_std_ci_large",
        "fetch": _fred("DRTSCILM"),
        "label": "US C&I lending standards, large and mid-size firms",
        "source": "Fed Senior Loan Officer Opinion Survey, via FRED DRTSCILM",
        "source_url": "https://fred.stlouisfed.org/series/DRTSCILM",
        "units": "net_percent_tightening", "freq": "quarterly",
        "note": "Net percentage of domestic banks tightening standards for commercial and industrial loans, quarterly since 1990-Q2. Positive means more banks tightened than eased. The survey existed earlier; this series does not, and the 1990 episode in the table is left-censored for exactly that reason.",
    },
    {
        "id": "us_std_ci_small",
        "fetch": _fred("DRTSCIS"),
        "label": "US C&I lending standards, small firms",
        "source": "Fed SLOOS, via FRED DRTSCIS",
        "source_url": "https://fred.stlouisfed.org/series/DRTSCIS",
        "units": "net_percent_tightening", "freq": "quarterly",
        "note": "Same question for small firms, quarterly since 1990-Q2.",
    },
    {
        "id": "us_willingness_consumer",
        "fetch": _fred("DRIWCIL"),
        "label": "US willingness to make consumer loans",
        "source": "Fed SLOOS, via FRED DRIWCIL",
        "source_url": "https://fred.stlouisfed.org/series/DRIWCIL",
        "units": "net_percent_more_willing", "freq": "quarterly",
        "note": "SIGN RUNS THE OTHER WAY: positive means MORE willing to lend. Quarterly since 1982-Q2, the deepest SLOOS record on FRED, eight years older than the standards series.",
    },
    {
        "id": "us_demand_ci_large",
        "fetch": _fred("DRSDCILM"),
        "label": "US C&I loan demand, large and mid-size firms",
        "source": "Fed SLOOS, via FRED DRSDCILM",
        "source_url": "https://fred.stlouisfed.org/series/DRSDCILM",
        "units": "net_percent_stronger", "freq": "quarterly",
        "note": "Net percentage reporting stronger loan demand, quarterly since 1991-Q4. The other blade of the loan market's scissors.",
    },
    {
        "id": "us_ci_loans",
        "fetch": _fred("BUSLOANS"),
        "label": "US C&I loans outstanding",
        "source": "Fed H.8, all commercial banks, via FRED BUSLOANS",
        "source_url": "https://fred.stlouisfed.org/series/BUSLOANS",
        "units": "USD_billions", "freq": "monthly",
        "note": "Monthly since January 1947. The contraction lag table computes from this series. Benchmarked routinely, so the revision log will show it.",
    },
    {
        "id": "us_re_loans",
        "fetch": _fred("REALLN"),
        "label": "US real estate loans outstanding",
        "source": "Fed H.8, all commercial banks, via FRED REALLN",
        "source_url": "https://fred.stlouisfed.org/series/REALLN",
        "units": "USD_billions", "freq": "monthly",
        "note": "Monthly since January 1947.",
    },
    {
        "id": "us_consumer_credit",
        "fetch": _fred("TOTALSL"),
        "label": "US consumer credit outstanding",
        "source": "Fed G.19, via FRED TOTALSL",
        "source_url": "https://fred.stlouisfed.org/series/TOTALSL",
        "units": "USD_millions", "freq": "monthly",
        "note": "Monthly since January 1943, all holders, not just banks.",
    },
    {
        "id": "us_bank_credit_weekly",
        "fetch": _fred("TOTBKCR"),
        "label": "US bank credit, weekly",
        "source": "Fed H.8, all commercial banks, via FRED TOTBKCR",
        "source_url": "https://fred.stlouisfed.org/series/TOTBKCR",
        "units": "USD_billions", "freq": "weekly",
        "note": "Weekly since January 1973. In the payload for anyone who wants the high-frequency view; the charts use the deeper monthlies.",
    },
    {
        "id": "us_delinq_all",
        "fetch": _fred("DRALACBS"),
        "label": "US delinquency rate, all loans",
        "source": "Fed, all commercial banks, via FRED DRALACBS",
        "source_url": "https://fred.stlouisfed.org/series/DRALACBS",
        "units": "percent", "freq": "quarterly",
        "note": "Quarterly since 1985. The outcome side of lending; standards move first, delinquencies confirm later.",
    },
    {
        "id": "us_delinq_business",
        "fetch": _fred("DRBLACBS"),
        "label": "US delinquency rate, business loans",
        "source": "Fed, via FRED DRBLACBS",
        "source_url": "https://fred.stlouisfed.org/series/DRBLACBS",
        "units": "percent", "freq": "quarterly",
        "note": "Quarterly since 1987.",
    },
    {
        "id": "us_delinq_cards",
        "fetch": _fred("DRCCLACBS"),
        "label": "US delinquency rate, credit cards",
        "source": "Fed, via FRED DRCCLACBS",
        "source_url": "https://fred.stlouisfed.org/series/DRCCLACBS",
        "units": "percent", "freq": "quarterly",
        "note": "Quarterly since 1991.",
    },
    {
        "id": "us_delinq_mortgages",
        "fetch": _fred("DRSFRMACBS"),
        "label": "US delinquency rate, single-family mortgages",
        "source": "Fed, via FRED DRSFRMACBS",
        "source_url": "https://fred.stlouisfed.org/series/DRSFRMACBS",
        "units": "percent", "freq": "quarterly",
        "note": "Quarterly since 1991.",
    },
    {
        "id": "us_chargeoffs_all",
        "fetch": _fred("CORALACBS"),
        "label": "US charge-off rate, all loans",
        "source": "Fed, via FRED CORALACBS",
        "source_url": "https://fred.stlouisfed.org/series/CORALACBS",
        "units": "percent", "freq": "quarterly",
        "note": "Quarterly since 1985, annualized, seasonally adjusted. In the payload; the delinquency chart carries the outcome story.",
    },
    {
        "id": "ca_slos_business",
        "fetch": _valet("SLOS_BUS_LEND"),
        "label": "Canada business lending conditions (SLOS)",
        "source": "Bank of Canada Senior Loan Officer Survey, Valet series SLOS_BUS_LEND",
        "source_url": SLOS_URL,
        "units": "balance_of_opinion", "freq": "quarterly",
        "note": "Balance of opinion, positive means tightening, quarterly since 1999-Q2. The lenders' answer to the same question SLOOS asks in the US.",
    },
    {
        "id": "ca_slos_business_price",
        "fetch": _valet("SLOS_BUS_LEND_PC"),
        "label": "Canada business lending conditions, price",
        "source": "Bank of Canada SLOS, Valet series SLOS_BUS_LEND_PC",
        "source_url": SLOS_URL,
        "units": "balance_of_opinion", "freq": "quarterly",
        "note": "The price (spread) component, quarterly since 1999-Q2.",
    },
    {
        "id": "ca_slos_business_nonprice",
        "fetch": _valet("SLOS_BUS_LEND_NP"),
        "label": "Canada business lending conditions, non-price",
        "source": "Bank of Canada SLOS, Valet series SLOS_BUS_LEND_NP",
        "source_url": SLOS_URL,
        "units": "balance_of_opinion", "freq": "quarterly",
        "note": "The non-price (terms and availability) component.",
    },
    {
        "id": "ca_bos_credit",
        "fetch": _valet("CREDIT"),
        "label": "Canada credit conditions, firms' view (BOS)",
        "source": "Bank of Canada Business Outlook Survey, Valet series CREDIT",
        "source_url": BOS_URL,
        "units": "balance_of_opinion", "freq": "quarterly",
        "note": "Firms reporting credit conditions tightened minus eased, quarterly since 2000-Q3. The borrowers' side of the Canadian card; SLOS is the lenders' side.",
    },
]


# --------------------------------------------------------------------------
# derived series
# --------------------------------------------------------------------------

def build_derived(series):
    """Year-over-year growth of the volume aggregates, computed here with the
    construction stated; inputs ship in the same payload."""
    out = {}
    for src_id, new_id, label in [
        ("us_ci_loans", "us_ci_loans_yoy", "US C&I loan growth, year over year"),
        ("us_re_loans", "us_re_loans_yoy", "US real estate loan growth, year over year"),
        ("us_consumer_credit", "us_consumer_credit_yoy", "US consumer credit growth, year over year"),
    ]:
        src = series.get(src_id)
        if not src:
            continue
        out[new_id] = econcore.make_series(
            new_id, label,
            "Derived: 12-month percent change of " + src["source"],
            src["source_url"], "percent", "monthly",
            [[d, round(v, 2)] for d, v in
             econcore.yoy_percent(src["obs"], 12)],
            confidence="estimate",
            note="Computed here from the level series in this payload.")
    return out


# --------------------------------------------------------------------------
# analysis: status, the SLOOS table, the contraction lag table
# --------------------------------------------------------------------------

def _mi(year_month):
    year, month = year_month.split("-")[:2]
    return int(year) * 12 + int(month) - 1


def build_status(series):
    status = {}
    std = series.get("us_std_ci_large")
    if std and len(std["obs"]) > 9:
        months = [[d[:7], v] for d, v in std["obs"]]
        window = months[-9:-1]
        low = min(window, key=lambda m: m[1])
        status["us_std_ci_large"] = {
            "latest": [std["obs"][-1][0], std["obs"][-1][1]],
            "low_8q": [low[0], low[1]],
        }
        status["signal_active"] = std["obs"][-1][1] >= EPISODE_RULE["threshold_net_pct"]
    for sid in ("us_demand_ci_large", "us_willingness_consumer",
                "us_delinq_all", "ca_slos_business"):
        entry = series.get(sid)
        if entry:
            status[sid] = {"latest": [entry["obs"][-1][0], entry["obs"][-1][1]]}
    return status


def _runs_and_groups(flags_idx, months, merge_gap):
    runs = [[flags_idx[0], flags_idx[0]]]
    for i in flags_idx[1:]:
        if i == runs[-1][1] + 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    groups = [runs[0][:]]
    for start_i, end_i in runs[1:]:
        gap = _mi(months[start_i][0]) - _mi(months[groups[-1][1]][0]) - 1
        if gap < merge_gap:
            groups[-1][1] = end_i
        else:
            groups.append([start_i, end_i])
    return groups


def build_episodes(std_entry, recessions):
    """The SLOOS table: the family's two clocks with the trough flipped to
    an easing trough, plus a left-censor flag where the trough window reaches
    before the survey exists. Lead time runs from the ALARM here, unlike the
    spread and housing tables, because standards are themselves the early
    clock; the trough is context, not the lead measure."""
    months = [[d[:7], v] for d, v in std_entry["obs"]]
    level = dict(months)
    first_mi = _mi(months[0][0])
    threshold = EPISODE_RULE["threshold_net_pct"]
    qualifying = [i for i, (_, v) in enumerate(months) if v >= threshold]
    if not qualifying:
        return {"rule": EPISODE_RULE, "episodes": [], "stats": {}}

    groups = _runs_and_groups(qualifying, months, EPISODE_RULE["merge_gap_months"])

    def longest_run(lo, hi):
        best = run = 0
        for i in range(lo, hi + 1):
            run = run + 1 if months[i][1] >= threshold else 0
            best = max(best, run)
        return best

    groups = [g for g in groups
              if longest_run(g[0], g[1]) >= EPISODE_RULE["sustain_quarters"]]

    shells = []
    for lo, hi in groups:
        span = months[lo:hi + 1]
        start, end = span[0][0], span[-1][0]
        look_lo = _mi(start) - EPISODE_RULE["trough_lookback_months"]
        before = [m for m in months
                  if look_lo <= _mi(m[0]) < _mi(start)]
        trough = min(before, key=lambda m: m[1]) if before else None
        after = [m for m in months
                 if _mi(start) <= _mi(m[0]) <= _mi(end) + 6]
        peak_row = max(after, key=lambda m: m[1])
        shells.append({
            "start": start, "end": end, "span": span,
            "trough": trough, "peak_row": peak_row,
            "left_censored": look_lo < first_mi,
            "window_lo": _mi(start) - EPISODE_RULE["window_before_months"],
            "window_hi": _mi(end) + EPISODE_RULE["window_after_months"],
            "peaks": [],
        })

    bands = recessions["us"]["bands"]
    data_through = _mi(recessions["us"]["as_of"][:7])
    assigned = set()
    for band in bands:
        peak = band["peak"]
        candidates = [s for s in shells
                      if s["window_lo"] <= _mi(peak) <= s["window_hi"]]
        if not candidates:
            continue
        best = max(candidates, key=lambda s: _mi(s["start"]))
        best["peaks"].append(peak)
        assigned.add(peak)

    episodes = []
    for s in shells:
        led = [p for p in s["peaks"] if _mi(p) >= _mi(s["start"])]
        if led:
            outcome = "recession"
        elif s["peaks"]:
            outcome = "coincident"
        elif s["window_hi"] > data_through:
            outcome = "pending"
        else:
            outcome = "none_in_window"
        first_peak = s["peaks"][0] if s["peaks"] else None
        episodes.append({
            "start": s["start"],
            "end": s["end"],
            "left_censored": s["left_censored"],
            "trough": ({"month": s["trough"][0], "value": s["trough"][1]}
                       if s["trough"] else None),
            "peak": {"month": s["peak_row"][0], "value": s["peak_row"][1]},
            "quarters_signalling": len([1 for _, v in s["span"]
                                        if v >= threshold]),
            "recessions": s["peaks"],
            "lead_from_alarm_months": (_mi(first_peak) - _mi(s["start"])
                                       if first_peak else None),
            "outcome": outcome,
        })

    leads = sorted(e["lead_from_alarm_months"] for e in episodes
                   if e["recessions"])
    stats = {}
    if leads:
        mid = len(leads) // 2
        median = (leads[mid] if len(leads) % 2
                  else (leads[mid - 1] + leads[mid]) / 2.0)
        stats = {"credited_episodes": len(leads),
                 "median_lead_from_alarm_months": median,
                 "alarm_led": len([e for e in episodes
                                   if e["outcome"] == "recession"]),
                 "coincident": len([e for e in episodes
                                    if e["outcome"] == "coincident"]),
                 "false_positives": len([e for e in episodes
                                         if e["outcome"] == "none_in_window"]),
                 "pending": len([e for e in episodes
                                 if e["outcome"] == "pending"]),
                 "uncredited_recessions": [
                     b["peak"] for b in bands
                     if _mi(b["peak"]) >= first_mi
                     and b["peak"] not in assigned]}
    return {"rule": EPISODE_RULE, "episodes": episodes, "stats": stats}


def build_contractions(loans_entry, recessions):
    """The lag table: C&I loan contractions attached to the nearest recession
    peak. Not a predictor and not scored as one; the sign of the lag is the
    finding."""
    yoy = econcore.yoy_percent(loans_entry["obs"], 12)
    months = [[d[:7], v] for d, v in yoy]
    qualifying = [i for i, (_, v) in enumerate(months) if v < 0]
    if not qualifying:
        return {"rule": CONTRACTION_RULE, "contractions": [], "stats": {}}

    groups = _runs_and_groups(qualifying, months, CONTRACTION_RULE["merge_gap_months"])

    def longest_run(lo, hi):
        best = run = 0
        for i in range(lo, hi + 1):
            run = run + 1 if months[i][1] < 0 else 0
            best = max(best, run)
        return best

    groups = [g for g in groups
              if longest_run(g[0], g[1]) >= CONTRACTION_RULE["sustain_months"]]

    bands = recessions["us"]["bands"]
    radius = CONTRACTION_RULE["attach_radius_months"]
    contractions = []
    for lo, hi in groups:
        span = months[lo:hi + 1]
        start, end = span[0][0], span[-1][0]
        deepest = min(span, key=lambda m: m[1])
        nearest_peak, nearest_gap = None, None
        for band in bands:
            gap = _mi(start) - _mi(band["peak"])
            if abs(gap) <= radius and (nearest_gap is None
                                       or abs(gap) < abs(nearest_gap)):
                nearest_peak, nearest_gap = band["peak"], gap
        contractions.append({
            "start": start,
            "end": end,
            "months": _mi(end) - _mi(start) + 1,
            "deepest": {"month": deepest[0], "value": round(deepest[1], 1)},
            "recession_peak": nearest_peak,
            "lag_months": nearest_gap,
        })

    lags = sorted(c["lag_months"] for c in contractions
                  if c["lag_months"] is not None)
    stats = {}
    if lags:
        mid = len(lags) // 2
        median = (lags[mid] if len(lags) % 2
                  else (lags[mid - 1] + lags[mid]) / 2.0)
        stats = {"attached": len(lags),
                 "unattached": len(contractions) - len(lags),
                 "median_lag_months": median,
                 "began_after_recession": len([l for l in lags if l > 0])}
    return {"rule": CONTRACTION_RULE, "contractions": contractions,
            "stats": stats}


def build_analysis(series):
    analysis = {"status": build_status(series)}
    try:
        recessions = econcore.load_recessions(RECESSIONS_FILE)
    except Exception as exc:  # noqa: BLE001 - both tables degrade, page renders
        analysis["episodes_error"] = "%s: %s" % (type(exc).__name__, exc)
        return analysis
    std = series.get("us_std_ci_large")
    if std:
        try:
            analysis["episodes"] = build_episodes(std, recessions)
        except Exception as exc:  # noqa: BLE001
            analysis["episodes_error"] = "%s: %s" % (type(exc).__name__, exc)
    loans = series.get("us_ci_loans")
    if loans:
        try:
            analysis["contractions"] = build_contractions(loans, recessions)
        except Exception as exc:  # noqa: BLE001
            analysis["contractions_error"] = "%s: %s" % (type(exc).__name__, exc)
    return analysis


# --------------------------------------------------------------------------
# refresh
# --------------------------------------------------------------------------

def load_old_series():
    try:
        with open(SERIES_FILE) as fh:
            return json.load(fh).get("series", {})
    except Exception:  # noqa: BLE001 - first run, or corrupt file: start clean
        return {}


def _diff_revisions(series_id, old_obs, new_obs):
    old_map = dict(map(tuple, old_obs))
    changed = [(d, old_map[d], v) for d, v in new_obs
               if d in old_map and abs(old_map[d] - v) > 1e-9]
    if not changed:
        return None
    deltas = [abs(after - before) for _, before, after in changed]
    return {
        "series": series_id, "action": "revised",
        "changed": len(changed),
        "span": [changed[0][0], changed[-1][0]],
        "max_delta": round(max(deltas), 4),
        "sample": [{"date": d, "before": b, "after": a}
                   for d, b, a in changed[:3]],
    }


def refresh_series(dry=False):
    old = load_old_series()
    series, errors, results = {}, {}, []

    for spec in FETCHED:
        sid = spec["id"]
        prev = old.get(sid)
        rec = {"series": sid, "action": "fetched"}
        try:
            obs = spec["fetch"]()
            doc = econcore.make_series(
                sid, spec["label"], spec["source"], spec["source_url"],
                spec["units"], spec["freq"], obs, note=spec.get("note"))
            if prev and prev.get("obs"):
                if doc["as_of"] < prev["as_of"]:
                    rec.update(action="stale-upstream",
                               reason="upstream at %s, behind stored %s; kept"
                                      % (doc["as_of"], prev["as_of"]))
                    doc = prev
                elif len(obs) < len(prev["obs"]) * SHRINK_TOLERANCE:
                    rec.update(action="shrunk",
                               reason="%d obs against %d stored; kept"
                                      % (len(obs), len(prev["obs"])))
                    doc = prev
                else:
                    revision = _diff_revisions(sid, prev["obs"], obs)
                    if revision and prev.get("source") == doc.get("source"):
                        if not dry:
                            econcore.log_revision(CHANGELOG, revision)
                        rec.update(action="revised",
                                   changed=revision["changed"])
                    added = len(obs) - len(prev["obs"])
                    if added > 0:
                        rec["added"] = added
            series[sid] = doc
        except Exception as exc:  # noqa: BLE001 - one dead endpoint, one chart
            errors[sid] = "%s: %s" % (type(exc).__name__, exc)
            rec.update(action="error", reason=errors[sid])
            if prev:
                series[sid] = prev
                rec["carried_forward"] = True
        results.append(rec)
        print("%-26s %-14s %s" % (sid, rec["action"], rec.get("reason", "")),
              flush=True)

    if not series:
        raise ValueError("nothing fetched and nothing stored; refusing to write")

    series.update(build_derived(series))
    analysis = build_analysis(series)

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "note": "Machine-fetched. Never hand-edited; the updater rewrites this file wholesale.",
        "econcore": econcore.VERSION,
        "fred_key_used": bool(FRED_KEY),
        "errors": errors,
        "series": series,
        "analysis": analysis,
    }

    if dry:
        total = sum(len(s["obs"]) for s in series.values())
        print("dry run: %d series, %d observations, %d errors -- not written"
              % (len(series), total, len(errors)), flush=True)
        return payload

    tmp = SERIES_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, SERIES_FILE)

    with _lock:
        _state["last_run"] = datetime.now(timezone.utc).isoformat()
        _state["results"] = results
    _save_state()

    total = sum(len(s["obs"]) for s in series.values())
    print("series refreshed: %d series, %d observations, %d errors"
          % (len(series), total, len(errors)), flush=True)
    return payload


def _save_state():
    try:
        with _lock:
            snapshot = dict(_state)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(snapshot, fh, indent=2)
        os.chmod(tmp, 0o644)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


# --------------------------------------------------------------------------
# read-only HTTP
# --------------------------------------------------------------------------

def _load(name):
    with open(os.path.join(DATA_DIR, name)) as fh:
        return json.load(fh)


def data_stamp():
    newest = 0.0
    names = [n + ".json" for n in CURATED] + ["series.json", "changelog.jsonl"]
    for name in names:
        try:
            newest = max(newest, os.path.getmtime(os.path.join(DATA_DIR, name)))
        except OSError:
            continue
    return newest


def build_data_payload():
    stamp = data_stamp()
    if _payload_cache["stamp"] == stamp and _payload_cache["body"] is not None:
        return _payload_cache["body"]

    payload = {"generated_at": datetime.now(timezone.utc).isoformat()}
    for name in CURATED:
        try:
            payload[name] = _load(name + ".json")
        except Exception as exc:  # noqa: BLE001 - reported, not fatal
            payload[name] = None
            payload.setdefault("errors", {})[name] = str(exc)
    try:
        doc = _load("series.json")
        payload["series"] = doc.get("series", {})
        payload["analysis"] = doc.get("analysis", {})
        payload["series_fetched_at"] = doc.get("fetched_at")
        payload["series_errors"] = doc.get("errors", {})
    except Exception as exc:  # noqa: BLE001 - charts degrade, page renders
        payload["series"] = {}
        payload["analysis"] = {}
        payload.setdefault("errors", {})["series"] = str(exc)

    recent, total = econcore.read_revisions(CHANGELOG, CHANGELOG_IN_PAYLOAD)
    payload["changelog"] = {"total": total, "recent": recent}

    _payload_cache["stamp"] = stamp
    _payload_cache["body"] = payload
    return payload


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, cache="no-cache"):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/health":
            # Probe the dependency, not the process: no data, not healthy.
            try:
                doc = _load("series.json")
                std = doc.get("series", {}).get("us_std_ci_large", {})
                st = doc.get("analysis", {}).get("status", {})
                self._send(200, {
                    "status": "ok",
                    "series": len(doc.get("series", {})),
                    "latest": std.get("as_of"),
                    "signal_active": st.get("signal_active"),
                    "errors": len(doc.get("errors", {})),
                    "fetched_at": doc.get("fetched_at"),
                })
            except Exception as exc:  # noqa: BLE001 - absent data IS the unhealthy case
                self._send(503, {"status": "no data", "error": str(exc)})
        elif path == "/api/data":
            self._send(200, build_data_payload(),
                       cache="public, max-age=300, must-revalidate")
        elif path == "/api/status":
            with _lock:
                snapshot = dict(_state)
            snapshot["fred_key"] = bool(FRED_KEY)
            snapshot["econcore"] = econcore.VERSION
            self._send(200, snapshot)
        elif path == "/api/changelog":
            recent, total = econcore.read_revisions(CHANGELOG, CHANGELOG_IN_PAYLOAD)
            self._send(200, {"total": total, "recent": recent})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        return


def main():
    if "--refresh" in sys.argv:
        refresh_series()
        return
    if "--once" in sys.argv:
        refresh_series(dry=True)
        return

    print("updater starting: fred_key=%s (schedule: host cron)"
          % bool(FRED_KEY), flush=True)

    def warm():
        try:
            refresh_series()
        except Exception as exc:  # noqa: BLE001 - server must come up regardless
            print("initial fetch failed: %s" % exc, flush=True)

    threading.Thread(target=warm, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()


if __name__ == "__main__":
    main()
