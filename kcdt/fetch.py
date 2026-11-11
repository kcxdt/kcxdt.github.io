"""Download data: index member lists from Wikipedia, prices and fundamentals from Yahoo Finance."""
import io
import json
import pathlib
import re
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
import yfinance as yf

from .checks import QLAG
from .compute import DOW30, INDICES, SECTORS

WIKI = "https://en.wikipedia.org/wiki/"
LISTS = {  # name -> Wikipedia page
    "ndx": "Nasdaq-100",
    "sp500": "List_of_S%26P_500_companies",
    "sp400": "List_of_S%26P_400_companies",
    "sp600": "List_of_S%26P_600_companies",
}
UA = {"User-Agent": "Mozilla/5.0 KCDT"}
NASDAQ_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/124.0 Safari/537.36", "Accept": "application/json"}


def _pick(cols, *keys):
    for k in keys:
        for name, orig in cols.items():
            if name.startswith(k):
                return orig
    return None


def _members(page):
    seen = []
    for url in (WIKI + page, "https://en.wikipedia.org/api/rest_v1/page/html/" + page):
        r = requests.get(url, headers=UA, timeout=30)
        try:
            tables = pd.read_html(io.StringIO(r.text))
        except ValueError:
            tables = []
        seen.append(f"{url} -> HTTP {r.status_code}, {len(r.text)} bytes, table rows {[len(t) for t in tables]}")
        for t in tables:
            if isinstance(t.columns, pd.MultiIndex):
                t.columns = [" ".join(str(x) for x in c if "Unnamed" not in str(x)) for c in t.columns]
            cols = {str(c).strip().lower(): c for c in t.columns}
            sym = _pick(cols, "symbol", "ticker")
            name = _pick(cols, "security", "company", "name")
            sec = _pick(cols, "gics sector", "sector", "icb industry", "industry")
            if sym is not None and name is not None and len(t) >= 80:
                return pd.DataFrame({
                    "s": t[sym].astype(str).str.strip().str.replace(".", "-", regex=False),
                    "n": t[name].astype(str),
                    "gics": t[sec].astype(str) if sec is not None else None,
                })
            if len(t) >= 80:
                seen.append(f"  columns {list(cols)}")
    raise RuntimeError(f"no member table found on {page}: " + " | ".join(seen))


def _nasdaq100():
    """Nasdaq's own list (Wikipedia's Nasdaq-100 page no longer carries the member table)."""
    r = requests.get("https://api.nasdaq.com/api/quote/list-type/nasdaq100", headers=NASDAQ_UA, timeout=30)
    rows = r.json()["data"]["data"]["rows"]
    return pd.DataFrame({"s": [x["symbol"].strip().replace(".", "-") for x in rows],
                         "n": [x["companyName"] for x in rows], "gics": None})


# Nasdaq screener sectors -> the Yahoo/finviz names used everywhere else
NASDAQ_SECTOR = {
    "Technology": "Technology", "Finance": "Financial Services", "Health Care": "Healthcare",
    "Consumer Discretionary": "Consumer Cyclical", "Consumer Staples": "Consumer Defensive",
    "Industrials": "Industrials", "Real Estate": "Real Estate", "Energy": "Energy", "Utilities": "Utilities",
    "Basic Materials": "Basic Materials", "Telecommunications": "Communication Services",
}
SKIP_NAMES = ("Warrant", "Preferred", " Unit", "Rights", "Notes", "Depositary Shares Each")


def _all_us(min_cap=3e8, min_price=12):
    """Every US-listed common stock from Nasdaq's screener, above KC's cap and price floor."""
    r = requests.get("https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&download=true",
                     headers=NASDAQ_UA, timeout=60)
    rows = r.json()["data"]["rows"]
    num = lambda x: float(str(x).replace("$", "").replace(",", "") or 0)
    keep = [x for x in rows
            if num(x.get("marketCap")) >= min_cap and num(x.get("lastsale")) >= min_price
            and x["symbol"].isalpha() and not any(w in x["name"] for w in SKIP_NAMES)]
    return pd.DataFrame({"s": [x["symbol"].strip() for x in keep], "n": [x["name"].strip() for x in keep],
                         "gics": [NASDAQ_SECTOR.get(x.get("sector")) for x in keep],
                         "us": [x.get("country") == "United States" for x in keep]})


