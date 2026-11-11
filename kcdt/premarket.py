"""python -m kcdt.premarket [--if-due] -> 每日盤前需知, ready about 3 hours before the New York open.

Runs on top of the last full build (site/data.json) and writes site/pm/DATE.json plus site/pm/index.json; with
KCDT_HISTORY set, the day's brief is also kept in that folder (the history branch) under pm/.
--if-due: only run on a US weekday between 05:30 and 16:00 New York time when today's brief doesn't exist yet,
so two cron times (one per side of daylight saving) give one brief a day. GitHub has started the scheduled runs
up to 7 hours late (06 Nov 2026: 11:56 New York), so a brief made after the 09:30 open is still published, marked
late=True so the page says it came out after the open."""
import io
import json
import os
import pathlib
import sys
import time

import pandas as pd
import requests

from .__main__ import OUT, ROOT, now_utc, pass_kc, write_page
from .fetch import NASDAQ_UA, EARN_TIME, economic_calendar

NY = "America/New_York"
IDX = [("NDX", "^NDX", "Nasdaq-100 指數"), ("SPX", "^GSPC", "標普 500 指數")]
ETF = [("QQQ", "Nasdaq-100 ETF"), ("SPY", "標普 500 ETF")]
MKT = [("VIX", "^VIX", "VIX 恐慌指數"), ("DXY", "DX-Y.NYB", "美元指數"), ("CL", "CL=F", "紐約原油期貨"), ("GC", "GC=F", "黃金期貨")]
WORLD = [("N225", "^N225", "日經 225"), ("HSI", "^HSI", "恒生指數"), ("DAX", "^GDAXI", "德國 DAX")]
QUOTE = "https://finance.yahoo.com/quote/"
TREASURY = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{y}/all"
            "?type=daily_treasury_yield_curve&field_tdr_date_value={y}&page&_format=csv")


def _r(x, d=2):
    return None if x is None or pd.isna(x) else round(float(x), d)


def _iso(ts):
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%MZ")


# When each daily bar is final: (time zone, minutes after midnight). A bar dated today before then is still trading,
# so the page calls it 最新（日內）, not 收市 (futures, VIX and the dollar index trade before the New York open).
CLOSE = {"^NDX": (NY, 960), "^GSPC": (NY, 960), "^VIX": (NY, 975), "DX-Y.NYB": (NY, 1020), "CL=F": (NY, 1020),
         "GC=F": (NY, 1020), "^N225": ("Asia/Tokyo", 930), "^HSI": ("Asia/Hong_Kong", 970),
         "^GDAXI": ("Europe/Berlin", 1050)}


def still_open(sym, day, now):
    """True when the bar dated `day` (YYYY-MM-DD) has not closed yet at `now`."""
    tz, m = CLOSE.get(sym, (NY, 960))
    t = pd.Timestamp(now).tz_convert(tz)
    return day > t.strftime("%Y-%m-%d") or (day == t.strftime("%Y-%m-%d") and t.hour * 60 + t.minute < m)


def daily_last(close):
    """Last two daily closes -> value, date and change."""
    c = close.dropna()
    if c.empty:
        return None
    v, d = float(c.iloc[-1]), c.index[-1]
    p = float(c.iloc[-2]) if len(c) > 1 else None
    return dict(v=_r(v), d=pd.Timestamp(d).strftime("%Y-%m-%d"), chg=_r(v - p) if p else None,
                pct=_r((v / p - 1) * 100) if p else None)


def treasury(now):
    """2- and 10-year par yields from the Treasury's daily CSV (end of day, previous US business day)."""
    try:
        r = requests.get(TREASURY.format(y=now.year), headers={"User-Agent": NASDAQ_UA["User-Agent"]}, timeout=30)
        df = pd.read_csv(io.StringIO(r.text))
    except Exception as e:
        print("treasury failed:", e)
        return []
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date")
    out = []
    for k, col, n in [("US2Y", "2 Yr", "美國 2 年期國債息率"), ("US10Y", "10 Yr", "美國 10 年期國債息率")]:
        c = df.set_index("Date")[col]
        x = daily_last(c)
        if x:
            out.append(dict(k=k, n=n, u="%", src="US Treasury", link="https://home.treasury.gov/resource-center/"
                            "data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve", eod=True, **x))
    return out


def _earn_day(day):
    from .fetch import _earn_rows  # same request, with retries
    return _earn_rows(day)


def _money(x):
    try:
        return float(str(x).replace("$", "").replace(",", "").replace("(", "-").replace(")", ""))
    except ValueError:
        return None


