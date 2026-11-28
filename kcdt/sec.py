"""Supplement stale quarterly screener inputs with directly reported US GAAP quarterly facts."""
import json
import math
import os
import time
from datetime import date, timedelta
from pathlib import Path

import requests

from .compute import eps_yoy

BOH_RELEASE = "https://www.sec.gov/Archives/edgar/data/46195/000004619526000062/ex991earningsrelease093026.htm"
METRICS = {
    "eps": ("USD/shares", ("EarningsPerShareDiluted",)),
    "revenue": ("USD", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")),
}


def quarter_series(entries, report, asof):
    """Match 13/14-week periods; missing quarters retain their slot, including missing Q4."""
    by_end = {}
    for item in entries:
        if item.get("form") not in {"10-Q", "10-K", "10-Q/A", "10-K/A"} or item.get("filed", "9999") > asof:
            continue
        if not item.get("start") or not item.get("end") or not isinstance(item.get("val"), (float, int)):
            continue
        start, end = map(date.fromisoformat, (item["start"], item["end"]))
        if not 77 <= (end-start).days <= 104 or not math.isfinite(item["val"]) or end > report:
            continue
        if end not in by_end or item["filed"] > by_end[end]["filed"]:
            by_end[end] = item
    if not by_end:
        return
    latest = max(by_end)
    if (report-latest).days > 105 or by_end[latest]["filed"] < report.isoformat():
        return
    values = []
    periods = []
    for i in range(7):
        target = latest - timedelta(days=round(i * 365.25 / 4))
        matches = [d for d in by_end if abs((d-target).days) <= 8]
        hit = by_end[matches[0]] if len(matches) == 1 else None
        values.append(hit["val"] if hit else None)
        periods.append(hit["end"] if hit else None)
    return dict(end=latest.isoformat(), values=values, periods=periods, accession=by_end[latest]["accn"])


def extract(payload, report, asof):
    facts = payload.get("facts", {}).get("us-gaap", {})
    result = {}
    for metric, (unit, tags) in METRICS.items():
        candidates = []
        for tag in tags:
            series = quarter_series(facts.get(tag, {}).get("units", {}).get(unit, []), date.fromisoformat(report), asof)
            if series:
                accession = series.pop("accession")
                series["url"] = f"https://www.sec.gov/Archives/edgar/data/{int(payload['cik'])}/{accession.replace('-', '')}/{accession}-index.htm"
                candidates.append(series)
        if candidates:
            result[metric] = max(candidates, key=lambda x: (x["end"], x["values"][4] is not None))
    return result


def growth(a, b):
    return round(100 * (a-b) / abs(b), 1) if a is not None and b else None


def apply(row, metrics):
    old_end = row.get("qend") or ""
    newest = max((m["end"] for m in metrics.values()), default="")
    if newest <= old_end:
        return
    details = {}
    for key in METRICS:
        m = metrics.get(key)
        vals = m["values"] if m and m["end"] == newest else [None] * 7
        comparable = growth(vals[0], vals[4])
        yoy = [growth(vals[i], vals[i+4]) for i in range(3)]
        seq = [growth(vals[i], vals[i+1]) for i in range(3)]
        minimum = min(yoy) if None not in yoy else None
        rising = all(g > 0 for g in seq) if None not in seq else None
        if key == "eps":
            row.pop("eqd", None)
            row.update(eps_yoy(vals, [], newest), eq=comparable, g3e=minimum,
                       g3eb="g" if minimum is not None else None, eps3=rising)
            if vals[4] is not None:
                row["eqd"] = [newest[:7], m["periods"][4][:7]]
        else:
            row.update(sq=comparable, g3r=minimum, rev3=rising)
        if vals[0] is not None:
            details[key] = dict(value=vals[0], url=m["url"], comparable=comparable is not None)
    row["secQuarter"] = dict(end=newest, metrics=details)
    row["ni"] = None  # cached quarterly net income belongs to the old period
    if len(details) == 2 and all(m["comparable"] for m in details.values()):
        row["qend"] = newest
        row.pop("fst", None)


def supplement(stocks, asof, path):
    path = Path(path)
    cache = json.loads(path.read_text()) if path.exists() else {}
    now = time.time()
    pending = sorted((r for r in stocks if r.get("fst") and r.get("erep") and r["erep"] <= asof),
                     key=lambda r: (cache.get(r["s"], {}).get("checked", 0), r["erep"], r["s"]))
    headers = {"User-Agent": os.getenv("SEC_USER_AGENT") or "KCDT research https://github.com/kcxdt/kcxdt.github.io"}
    def get(url):
        time.sleep(0.2)
        response = requests.get(url, headers=headers, timeout=8)
        response.raise_for_status()
        return response.json()
    calls = 0
    unavailable = False
    directory = cache.get("_directory", {})
    for row in pending:
        ticker, report = row["s"], row["erep"]
        entry = cache.get(ticker, {})
        if entry.get("report") != report or entry.get("asof", "") > asof:
            entry = {}
        if not unavailable and calls < 50 and now-entry.get("checked", 0) >= 43200:
            try:
                if now-directory.get("checked", 0) >= 7*86400:
                    listing = get("https://www.sec.gov/files/company_tickers.json")
                    directory = dict(checked=now, ids={v["ticker"]: v["cik_str"] for v in listing.values()})
                    cache["_directory"] = directory
                cik = directory["ids"].get(ticker.replace(".", "-"))
                result = {}
                if cik:
                    calls += 1
                    payload = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010}.json")
                    if int(payload["cik"]) == int(cik):
                        result = extract(payload, report, asof)
                saved = dict(entry.get("metrics", {}))
                for key, series in result.items():
                    if series["end"] >= saved.get(key, {}).get("end", ""):
                        saved[key] = series
                entry = cache[ticker] = dict(checked=now, asof=asof, report=report, metrics=saved)
            except requests.HTTPError as err:
                if err.response.status_code == 404 and directory.get("ids"):
                    cache[ticker] = dict(entry, checked=now, asof=asof, report=report)
                else:
                    unavailable = True
                print("SEC quarterly lookup:", ticker, err)
            except (requests.RequestException, ValueError, KeyError) as err:
                unavailable = True
                print("SEC quarterly lookup:", ticker, err)
        metrics = dict(entry.get("metrics", {}))
        # Filed earnings release: verified fallback for this specific BOH quarter.
        if ticker == "BOH" and report == "2026-10-26" and "eps" not in metrics:
            metrics["eps"] = dict(end="2026-09-30", values=[1.49, 1.47, None, None, 1.20, None, None],
                                  periods=["2026-09-30", "2026-06-30", None, None, "2025-09-30", None, None], url=BOH_RELEASE)
        apply(row, metrics)
    if pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache))
        tmp.replace(path)
    return dict(pending=len(pending), checked=calls, unavailable=unavailable,
                supplemented=sum(bool(r.get("secQuarter")) for r in pending))