def _list(key, page, cache):
    """Fetch one member list; fall back to the last good copy so one bad source doesn't stop the update."""
    path = cache / f"members-{key}.json"
    tries = ([_nasdaq100, lambda: _members(page)] if key == "ndx" else [_all_us] if key == "usa"
             else [lambda: _members(page)])
    for f in tries:
        try:
            m = f()
            if len(m) >= 80:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(m.to_json(orient="records"))
                return m
        except Exception as e:
            print(f"{key}: source failed: {e}")
    if path.exists():
        print(f"{key}: using cached member list")
        return pd.read_json(path, orient="records")
    raise RuntimeError(f"no member list for {key}")


def universe(cache="cache"):
    """One row per ticker: name, sector, which index lists it belongs to (idx = any of them),
    plus all other US-listed stocks above KC's cap and price floor (usa)."""
    frames = []
    for key, page in {**LISTS, "usa": None}.items():
        try:
            m = _list(key, page, pathlib.Path(cache))
        except RuntimeError:
            if key != "usa":
                raise
            print("usa: no list, using index members only")
            continue
        m[key] = True
        frames.append(m)
        print(f"{key}: {len(m)} members")
    any_ = lambda x: bool(x.fillna(False).any())
    u = pd.concat(frames)
    if "us" not in u:
        u["us"] = None
    # index lists come first, so their names and GICS sectors win over the screener's
    u = u.groupby("s").agg({"n": "first", "gics": "first", "us": "first", **{k: any_ for k in LISTS}})
    for k in LISTS:
        u[k] = u[k].fillna(False).astype(bool)
    u["idx"] = u[list(LISTS)].any(axis=1)
    u["gics"] = u["gics"].fillna("")
    return u


def _download(tickers, period, threads=False):
    df = yf.download(tickers, period=period, auto_adjust=True, progress=False, threads=threads)
    return {f: df[f] for f in ("Open", "High", "Low", "Close", "Volume")}