def reported_eps(t, day):
    """Yahoo's reported EPS for the report dated `day` (usually there within a few hours)."""
    import yfinance as yf
    try:
        e = yf.Ticker(t).get_earnings_dates(limit=4)
    except Exception as x:
        print("earnings dates failed:", t, x)
        return None
    if e is None or e.empty:
        return None
    e = e[[pd.Timestamp(i).tz_convert(NY).strftime("%Y-%m-%d") == day for i in e.index]]
    if e.empty or pd.isna(e["Reported EPS"].iloc[0]):
        return None
    row = e.iloc[0]
    return dict(est=_r(row.get("EPS Estimate")), rep=_r(row["Reported EPS"]), sur=_r(row.get("Surprise(%)"), 1))


def prev_bday(day):
    d = pd.Timestamp(day) - pd.offsets.BDay(1)
    return d.strftime("%Y-%m-%d")


def next_bday(day):
    return (pd.Timestamp(day) + pd.offsets.BDay(1)).strftime("%Y-%m-%d")


def earnings(today, stocks):
    """Today's reporters in the universe, last night's / this morning's that are already out with EPS, and the
    next trading day's (so the brief the day before already says who reports tomorrow)."""
    by = {s["s"]: s for s in stocks}
    big = lambda s: s.get("ndx") or (s.get("mcap") or 0) >= 10
    rows = lambda day: [(r["symbol"].strip().replace(".", "-"), EARN_TIME.get(r.get("time")), r) for r in _earn_day(day)
                        if (r.get("symbol") or "").strip().replace(".", "-") in by]
    def listed(day):
        return [dict(s=t, n=by[t].get("n"), tm=tm, est=_money(r.get("epsForecast")), ndx=bool(by[t].get("ndx")),
                     mcap=by[t].get("mcap")) for t, tm, r in rows(day)]
    out_today, out_next = listed(today), listed(next_bday(today))
    y = prev_bday(today)
    done = []
    for day, tm_want in [(y, "amc"), (today, "bmo")]:
        for t, tm, r in rows(day):
            s = by[t]
            if tm != tm_want or not big(s):
                continue
            eps = reported_eps(t, day)
            if eps:
                done.append(dict(s=t, n=s.get("n"), d=day, tm=tm, ndx=bool(s.get("ndx")), mcap=s.get("mcap"), **eps))
            time.sleep(0.3)
    key = lambda x: (not x["ndx"], -(x.get("mcap") or 0))
    return sorted(out_today, key=key), sorted(done, key=key), sorted(out_next, key=key)


def movers(bars, now):
    """Extended-hours move against the last regular-session close, per ticker. bars: {ticker: 5-minute frame}.
    "pre" when the latest quote is from a later day than that close (this morning), else "post" (after the close)."""
    out = {}
    for t, b in bars.items():
        b = b.dropna(subset=["Close"])
        b = b[b.index <= now]
        if b.empty:
            continue
        mins = b.index.hour * 60 + b.index.minute
        reg = b[(mins >= 570) & (mins < 960)]
        if reg.empty:
            continue
        close = float(reg["Close"].iloc[-1])
        after = b[b.index > reg.index[-1]]
        if after.empty:
            continue
        last, ts = float(after["Close"].iloc[-1]), after.index[-1]
        out[t] = dict(c=_r(close), v=_r(last), pct=_r((last / close - 1) * 100), t=_iso(ts),
                      when="pre" if ts.normalize() > reg.index[-1].normalize() else "post")
    return out


def _yf_bars(tickers, period, interval, prepost):
    import yfinance as yf
    out = {}
    for i in range(0, len(tickers), 50):
        part = tickers[i:i + 50]
        try:
            df = yf.download(part, period=period, interval=interval, prepost=prepost, auto_adjust=False,
                             progress=False, threads=False, group_by="ticker")
        except Exception as e:
            print("download failed:", e)
            continue
        for t in part:
            try:
                g = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            except KeyError:
                continue
            if g.index.tz is None:
                g.index = g.index.tz_localize("UTC")
            out[t] = g.tz_convert(NY)
        time.sleep(2)
    return out


def _econ_today(econ, today):
    """Releases dated today (New York) and anything released in the last 24 hours."""
    now = pd.Timestamp.now(tz="UTC")
    keep = []
    for e in econ:
        u = pd.Timestamp(e["u"]) if e.get("u") else None
        if e.get("d") == today or (u is not None and now - pd.Timedelta(hours=24) <= u <= now):
            keep.append(e)
    return keep


KEY = ("FOMC", "Fed Interest Rate", "CPI", "PCE", "GDP", "Nonfarm", "Unemployment Rate", "Initial Jobless", "Retail Sales",
       "ISM", "PPI", "JOLTS", "Consumer Confidence", "Michigan Consumer Sentiment", "Durable Goods Orders")


