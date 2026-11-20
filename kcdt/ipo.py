"""Searchable US listings, independent of screener eligibility."""
import csv
import io
import json
import re
from datetime import timedelta

import pandas as pd
import requests

from .fetch import NASDAQ_UA, _download

DIRECTORY = "https://www.nasdaqtrader.com/dynamic/SymDir/"
CALENDAR = "https://api.nasdaq.com/api/ipo/calendar"
COMMON = re.compile(r"\b(common (stock|shares?)|ordinary shares?|american depositary (shares?|receipts?))\b", re.I)
OTHER = re.compile(r"\b(warrants?|units?|rights|preferred|notes|debentures?|ETF|fund)\b", re.I)


# Nasdaq Trader DTN2026-28 confirms ANT. Bootstrap its date even when the monthly
# IPO calendar lags the symbol directory during the first deployment.
CONFIRMED = {"ANT": ("Anthropic", "2026-11-19", "https://www.nasdaqtrader.com/TraderNews.aspx?id=DTN2026-28")}


def symbol(value):
    value = (value or "").strip().upper().replace(".", "-")
    return value if re.fullmatch(r"[A-Z]{1,6}(?:-[A-Z])?", value) else None


def directory(text, key):
    if "File Creation Time:" not in text or key not in text.partition("\n")[0].split("|"):
        raise ValueError("incomplete listing file")
    out = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="|"):
        name = row.get("Security Name", "")
        ticker = symbol(row.get(key))
        if ticker and row["Test Issue"] == row["ETF"] == "N" and COMMON.search(name) and not OTHER.search(name):
            out[ticker] = name
    if not out:
        raise ValueError("empty listing file")
    return out


def load_listings(cache, today):
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / "ipo-directory.json"
    old = json.loads(path.read_text(encoding="utf8")) if path.exists() else {}
    state = {}
    names = {}
    for file, key in [("nasdaqlisted", "Symbol"), ("otherlisted", "ACT Symbol")]:
        try:
            r = requests.get(DIRECTORY + file + ".txt", headers={**NASDAQ_UA, "Accept": "text/plain"}, timeout=20)
            r.raise_for_status()
            state[file] = directory(r.text, key)
        except (requests.RequestException, ValueError) as exc:
            print(f"IPO directory {file}: {exc}")
            if not old.get(file):
                raise RuntimeError(f"No usable current or cached {file} directory") from exc
            state[file] = old[file]
        names.update(state[file])

    recent = {s: d for s, d in old.get("recent", {}).items() if d >= (today - timedelta(days=400)).isoformat()}
    for ticker, (issuer, day, _) in CONFIRMED.items():
        if issuer in names.get(ticker, "") and today.isoformat() >= day >= (today - timedelta(days=400)).isoformat():
            recent[ticker] = day
    for file in ("nasdaqlisted", "otherlisted"):
        if old.get(file):
            for s in state[file].keys() - old[file].keys():
                recent[s] = today.isoformat()
    upcoming = dict(old.get("upcoming", {}))
    for month in sorted({today.replace(day=1), (today.replace(day=1) - timedelta(days=1)).replace(day=1),
                         (today.replace(day=28) + timedelta(days=4)).replace(day=1)}):
        try:
            r = requests.get(CALENDAR, params={"date": month.strftime("%Y-%m")}, headers=NASDAQ_UA, timeout=15)
            r.raise_for_status()
            data = r.json()["data"]
            for row in (data.get("priced") or {}).get("rows") or []:
                s = symbol(row.get("proposedTickerSymbol"))
                if s in names:
                    recent.setdefault(s, month.isoformat())
            if "upcoming" in data:
                prefix = month.strftime("%Y-%m")
                upcoming = {s: v for s, v in upcoming.items() if not v[1].startswith(prefix)}
                for row in (data["upcoming"] or {}).get("rows") or []:
                    s = symbol(row.get("proposedTickerSymbol"))
                    when = pd.to_datetime(row.get("expectedPriceDate"), errors="coerce")
                    name = row.get("companyName") or ""
                    exchange = (row.get("proposedExchange") or "").upper()
                    if s and name and not OTHER.search(name) and exchange.startswith(("NASDAQ", "NYSE")) and pd.notna(when):
                        upcoming[s] = [name, when.date().isoformat()]
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            print(f"IPO calendar {month}: {exc}")
    upcoming = {s: v for s, v in upcoming.items() if v[1] >= today.isoformat()}
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(dict(state, recent=recent, upcoming=upcoming)), encoding="utf8")
    temp.replace(path)
    return names, recent, upcoming


def searchable(px, stocks, special, sources):
    names, recent, upcoming = sources
    close = px["Close"]
    sessions = close["^GSPC"].dropna().index
    ranked = {row["s"] for row in stocks} | set(special)
    result = {s: {"n": v[0], "expected": v[1]} for s, v in upcoming.items() if s not in ranked}
    extra = sorted((names.keys() & recent.keys()) - set(close.columns) - ranked)
    for start in range(0, len(extra), 60):
        try:
            quotes = _download(extra[start:start + 60], "2y", threads=True)["Close"]
            close = close.join(quotes, how="left")
        except Exception as exc:
            print(f"IPO quotes unavailable: {exc}")
    for s in names.keys() - ranked:
        record = {"n": names[s]}
        confirmed = CONFIRMED.get(s)
        if confirmed and confirmed[0] in names[s]:
            record.update(listed=confirmed[1], source=confirmed[2])
        if s in close:
            series = close[s].reindex(sessions)
            if record.get("listed"):
                series = series.where(series.index >= pd.Timestamp(record["listed"]))
            valid = series.dropna()
            if len(valid):
                record.update(c=round(float(valid.iloc[-1]), 2), day=valid.index[-1].date().isoformat())
                if len(valid) < 253 and valid.index[0] > sessions[0]:
                    record["first"] = valid.index[0].date().isoformat()
                if len(series) >= 2 and pd.notna(series.iloc[-1]) and series.iloc[-2] > 0:
                    record["chg"] = round(float((series.iloc[-1] / series.iloc[-2] - 1) * 100), 2)
                mean = series.tail(50)
                if len(mean) == 50 and mean.notna().all():
                    record["ma50"] = round(float(mean.mean()), 2)
                    record["d50"] = round(float((mean.iloc[-1] / mean.mean() - 1) * 100), 2)
        result[s] = record
    return result