def prices(tickers, period="2y", chunk=50, pause=3, fast=(100, 1, 4)):
    """Download in batches with pauses; Yahoo rate-limits big bursts from cloud IPs.

    The first pass uses `fast` = (batch size, pause, parallel requests): one request at a time took ~9 minutes for
    2,700 tickers, most of the close update. Whatever that pass misses (or all of it, if Yahoo pushes back) is
    fetched again the slow way, one request at a time in batches of `chunk`."""
    parts = {f: [] for f in ("Open", "High", "Low", "Close", "Volume")}
    todo = list(tickers)
    missing = []
    for attempt in range(4):
        missing = []
        size, wait, threads = fast if attempt == 0 and fast else (chunk, pause, False)
        for i in range(0, len(todo), size):
            part = todo[i:i + size]
            try:
                got_df = _download(part, period, threads)
            except Exception as e:
                print("batch failed:", e)
                missing += part
                time.sleep(pause * 10)
                if attempt == 0 and len(missing) > max(200, len(tickers) // 5):
                    # Yahoo is pushing back on the fast pass: stop it here and do the rest the slow way
                    missing += todo[i + size:]
                    break
                continue
            c = got_df["Close"]
            got = [t for t in part if t in c and not c[t].dropna().empty]
            for f in parts:
                parts[f].append(got_df[f][got])
            missing += [t for t in part if t not in got]
            time.sleep(wait)
        print(f"attempt {attempt + 1}: {len(tickers) - len(missing)}/{len(tickers)} downloaded")
        if len(missing) <= max(3, len(tickers) // 200):  # a few delisted symbols: not worth minutes of retries
            break
        todo = missing
        time.sleep(60 * (attempt + 1) if attempt else 20)
    if len(missing) > len(tickers) * 0.05:
        raise RuntimeError(f"{len(missing)} tickers missing, e.g. {missing[:10]}")
    out = {}
    for f, frames in parts.items():
        df = pd.concat(frames, axis=1)
        out[f] = df.loc[:, ~df.columns.duplicated()]
    return out


# ---- fundamentals (cached; Yahoo allows only a few hundred of these calls per run) ----

INFO_KEYS = {
    "marketCap": "mcap", "beta": "beta", "heldPercentInstitutions": "inst",
    "revenueGrowth": "sq", "earningsQuarterlyGrowth": "eq", "trailingPE": "pe",
    "forwardPE": "fpe", "sector": "ysec", "industry": "ind",
    "firstTradeDateMilliseconds": "ipo", "earningsTimestamp": "edate", "isEarningsDateEstimate": "eest",
}


def _row_values(df, names):
    for n in names:
        if n in df.index:
            return [None if pd.isna(v) else float(v) for v in df.loc[n].tolist()]
    return []


SCHEMA = 2  # bump when _one() starts collecting new fields, so old cache entries refresh first


def _estimate_growth(df):
    """Analyst growth estimates for this and next fiscal year, as fractions."""
    if df is None or df.empty or "growth" not in df:
        return None, None
    g = df["growth"]
    val = lambda k: None if k not in g.index or pd.isna(g[k]) else float(g[k])
    return val("0y"), val("+1y")


def _one(t):
    tk = yf.Ticker(t)
    info = tk.info or {}
    rec = {v: info.get(k) for k, v in INFO_KEYS.items()}
    rec["_v"] = SCHEMA
    q = tk.quarterly_income_stmt
    if q is not None and not q.empty:
        q = q[sorted(q.columns, reverse=True)]  # newest first
        rec["qend"] = pd.Timestamp(q.columns[0]).strftime("%Y-%m-%d")  # end of the latest reported fiscal quarter
        rec["revq"] = _row_values(q, ["Total Revenue", "Operating Revenue"])[:8]
        rec["epsq"] = _row_values(q, ["Diluted EPS", "Basic EPS"])[:8]
    a = tk.income_stmt
    if a is not None and not a.empty:
        a = a[sorted(a.columns, reverse=True)]
        rec["reva"] = _row_values(a, ["Total Revenue", "Operating Revenue"])[:4]
        rec["epsa"] = _row_values(a, ["Diluted EPS", "Basic EPS"])[:4]
    # Reported EPS goes back further here than in the income statement (needed for 3 quarters year on year)
    try:
        e = tk.get_earnings_dates(limit=16)
        if e is not None and not e.empty and "Reported EPS" in e:
            e = e["Reported EPS"].dropna().sort_index(ascending=False)
            rec["epsh"] = [float(x) for x in e.tolist()[:8]]
    except Exception as ex:
        print("earnings dates failed:", t, ex)
    for name, key in (("earnings_estimate", "efc"), ("revenue_estimate", "rfc")):
        try:
            rec[key] = list(_estimate_growth(getattr(tk, name)))
        except Exception as ex:
            print(f"{name} failed:", t, ex)
    return rec


AFTER_REPORT = 20 * 3600  # Yahoo usually has the new quarter by the next day's run


def reported_since(rec, now):
    """True when the company reported after this record was fetched (so its revenue/EPS are a quarter old)."""
    e = rec.get("edate")
    return bool(e) and e + AFTER_REPORT <= now and rec.get("_ts", 0) < e + AFTER_REPORT


def report_times(done):
    """{ticker: "YYYY-MM-DD"} of confirmed reports (Nasdaq's calendar) -> {ticker: unix time of 16:00 New York that
    day}. The time of day isn't known, so take the latest a report can come out (after the close)."""
    return {t: pd.Timestamp(f"{d} 16:00", tz="America/New_York").timestamp() for t, d in (done or {}).items()}


def reported_after_fetch(rec, report, now):
    """True when a confirmed report (unix time from report_times) came after this cached record was fetched."""
    return bool(report) and rec.get("_ts", 0) < report + AFTER_REPORT <= now


RETRY_AFTER = 12 * 3600  # a record still on the quarter before its last report is fetched again after this long
RETRY_DAYS = 45  # ... for up to this many days after the report; then it goes back to the normal 7-day turn


def quarter_behind(rec, report=None):
    """True when the latest quarter in the record ended more than QLAG days before the company's last report
    (Yahoo's own, or Nasdaq's confirmed `report` time): the record is still the quarter before."""
    rep, q = max(rec.get("erep") or 0, report or 0), rec.get("qend")
    return bool(rep and q) and rep - pd.Timestamp(q).timestamp() > QLAG * 86400


def retry_due(rec, report, now):
    """Fetched after the report, but Yahoo did not have the new quarter yet: try again (at most every RETRY_AFTER)."""
    rep = max(rec.get("erep") or 0, report or 0)
    return (quarter_behind(rec, report) and rep + AFTER_REPORT <= now < rep + RETRY_DAYS * 86400
            and now - rec.get("_ts", 0) >= RETRY_AFTER)


def fundamentals(tickers, cache_file, max_age_days=7, max_calls=250, pause=0.6, reports=None, first=None):
    """Refresh stale entries for `tickers`: companies that just reported first, then the ones whose last fetch was
    still the quarter before (retried), then old schema, then oldest.

    reports: {ticker: unix time} from report_times(): Nasdaq's calendar confirms reports that Yahoo's cached
    earnings date missed (it had already moved on, or had none), so those count as just reported too.
    first: among the just-reported, these go first (the screener's pool, ahead of stocks outside it)."""
    path = pathlib.Path(cache_file)
    cache = json.loads(path.read_text()) if path.exists() else {}
    now = time.time()
    reports = reports or {}
    # a record with quarterly EPS but no quarter end date was saved before that date was collected: it counts as an
    # old-schema record, so it is fetched again ahead of the normal 7-day turn (11 Nov 2026: 59 such, CRM, DELL, HPQ...)
    current = lambda r: r.get("_v", 1) >= SCHEMA and not (r.get("epsq") and not r.get("qend"))
    age = lambda t: (current(cache.get(t, {})), cache.get(t, {}).get("_ts", 0))
    just = {t for t in tickers if reported_since(cache.get(t, {}), now)
            or (t in cache and reported_after_fetch(cache[t], reports.get(t), now))}
    retry = {t for t in tickers if t in cache and t not in just and retry_due(cache[t], reports.get(t), now)}
    stale = sorted((t for t in tickers if t in just or t in retry or age(t)[0] is False
                    or now - age(t)[1] > max_age_days * 86400),
                   key=lambda t: (0 if t in just else 1 if t in retry else 2, first is not None and t not in first, age(t)))
    print(f"fundamentals: {len(just)} reported since last fetch, refreshing those first; "
          f"{len(retry)} still on the quarter before their report, trying again")
    done = fails = 0
    for t in stale[:max_calls]:
        try:
            old = cache.get(t, {})
            rec = _one(t)
            rec["_ts"] = now
            # remember the last report date (Yahoo moves earningsTimestamp on to the next report)
            past = old.get("edate") if old.get("edate") and old["edate"] <= now else None
            rec["erep"] = past if past and (not rec.get("edate") or rec["edate"] > now) else old.get("erep")
            if rec.get("edate") and rec["edate"] <= now:
                rec["erep"] = rec["edate"]
            cache[t] = rec
            done += 1
        except Exception as e:
            fails += 1
            print("fundamentals failed:", t, e)
            if fails > 25:
                print("too many failures, stopping fundamentals refresh for this run")
                break
            time.sleep(pause * 10)
        time.sleep(pause)
    print(f"fundamentals: {done} refreshed, {len(stale) - done} still stale, {len(cache)} cached")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache))
    return cache