def build_brief(data, now=None):
    import yfinance as yf  # noqa: F401  (imported here so tests can stub the fetchers)
    now = now or pd.Timestamp.now(tz=NY)
    today = now.strftime("%Y-%m-%d")
    stocks = data["stocks"]
    dl = {}
    for k, y, n in IDX + MKT + WORLD:
        try:
            c = yf.download(y, period="10d", interval="1d", auto_adjust=False, progress=False)["Close"]
            c = c[y] if isinstance(c, pd.DataFrame) else c
            x = daily_last(c)
            if x:
                live = still_open(y, x["d"], now)
                dl[k] = dict(k=k, n=n, sym=y, link=QUOTE + y, eod=not live, live=live, **x)
        except Exception as e:
            print("daily failed:", y, e)
    mkt = [dl[k] for k, _y, _n in MKT if k in dl] + treasury(now)
    world = [dl[k] for k, _y, _n in WORLD if k in dl]
    idx = [dl[k] for k, _y, _n in IDX if k in dl]
    econ = economic_calendar(labels_file=str(ROOT / "cache" / "econ_labels.json"),
                             days_file=str(ROOT / "cache" / "econ_days.json"), prev=data.get("econ")) or data.get("econ") or []
    for e in econ:
        e["key"] = e.get("imp") == 3 or any(k.lower() in (e.get("e") or "").lower() for k in KEY)
    earn_today, earn_done, earn_next = earnings(today, stocks)
    want = {s["s"] for s in stocks if s.get("ndx") or pass_kc(s) or (s.get("mcap") or 0) >= 50}
    want |= {e["s"] for e in earn_today + earn_done} | {t for t, _n in ETF}
    mv = movers(_yf_bars(sorted(want), "5d", "5m", True), now)
    etf = [dict(k=t, n=n, link=QUOTE + t, **mv[t]) for t, n in ETF if t in mv]
    b = dict(d=today, t=now_utc(), asof=data.get("asof"), idx=idx, etf=etf, mkt=mkt, world=world, econ=_econ_today(econ, today),
             earn_today=earn_today, earn_done=earn_done, earn_next=earn_next, nd=next_bday(today), movers=mv)
    if now.hour * 60 + now.minute >= 570:
        b["late"] = True  # made after the 09:30 open: the "pre-market" prices include regular-session trading
    b["score"] = [round(h["score"] * 100 / 16) for h in (data.get("hist") or [])[-1:]] + \
        [round(h["score"] * 100 / 16) for h in ((data.get("tech") or {}).get("hist") or [])[-1:]]
    print(f"premarket {today}: {len(idx)} indices, {len(etf)} ETFs, {len(mkt)} market items, {len(world)} world, "
          f"{len(b['econ'])} releases, {len(earn_today)} reporting today, {len(earn_next)} next day, {len(earn_done)} reported, {len(mv)} movers")
    return b, econ


def publish(brief, folder=None):
    """Write site/pm/DATE.json and the index; keep a copy in the history folder when given."""
    out = OUT / "pm"
    out.mkdir(parents=True, exist_ok=True)
    body = json.dumps(brief, ensure_ascii=False, separators=(",", ":"))
    (out / f"{brief['d']}.json").write_text(body, encoding="utf-8")
    if folder:
        keep = pathlib.Path(folder) / "pm"
        keep.mkdir(parents=True, exist_ok=True)
        (keep / f"{brief['d']}.json").write_text(body, encoding="utf-8")
        for f in keep.glob("20*.json"):
            if not (out / f.name).exists():
                (out / f.name).write_bytes(f.read_bytes())
    days = sorted(f.stem for f in out.glob("20*.json"))
    (out / "index.json").write_text(json.dumps(days), encoding="utf-8")


def due(now, history=None):
    """A weekday between 05:30 and 16:00 New York with no brief for today yet, in the saved site or the history
    branch (the backup runs restore an older site copy when the cache is behind)."""
    m = now.hour * 60 + now.minute
    name = f"{now.strftime('%Y-%m-%d')}.json"
    have = (OUT / "pm" / name).exists() or bool(history and (pathlib.Path(history) / "pm" / name).exists())
    return now.weekday() < 5 and 330 <= m < 960 and not have


def main():
    now = pd.Timestamp.now(tz=NY)
    flag = os.environ.get("GITHUB_OUTPUT")
    if "--if-due" in sys.argv and not due(now, os.environ.get("KCDT_HISTORY")):
        print(f"not due ({now:%Y-%m-%d %H:%M} New York)")
        if flag:
            open(flag, "a").write("run=false\n")
        return
    path = OUT / "data.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    brief, econ = build_brief(data, now)
    publish(brief, os.environ.get("KCDT_HISTORY"))
    if econ:  # the brief fetched a fresh calendar: the 經濟 page gets it too
        data["econ"], data["econ_t"] = econ, now_utc()
    data["pm_t"], data["pm_d"] = brief["t"], brief["d"]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    write_page(data)
    if flag:
        open(flag, "a").write("run=true\n")


if __name__ == "__main__":
    main()