def profiles(tickers, cache_file, have=(), max_calls=450, max_age_days=180, pause=0.4):
    """Yahoo sector and industry for stocks outside the fundamentals list (`have` already has them).
    One cheap call each; these rarely change, so new tickers first and each kept for half a year."""
    path = pathlib.Path(cache_file)
    cache = json.loads(path.read_text()) if path.exists() else {}
    now = time.time()
    todo = sorted((t for t in tickers if t not in have and now - cache.get(t, {}).get("_ts", 0) > max_age_days * 86400),
                  key=lambda t: cache.get(t, {}).get("_ts", 0))
    done = fails = 0
    for t in todo[:max_calls]:
        try:
            info = yf.Ticker(t).info or {}
            cache[t] = {"ysec": info.get("sector"), "ind": info.get("industry"), "_ts": now}
            done += 1
        except Exception as e:
            fails += 1
            print("profile failed:", t, e)
            if fails > 25:
                print("too many failures, stopping profile refresh for this run")
                break
            time.sleep(pause * 10)
        time.sleep(pause)
    print(f"profiles: {done} fetched, {max(0, len(todo) - done)} still to do, {len(cache)} cached")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache))
    return cache


# ---- quarterly revenue / EPS history from Nasdaq (Yahoo only gives ~5 quarters, too few for 3 quarters YoY) ----

def _money(x):
    """'$44,062(m)' -> 44062e6, '$0.76 (04/27/2025)' -> 0.76, '' -> None."""
    m = re.match(r"\s*(-?)\$?(-?)([\d,]*\.?\d+)\s*(\((m|b|k)\))?", x or "")
    if not m:
        return None
    v = float(m.group(3).replace(",", "")) * {"m": 1e6, "b": 1e9, "k": 1e3}.get(m.group(5), 1)
    return -v if (m.group(1) or m.group(2)) else v


def _nasdaq_quarters(t):
    r = requests.get(f"https://api.nasdaq.com/api/company/{t.replace('-', '.')}/revenue?limit=1", headers=NASDAQ_UA, timeout=30)
    rows = ((r.json().get("data") or {}).get("revenueTable") or {}).get("rows") or []
    cols = ["value2", "value3", "value4"]  # fiscal years, newest first
    quarters, cur = [], None
    for row in rows:
        label = row.get("value1", "")
        if label == "Totals":
            break
        if label == "Revenue" and cur is not None:
            cur["rev"] = [_money(row.get(c)) for c in cols]
        elif label == "EPS" and cur is not None:
            cur["eps"] = [_money(row.get(c)) for c in cols]
        elif label not in ("Revenue", "EPS", "Dividends"):
            cur = {"rev": [None] * 3, "eps": [None] * 3}
            quarters.append(cur)
    # newest first: latest fiscal year's quarters in reverse order, then the year before, ...
    seq = [(q["rev"][i], q["eps"][i]) for i in range(3) for q in reversed(quarters)]
    while seq and seq[0][0] is None:  # quarters of the current year not reported yet
        seq.pop(0)
    out = []
    for rev, eps in seq:  # keep only the unbroken run
        if rev is None:
            break
        out.append((rev, eps))
    return {"nrev": [x[0] for x in out], "neps": [x[1] for x in out]}


def nasdaq_quarters(tickers, cache_file, max_age_days=7, max_calls=400, workers=4, reports=None):
    """reports: {ticker: last report time}; anything that reported after its cached copy is refreshed first."""
    path = pathlib.Path(cache_file)
    cache = json.loads(path.read_text()) if path.exists() else {}
    now = time.time()
    reports = reports or {}
    just = {t for t in tickers if reports.get(t) and cache.get(t, {}).get("_ts", 0) < reports[t] + AFTER_REPORT <= now}
    stale = sorted((t for t in tickers if t in just or now - cache.get(t, {}).get("_ts", 0) > max_age_days * 86400),
                   key=lambda t: (t not in just, cache.get(t, {}).get("_ts", 0)))[:max_calls]

    def one(t):
        try:
            rec = _nasdaq_quarters(t)
            rec["_ts"] = now
            return t, rec
        except Exception as e:
            print("nasdaq quarters failed:", t, e)
            return t, None

    with ThreadPoolExecutor(workers) as ex:
        got = [(t, rec) for t, rec in ex.map(one, stale) if rec is not None]
    cache.update(got)
    print(f"nasdaq quarters: {len(got)}/{len(stale)} refreshed, {len(cache)} cached")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache))
    return cache


# ---- earnings calendar with timing (before the open / after the close) from Nasdaq ----

EARN_TIME = {"time-pre-market": "bmo", "time-after-hours": "amc"}


def _cap_bn(x):
    """'$4,376,559,880,000' -> 4376.56 (US$ bn); blank -> None."""
    try:
        return round(float(str(x).replace("$", "").replace(",", "")) / 1e9, 2)
    except ValueError:
        return None


def _earn_rows(day, tries=3, far=False):
    """One day of Nasdaq's earnings calendar. A failed request is tried again: a day lost here would drop every
    company reporting that day from the calendar (and from 「明日出業績」).
    far: a day many weeks ahead, where an answer with no data just means Nasdaq has nothing listed that far out
    yet, so it isn't asked again (seven such days cost over two minutes of retries per build)."""
    for i in range(tries):
        try:
            r = requests.get(f"https://api.nasdaq.com/api/calendar/earnings?date={day}", headers=NASDAQ_UA, timeout=30)
            r.raise_for_status()
            data = r.json().get("data")
            if data is None:
                if far:
                    return []
                raise ValueError("no data")
            return data.get("rows") or []
        except Exception as e:
            print(f"earnings calendar failed ({i + 1}/{tries}):", day, e)
            time.sleep(3 * (i + 1))
        finally:
            time.sleep(0.5)
    return []


def earnings_calendar(days=63, back=100, cache_file=None, today=None):
    """({ticker: [date, "bmo" | "amc" | None, market cap in US$ bn | None]} for the next `days` calendar days
    (about a quarter ahead, so the next date of most companies is a scheduled one rather than Yahoo's guess),
    {ticker: date} of reports in the last `back` days that came with an actual EPS, i.e. confirmed as published).

    Past days don't change once the numbers are in, so they are kept in `cache_file` ({date: [tickers]}) and only
    the last few days are asked for again; the first run fills a whole quarter."""
    out, done = {}, {}
    # Plain dates, no timezone: a tz-aware range breaks on the day the clocks change (AmbiguousTimeError on 1 Nov)
    start = pd.Timestamp(today or pd.Timestamp.now(tz="America/New_York").date())
    path = pathlib.Path(cache_file) if cache_file else None
    seen = {}
    if path and path.exists():
        try:
            seen = json.loads(path.read_text())
        except Exception:
            seen = {}
    recent = (start - pd.Timedelta(days=4)).strftime("%Y-%m-%d")  # late EPS figures still arriving
    span = [d for d in pd.date_range(start - pd.Timedelta(days=back), periods=back + days) if d.weekday() < 5]
    cached = lambda d: d < start and d.strftime("%Y-%m-%d") in seen and d.strftime("%Y-%m-%d") < recent
    # the days still to ask for, a few at a time (one by one took 5 minutes of a 15-minute build)
    ask = [d for d in span if not cached(d)]
    far = start + pd.Timedelta(days=42)
    with ThreadPoolExecutor(4) as ex:
        fetched = dict(zip(ask, ex.map(lambda d: _earn_rows(d.strftime("%Y-%m-%d"), far=d >= far), ask)))
    for d in span:
        day = d.strftime("%Y-%m-%d")
        if cached(d):
            for sym in seen[day]:
                done[sym] = max(done.get(sym, day), day)
            continue
        got = []
        for row in fetched[d]:
            sym = (row.get("symbol") or "").strip().replace(".", "-")
            if not sym:
                continue
            if d >= start and sym not in out:
                out[sym] = [day, EARN_TIME.get(row.get("time")), _cap_bn(row.get("marketCap"))]
            elif d < start and re.search(r"\d", str(row.get("eps") or "")):  # "$1.23", "($0.12)"; blank until published
                done[sym] = max(done.get(sym, day), day)
                got.append(sym)
        if d < start:
            seen[day] = got
    if path:
        first = (start - pd.Timedelta(days=back + 10)).strftime("%Y-%m-%d")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({k: v for k, v in sorted(seen.items()) if k >= first}))
    print(f"earnings calendar: {len(out)} companies in the next {days} days, {len(done)} reported in the last {back}")
    return out, done


def earnings_dates(tickers, cache_file, known=(), max_calls=300, recheck_days=3, pause=0.4):
    """Yahoo's next report time for stocks with no upcoming date yet: {ticker: {"ts", "est", "_ts"}}.

    Yahoo keeps the last report's time for a while after it, so a fundamentals copy fetched then never shows the
    next date. `known`: tickers that already have an upcoming date (Nasdaq's calendar, or Yahoo in the future).
    One cheap call each; Nasdaq-100 order first (as given), then whatever was checked longest ago."""
    path = pathlib.Path(cache_file)
    cache = json.loads(path.read_text()) if path.exists() else {}
    now = time.time()
    known = set(known)
    # a future date counts as known for two weeks; after that it is checked again in case the company moved it
    has_next = lambda t: (cache.get(t, {}).get("ts") or 0) > now and now - cache[t].get("_ts", 0) < 14 * 86400
    todo = [t for t in tickers if t not in known and not has_next(t)
            and now - cache.get(t, {}).get("_ts", 0) > recheck_days * 86400]
    order = {t: i for i, t in enumerate(tickers)}
    todo.sort(key=lambda t: (cache.get(t, {}).get("_ts", 0) > 0, order[t]))
    done = fails = 0
    for t in todo[:max_calls]:
        try:
            info = yf.Ticker(t).info or {}
            cache[t] = {"ts": info.get("earningsTimestamp") or info.get("earningsTimestampStart"),
                        "est": info.get("isEarningsDateEstimate"), "_ts": now}
            done += 1
        except Exception as e:
            fails += 1
            print("earnings date failed:", t, e)
            if fails > 25:
                print("too many failures, stopping the earnings date refresh for this run")
                break
            time.sleep(pause * 10)
        time.sleep(pause)
    # drop entries nobody asked about for a month (delisted, left the universe)
    keep = set(tickers)
    cache = {t: r for t, r in cache.items() if t in keep or now - r.get("_ts", 0) < 30 * 86400}
    print(f"earnings dates: {done} checked, {max(0, len(todo) - done)} still to check, "
          f"{sum(1 for r in cache.values() if (r.get('ts') or 0) > now)} with a next date")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache))
    return cache


# ---- US economic calendar and market headlines (Nasdaq) ----

def _nasdaq_utc(day, hhmm, now=None):
    """The calendar's "gmt" column is the release time in UTC shown with New York's offset *at the time of asking*,
    for every date alike. Asked in October (summer time), NFP at 08:30 ET on 6 Nov showed as 09:30; asked on
    11 Nov (winter time) the 28 Oct FOMC decision at 14:00 EDT shows as 13:00 and the 10 Nov CPI as 08:30.
    So undo today's offset, not the offset of the release date."""
    if not (hhmm or "").strip():
        return None
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    now = now.tz_localize("UTC") if now.tzinfo is None else now
    off = now.tz_convert("America/New_York").utcoffset()  # -4 h in summer, -5 h in winter
    try:
        return (pd.Timestamp(f"{day} {hhmm.strip()}") - off).strftime("%Y-%m-%dT%H:%MZ")
    except Exception:
        return None


def _econ_day(ask, tries=2):
    """US rows Nasdaq files under date=`ask`, or None when the request failed."""
    for i in range(tries):
        try:
            r = requests.get(f"https://api.nasdaq.com/api/calendar/economicevents?date={ask}", headers=NASDAQ_UA, timeout=30)
            r.raise_for_status()
            rows = ((r.json().get("data") or {}).get("rows")) or []
            return [x for x in rows if x.get("country") == "United States"]
        except Exception as e:
            print(f"economic calendar failed ({i + 1}/{tries}):", ask, e)
            time.sleep(3)
    return None


def economic_calendar(today=None, labels_file=None, days_file=None, prev=None, now=None):
    """US releases for this week and next: [{d, u (UTC time), e (event), a (actual), c (consensus), p (previous)}].

    Nasdaq files each day's releases under the next day's date (the 2026-10-28 FOMC decision comes back for
    date=2026-10-29, the 2026-11-10 CPI for date=2026-11-11), so query the day after and date the rows one day
    earlier. Now and then it answers with another day's rows: on the evening of 10 Nov, date=2026-11-11 gave
    Wednesday's rows instead of Tuesday's CPI, so the site lost CPI day. A day whose rows are the same as the next
    day's, or that comes back empty or failed where it had rows before, keeps its last good copy: `prev` (the
    rows the site shows now), else `days_file` (kept with the builds' cache)."""
    today = pd.Timestamp(today or pd.Timestamp.now(tz="America/New_York").date())
    monday = today - pd.Timedelta(days=today.weekday())
    clean = lambda x: None if (x or "").replace("&nbsp;", "").strip() == "" else x.strip()
    kept = {}
    dpath = pathlib.Path(days_file) if days_file else None
    if dpath and dpath.exists():
        try:
            kept = json.loads(dpath.read_text())
        except Exception:
            kept = {}
    shown = {}  # the rows the site shows now
    for r in prev or []:
        if r.get("d"):
            shown.setdefault(r["d"], []).append({k: r.get(k) for k in ("d", "u", "e", "a", "c", "p")})
    kept = {**kept, **shown}  # the site is never older than the file
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(monday, periods=10)]
    got = {}
    for day in days:
        got[day] = _econ_day((pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
        time.sleep(0.5)
    sig = lambda rows: sorted((x.get("gmt"), x.get("eventName"), x.get("previous")) for x in rows) if rows else None
    out = []
    for i, day in enumerate(days):
        rows = got[day]
        nxt = got[days[i + 1]] if i + 1 < len(days) else None
        why = ("failed" if rows is None else "same rows as the next day" if rows and sig(rows) == sig(nxt)
               else "empty" if not rows and kept.get(day) else None)
        if why:
            print(f"economic calendar: {day} {why}, keeping the last good copy ({len(kept.get(day) or [])} rows)")
            out += [dict(r) for r in kept.get(day) or []]
            continue
        rows = [dict(d=day, u=_nasdaq_utc(day, x.get("gmt"), now), e=clean(x.get("eventName")),
                     a=clean(x.get("actual")), c=clean(x.get("consensus")), p=clean(x.get("previous"))) for x in rows]
        kept[day] = rows
        out += [dict(r) for r in rows]
    if dpath:
        first = (monday - pd.Timedelta(days=21)).strftime("%Y-%m-%d")
        dpath.parent.mkdir(parents=True, exist_ok=True)
        dpath.write_text(json.dumps({k: v for k, v in sorted(kept.items()) if k >= first}))
    from .econ import bls_changes, forexfactory, label
    path = pathlib.Path(labels_file) if labels_file else None
    memory = {}
    if path and path.exists():
        try:
            memory = json.loads(path.read_text())
        except Exception:
            memory = {}
    label(out, forexfactory(), memory, bls_changes)
    if path:
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(memory, ensure_ascii=False, indent=1, sort_keys=True))
    print(f"economic calendar: {len(out)} US releases, {sum(1 for r in out if r.get('ref'))} with a period, "
          f"{sum(1 for r in out if r.get('per'))} with m/m-y/y known, {sum(1 for r in out if r.get('unconf'))} unconfirmed")
    return out


def headlines(feeds=("Markets", "Earnings", "Technology"), n=60):
    """Latest market news from Nasdaq's RSS feeds: [{t (UTC ISO), title, link, src}]."""
    import xml.etree.ElementTree as ET
    from datetime import timezone
    from email.utils import parsedate_to_datetime
    seen, out = set(), []
    for cat in feeds:
        try:
            r = requests.get(f"https://www.nasdaq.com/feed/rssoutbound?category={cat}",
                             headers={"User-Agent": NASDAQ_UA["User-Agent"]}, timeout=30)
            items = ET.fromstring(r.content).iter("item")
        except Exception as e:
            print("headlines failed:", cat, e)
            continue
        for it in items:
            title, link = (it.findtext("title") or "").strip(), (it.findtext("link") or "").strip()
            if not title or link in seen or "transcript" in title.lower():  # call transcripts crowd out the news
                continue
            seen.add(link)
            try:
                when = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                when = None
            src = it.findtext("{http://purl.org/dc/elements/1.1/}creator") or "Nasdaq"
            out.append(dict(t=when, title=title, link=link, src=src.strip(), cat=cat))
    out.sort(key=lambda x: x["t"] or "", reverse=True)
    print(f"headlines: {len(out)}")
    return out[:n]


# ---- CNN Fear & Greed (no official API; keep the last good copy if it fails) ----

def fear_greed(cache_file):
    path = pathlib.Path(cache_file)
    try:
        r = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                                  "Referer": "https://edition.cnn.com/", "Accept": "application/json"},
                         timeout=30)
        r.raise_for_status()
        j = r.json()
        now = j["fear_and_greed"]
        hist = j.get("fear_and_greed_historical", {}).get("data", [])
        out = dict(score=round(float(now["score"]), 1), rating=now.get("rating"),
                   prev=now.get("previous_close"), w1=now.get("previous_1_week"),
                   m1=now.get("previous_1_month"), y1=now.get("previous_1_year"),
                   ts=str(now.get("timestamp", ""))[:10],
                   hist=[[time.strftime("%Y-%m-%d", time.gmtime(x["x"] / 1000)), round(float(x["y"]), 1)]
                         for x in hist][-260:],
                   live=True)
        for k in ("prev", "w1", "m1", "y1"):
            out[k] = None if out[k] is None else round(float(out[k]), 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
        print(f"fear & greed: {out['score']} {out['rating']}")
        return out
    except Exception as e:
        print("fear & greed failed:", e)
        if path.exists():
            out = json.loads(path.read_text())
            out["live"] = False
            return out
        return None


def _quotes(symbols, size=400):
    """Yahoo's batch quote (the numbers on a quote page): {symbol: {regularMarketPrice, ...Open, ...DayHigh,
    ...DayLow, ...Volume, regularMarketTime}}. 500 symbols come back in one request."""
    from yfinance.data import YfData
    yd, out = YfData(), {}
    for i in range(0, len(symbols), size):
        try:
            r = yd.get_raw_json("https://query1.finance.yahoo.com/v7/finance/quote",
                                params={"symbols": ",".join(symbols[i:i + size])})
            out.update({q["symbol"]: q for q in r["quoteResponse"]["result"]})
        except Exception as e:
            print("quotes failed:", symbols[i], e)
        time.sleep(0.5)
    return out


def fill_last_day(px, quotes=None):
    """Fill the newest session's bar from Yahoo's quotes where the daily history left it empty.

    For hours after the close Yahoo's daily candle can be blank for most stocks while the index has one
    (10 Nov 2026, 5 hours after the close: 80 of 2,711 had Monday's bar, yet every quote showed Monday's close).
    Only quotes whose last regular trade is on that same New York day are used, so a halted or delisted stock
    stays empty. Returns how many bars were filled."""
    close = px["Close"]
    if "^GSPC" not in close:
        return 0
    last = close["^GSPC"].dropna().index[-1]
    row = close.loc[last]
    todo = [t for t in close.columns if pd.isna(row[t])]
    if not todo:
        return 0
    got = (quotes or _quotes)(todo)
    day, n = last.strftime("%Y-%m-%d"), 0
    fields = (("Close", "regularMarketPrice"), ("Open", "regularMarketOpen"), ("High", "regularMarketDayHigh"),
              ("Low", "regularMarketDayLow"), ("Volume", "regularMarketVolume"))
    for t in todo:
        q = got.get(t) or {}
        ts, c = q.get("regularMarketTime"), q.get("regularMarketPrice")
        if not ts or not c or pd.Timestamp(ts, unit="s", tz="UTC").tz_convert("America/New_York").strftime("%Y-%m-%d") != day:
            continue
        for f, k in fields:
            if q.get(k) is not None:
                px[f].loc[last, t] = float(q[k])
        n += 1
    print(f"last day {day}: {len(todo)} stocks had no daily bar, {n} filled from Yahoo quotes")
    return n


def fetch_prices(u):
    extra = [t for t, _ in INDICES] + [t for t, _ in SECTORS] + DOW30 + ["QQQ", "^NDX", "^VXN"]
    px = prices(sorted(set(list(u.index) + extra)))
    fill_last_day(px)
    return px
