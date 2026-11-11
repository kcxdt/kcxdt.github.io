"""Offline checks for the Nasdaq parsers (no network)."""
import sys, types, pathlib
import pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
from kcdt import fetch


class _Resp:
    def __init__(self, rows): self.rows = rows
    def json(self): return {"data": {"rows": self.rows}}
    def raise_for_status(self): pass


def _fake_nasdaq(pages, asked):
    def fake_get(url, **kw):
        if "faireconomy" in url:
            raise OSError("offline")
        day = url.rsplit("=", 1)[1]
        asked.append(day)
        return _Resp(pages.get(day, []))
    return fake_get


def _econ(today, pages, now, **kw):
    asked = []
    real_get, real_sleep = fetch.requests.get, fetch.time.sleep
    fetch.requests.get, fetch.time.sleep = _fake_nasdaq(pages, asked), lambda s: None
    try:
        return fetch.economic_calendar(today=today, now=now, **kw), asked
    finally:
        fetch.requests.get, fetch.time.sleep = real_get, real_sleep


def test_economic_calendar_dates_rows_one_day_back():
    # Nasdaq returns the 2026-10-28 FOMC decision under the next day's date; asked in winter time it shows 13:00
    pages = {"2026-10-29": [{"country": "United States", "gmt": "13:00", "eventName": "Fed Interest Rate Decision",
                             "actual": "4.00%", "consensus": "4.00%", "previous": "4.00%"},
                            {"country": "United States", "gmt": "13:00", "eventName": "FOMC Statement",
                             "actual": "&nbsp;", "consensus": " ", "previous": "&nbsp;"},
                            {"country": "Japan", "gmt": "01:00", "eventName": "CPI", "actual": "1%"}]}
    rows, asked = _econ("2026-10-29", pages, now="2026-11-11T02:28Z")
    assert asked[0] == "2026-10-27"  # Monday 10-26 is asked for as 10-27
    fed = [r for r in rows if r["e"] == "Fed Interest Rate Decision"][0]
    assert fed["d"] == "2026-10-28" and fed["u"] == "2026-10-28T18:00Z", fed  # 14:00 EDT
    stmt = [r for r in rows if r["e"] == "FOMC Statement"][0]
    assert stmt["a"] is None and stmt["p"] is None and stmt["c"] is None
    assert all(r["e"] != "CPI" for r in rows)


def test_economic_calendar_keeps_a_day_nasdaq_mixes_up(tmp_path=None):
    import tempfile
    us = lambda gmt, name, a="&nbsp;", p="1": {"country": "United States", "gmt": gmt, "eventName": name,
                                               "actual": a, "consensus": "", "previous": p}
    wed = [us("07:00", "MBA Mortgage Applications", p="-1.2%"), us("05:00", "OPEC Monthly Report")]
    good = {"2026-11-10": [us("10:00", "Wholesale Inventories", "0.7%")],
            "2026-11-11": [us("08:30", "CPI", "0.4%", "0.6%")], "2026-11-12": wed,
            "2026-11-13": [us("08:30", "Initial Jobless Claims", p="189K")]}
    days = str(pathlib.Path(tempfile.mkdtemp()) / "econ_days.json")
    rows, _ = _econ("2026-11-10", good, now="2026-11-10T12:00Z", days_file=days)
    cpi = [r for r in rows if r["e"] == "CPI"][0]
    assert cpi["d"] == "2026-11-10" and cpi["u"] == "2026-11-10T13:30Z" and cpi["a"] == "0.4%"  # 21:30 Hong Kong
    # evening of 10 Nov: date=2026-11-11 came back with Wednesday's rows; CPI day keeps the earlier answer
    bad = dict(good, **{"2026-11-11": wed})
    rows, _ = _econ("2026-11-10", bad, now="2026-11-10T23:28Z", days_file=days)
    assert [r["e"] for r in rows if r["d"] == "2026-11-10"] == ["CPI"]
    assert [r["e"] for r in rows if r["d"] == "2026-11-11"] == ["MBA Mortgage Applications", "OPEC Monthly Report"]
    # without the file, the rows the site shows now are the fallback
    shown = [dict(d="2026-11-10", u="2026-11-10T13:30Z", e="CPI", a="0.4%", c=None, p="0.6%", note="x")]
    rows, _ = _econ("2026-11-10", bad, now="2026-11-10T23:28Z", prev=shown)
    assert [r["e"] for r in rows if r["d"] == "2026-11-10"] == ["CPI"]
    # a failed day with nothing to fall back on is left out rather than filled with the wrong day
    rows, _ = _econ("2026-11-10", bad, now="2026-11-10T23:28Z")
    assert not [r for r in rows if r["d"] == "2026-11-10"]


def test_econ_labels():
    from kcdt.econ import label
    rows = [dict(d="2026-10-29", u="2026-10-29T12:30Z", e="Core PCE Price Index", a=None, c="0.3%", p="0.2%"),
            dict(d="2026-10-29", u="2026-10-29T12:30Z", e="Core PCE Price Index", a=None, c="3.1%", p="3.0%"),
            dict(d="2026-10-29", u="2026-10-29T12:30Z", e="GDP", a=None, c="3.2%", p="2.2%"),
            dict(d="2026-11-12", u="2026-11-12T13:30Z", e="CPI", a=None, c="0.2%", p="0.3%"),
            dict(d="2026-11-12", u="2026-11-12T13:30Z", e="CPI", a=None, c="2.9%", p="3.0%")]
    ff = [dict(t="2026-10-29T12:30Z", title="Core PCE Price Index m/m", impact="High", f="0.3%", p="0.2%"),
          dict(t="2026-10-29T12:30Z", title="Advance GDP q/q", impact="High", f="3.2%", p="1.5%")]
    label(rows, ff)
    pce_m, pce_y, gdp, cpi_m, cpi_y = rows
    assert (pce_m["per"], pce_y["per"]) == ("按月", "按年") and pce_m["imp"] == pce_y["imp"] == 3
    assert pce_m["ref"] == "2026 年 9 月" and not pce_m.get("unconf")
    assert gdp["per"] == "按季（年率）" and gdp["ref"] == "2026 年第 3 季"
    # never confirmed: no guess, flagged
    assert not cpi_m.get("per") and cpi_m["unconf"] and cpi_y["unconf"] and cpi_m["ref"] == "2026 年 10 月"


def test_econ_housing_percent_row_is_month_on_month():
    from kcdt.econ import label
    rows = [dict(d="2026-11-18", u="2026-11-18T13:30Z", e="Housing Starts", a=None, c="1.33M", p="1.31M"),
            dict(d="2026-11-18", u="2026-11-18T13:30Z", e="Housing Starts", a=None, c=None, p="-5.2%"),
            dict(d="2026-11-17", u="2026-11-17T13:30Z", e="Retail Sales", a=None, c=None, p="6.29%"),
            dict(d="2026-11-17", u="2026-11-17T13:30Z", e="Retail Sales", a=None, c=None, p="0.8%")]
    label(rows, [])
    assert rows[1]["per"] == "按月" and not rows[1].get("unconf")
    assert rows[2]["unconf"] and rows[3]["unconf"]  # two % rows: still never guessed


def test_econ_labels_remembered_and_notes():
    from kcdt.econ import label
    memory = {"CPI": {"0": "按月"}}  # old row-order format is dropped
    week1 = [dict(d="2026-10-15", u="2026-10-15T12:30Z", e="CPI", a="0.6%", c="0.4%", p="0.3%"),
             dict(d="2026-10-15", u="2026-10-15T12:30Z", e="CPI", a="3.7%", c="3.6%", p="3.0%")]
    label(week1, [dict(t="2026-10-15T12:30Z", title="CPI y/y", impact="High", f="3.6%", p="3.0%")], memory)
    assert [r["per"] for r in week1] == ["按月", "按年"] and "CPI" not in memory
    assert memory["cpi"]["v"]["0.6%"] == "按月" and memory["cpi"]["v"]["3.7%"] == "按年"
    # next month, no ForexFactory and Nasdaq lists y/y first: the previous values tell them apart
    week2 = [dict(d="2026-11-12", u="2026-11-12T13:30Z", e="CPI", c="3.5%", p="3.7%"),
             dict(d="2026-11-12", u="2026-11-12T13:30Z", e="CPI", c="0.2%", p="0.6%")]
    label(week2, [], memory)
    assert [r["per"] for r in week2] == ["按年", "按月"] and not any(r.get("unconf") for r in week2)
    # Nasdaq spells the two PCE headline rows differently; they still pair up
    pce = [dict(d="2026-10-30", u="2026-10-30T12:30Z", e="PCE Price index", c="2.9%", p="2.8%"),
           dict(d="2026-10-30", u="2026-10-30T12:30Z", e="PCE price index", c="0.3%", p="0.3%")]
    label(pce, [dict(t="2026-10-30T12:30Z", title="PCE Price Index m/m", impact="Medium", f="0.3%", p="0.3%")], {})
    assert [r["per"] for r in pce] == ["按年", "按月"]
    notes = [dict(d="2026-10-28", u="2026-10-28T18:00Z", e="Fed Interest Rate Decision", c="4.00%", p="4.00%"),
             dict(d="2026-10-27", u="2026-10-27T17:00Z", e="2-Year Note Auction", c=None, p="4.787%"),
             dict(d="2026-10-27", u="2026-10-27T12:55Z", e="Redbook", c=None, p="9.0%"),
             dict(d="2026-10-29", u="2026-10-29T20:30Z", e="Fed's Balance Sheet", c=None, p="6,754B"),
             dict(d="2026-10-28", u="2026-10-28T18:30Z", e="FOMC Press Conference", c=None, p=None)]
    label(notes, [], {})
    assert [r["note"] for r in notes] == ["議息會議結果", "國債拍賣 · 數字係得標息率",
                                         "截至 10/24 嗰星期 · 大型零售商同店銷售按年", "截至 10/28（星期三）", "議息會議後記者會"]
    assert notes[4]["link"].endswith("fomcpresconf20261028.htm")


def test_nasdaq_time_uses_new_yorks_offset_when_asked():
    # NFP 6 Nov 08:30 EST (13:30Z): asked in October (summer time) Nasdaq showed 09:30, asked in winter 08:30
    assert fetch._nasdaq_utc("2026-11-06", "09:30", now="2026-10-29T12:00Z") == "2026-11-06T13:30Z"
    assert fetch._nasdaq_utc("2026-11-06", "08:30", now="2026-11-11T02:28Z") == "2026-11-06T13:30Z"
    # FOMC 28 Oct 14:00 EDT (18:00Z) asked in winter shows 13:00
    assert fetch._nasdaq_utc("2026-10-28", "13:00", now="2026-11-11T02:28Z") == "2026-10-28T18:00Z"
    assert fetch._nasdaq_utc("2026-10-28", " ", now="2026-11-11T02:28Z") is None


def test_report_times_mark_fundamentals_a_quarter_behind():
    rep = fetch.report_times({"AFRM": "2026-11-05"})["AFRM"]  # 16:00 New York = 21:00Z
    assert rep == 1_793_912_400
    fetched = {"_ts": rep - 8 * 86400}  # fetched 28 Oct
    assert not fetch.reported_after_fetch(fetched, rep, rep + fetch.AFTER_REPORT - 1)  # Yahoo may not have it yet
    assert fetch.reported_after_fetch(fetched, rep, rep + fetch.AFTER_REPORT)
    assert not fetch.reported_after_fetch({"_ts": rep + fetch.AFTER_REPORT + 60}, rep, rep + 2 * 86400)
    assert not fetch.reported_after_fetch(fetched, None, rep + 2 * 86400)


def test_retry_while_the_new_quarter_is_missing():
    rep = fetch.report_times({"X": "2026-11-05"})["X"]
    behind = {"_ts": rep + fetch.AFTER_REPORT, "qend": "2026-06-30"}  # fetched after the report, Yahoo still had Q2
    now = rep + fetch.AFTER_REPORT + fetch.RETRY_AFTER
    assert fetch.quarter_behind(behind, rep) and fetch.retry_due(behind, rep, now)
    assert not fetch.retry_due(behind, rep, now - 60)                      # tried less than 12 hours ago
    assert not fetch.retry_due({**behind, "qend": "2026-09-30"}, rep, now)  # got the new quarter
    assert not fetch.retry_due(behind, rep, rep + 50 * 86400)               # gives up after 45 days
    assert not fetch.retry_due(behind, None, now)                           # no report known


def test_reported_since():
    e = 1_800_000_000  # report timestamp
    hold = fetch.AFTER_REPORT
    assert not fetch.reported_since({"edate": e, "_ts": e - 86400}, e + hold - 1)  # too soon: data not out yet
    assert fetch.reported_since({"edate": e, "_ts": e - 86400}, e + hold)          # fetched before the report
    assert not fetch.reported_since({"edate": e, "_ts": e + hold + 60}, e + 2 * hold)  # already refreshed
    assert not fetch.reported_since({"_ts": 0}, e)                                  # no date known


if __name__ == "__main__":
    test_economic_calendar_dates_rows_one_day_back()
    test_economic_calendar_keeps_a_day_nasdaq_mixes_up()
    test_nasdaq_time_uses_new_yorks_offset_when_asked()
    test_report_times_mark_fundamentals_a_quarter_behind()
    test_econ_labels()
    test_econ_labels_remembered_and_notes()

    test_reported_since()
    print("ok")


def test_earnings_calendar_splits_upcoming_and_reported():
    today = fetch.pd.Timestamp(fetch.pd.Timestamp.now(tz="America/New_York").date())
    past = next(d for d in fetch.pd.date_range(end=today - fetch.pd.Timedelta(days=1), periods=7)[::-1] if d.weekday() < 5)
    nxt = next(d for d in fetch.pd.date_range(today, periods=7) if d.weekday() < 5)
    rows = {past.strftime("%Y-%m-%d"): [{"symbol": "RNG", "time": "time-after-hours", "eps": "$0.23"},
                                        {"symbol": "LATE", "time": "time-pre-market", "eps": ""},
                                        {"symbol": "LOSS", "time": "time-pre-market", "eps": "($0.12)"}],
            nxt.strftime("%Y-%m-%d"): [{"symbol": "BRK.B", "time": "time-pre-market", "eps": "", "marketCap": "$1,050,000,000,000"}]}

    def fake_get(url, **kw):
        return _Resp(rows.get(url.rsplit("=", 1)[1], []))

    real_get, real_sleep = fetch.requests.get, fetch.time.sleep
    fetch.requests.get, fetch.time.sleep = fake_get, lambda s: None
    try:
        out, done = fetch.earnings_calendar(back=10)
    finally:
        fetch.requests.get, fetch.time.sleep = real_get, real_sleep
    assert out == {"BRK-B": [nxt.strftime("%Y-%m-%d"), "bmo", 1050.0]}
    assert done == {"RNG": past.strftime("%Y-%m-%d"), "LOSS": past.strftime("%Y-%m-%d")}  # no EPS yet = not confirmed


def test_earnings_calendar_cache_keeps_past_days(tmp_path=None):
    import tempfile
    cache = pathlib.Path(tempfile.mkdtemp()) / "done.json"
    today = fetch.pd.Timestamp(fetch.pd.Timestamp.now(tz="America/New_York").date())
    old = next(d for d in fetch.pd.date_range(end=today - fetch.pd.Timedelta(days=30), periods=7)[::-1] if d.weekday() < 5)
    cache.write_text(fetch.json.dumps({old.strftime("%Y-%m-%d"): ["A"], "2020-01-02": ["ZZZ"]}))
    asked = []

    def fake_get(url, **kw):
        asked.append(url.rsplit("=", 1)[1])
        return _Resp([])

    real_get, real_sleep = fetch.requests.get, fetch.time.sleep
    fetch.requests.get, fetch.time.sleep = fake_get, lambda s: None
    try:
        out, done = fetch.earnings_calendar(back=60, cache_file=cache)
        asked_first = len(asked)
        asked.clear()
        fetch.earnings_calendar(back=60, cache_file=cache)
    finally:
        fetch.requests.get, fetch.time.sleep = real_get, real_sleep
    assert done == {"A": old.strftime("%Y-%m-%d")}  # an August report confirmed from the cache, not asked again
    assert old.strftime("%Y-%m-%d") not in asked
    assert len(asked) < asked_first <= 100  # second run only asks the last few days and the weeks ahead
    saved = fetch.json.loads(cache.read_text())
    assert "2020-01-02" not in saved and saved[old.strftime("%Y-%m-%d")] == ["A"]


def test_earnings_calendar_across_clock_change():
    """The range spans 1 Nov 2026, when New York clocks go back; a tz-aware range crashed every run from 2 Nov."""
    def fake_get(url, **kw):
        return _Resp([])

    real_get, real_sleep = fetch.requests.get, fetch.time.sleep
    fetch.requests.get, fetch.time.sleep = fake_get, lambda s: None
    try:
        for today in ("2026-11-07", "2026-03-10"):  # autumn and spring changes
            out, done = fetch.earnings_calendar(back=100, today=today)
            assert out == {} and done == {}
    finally:
        fetch.requests.get, fetch.time.sleep = real_get, real_sleep


def test_earnings_dates_only_asks_for_missing_next_dates():
    import tempfile
    now = fetch.time.time()
    cache = pathlib.Path(tempfile.mkdtemp()) / "dates.json"
    cache.write_text(fetch.json.dumps({
        "FUT": {"ts": now + 20 * 86400, "est": False, "_ts": now - 5 * 86400},    # next date known, checked recently
        "OLDFUT": {"ts": now + 20 * 86400, "est": True, "_ts": now - 20 * 86400},  # known but a guess from weeks ago
        "PAST": {"ts": now - 60 * 86400, "est": False, "_ts": now - 4 * 86400},   # still shows the last report
        "RECENT": {"ts": now - 60 * 86400, "est": False, "_ts": now - 3600}}))    # checked an hour ago
    asked = []

    class T:
        def __init__(self, t):
            asked.append(t)
            self.info = {"earningsTimestamp": now + 30 * 86400, "isEarningsDateEstimate": t == "NEW"}

    real_t, real_sleep = getattr(fetch.yf, "Ticker", None), fetch.time.sleep
    fetch.yf.Ticker, fetch.time.sleep = T, lambda s: None
    try:
        got = fetch.earnings_dates(["NQ", "FUT", "OLDFUT", "PAST", "RECENT", "NEW"], cache, known={"NQ"})
    finally:
        fetch.yf.Ticker, fetch.time.sleep = real_t, real_sleep
    assert asked == ["NEW", "OLDFUT", "PAST"]  # never checked first; Nasdaq's and fresh ones skipped
    assert got["NEW"]["est"] is True and got["PAST"]["ts"] > now


def test_earn_rows_retries_a_failed_day():
    calls = []

    def flaky(url, **kw):
        calls.append(url)
        if len(calls) < 2:
            raise fetch.requests.ConnectionError("reset")
        return _Resp([{"symbol": "NVDA", "time": "time-after-hours"}])

    real_get, real_sleep = fetch.requests.get, fetch.time.sleep
    fetch.requests.get, fetch.time.sleep = flaky, lambda s: None
    try:
        rows = fetch._earn_rows("2026-11-17")
    finally:
        fetch.requests.get, fetch.time.sleep = real_get, real_sleep
    assert len(calls) == 2 and rows[0]["symbol"] == "NVDA"


def test_fill_last_day_uses_quotes_from_the_same_day_only():
    idx = pd.to_datetime(["2026-11-06", "2026-11-09"])
    nan = float("nan")
    mk = lambda v: pd.DataFrame({"^GSPC": [1.0, 2.0], "AAPL": [v, nan], "HALT": [v, nan], "OK": [v, v + 1]}, index=idx)
    px = {f: mk(v) for f, v in (("Open", 340.0), ("High", 346.0), ("Low", 338.0), ("Close", 342.37), ("Volume", 4e7))}
    mon = int(pd.Timestamp("2026-11-09 16:00", tz="America/New_York").timestamp())
    fri = int(pd.Timestamp("2026-11-06 16:00", tz="America/New_York").timestamp())
    asked = []

    def quotes(symbols):
        asked.extend(symbols)
        return {"AAPL": dict(regularMarketPrice=350.69, regularMarketOpen=344.935, regularMarketDayHigh=352.28,
                             regularMarketDayLow=344.78, regularMarketVolume=47499490, regularMarketTime=mon),
                "HALT": dict(regularMarketPrice=10.0, regularMarketTime=fri)}   # last traded on Friday
    assert fetch.fill_last_day(px, quotes) == 1 and asked == ["AAPL", "HALT"]
    last = idx[-1]
    assert px["Close"].loc[last, "AAPL"] == 350.69 and px["Open"].loc[last, "AAPL"] == 344.935
    assert px["High"].loc[last, "AAPL"] == 352.28 and px["Volume"].loc[last, "AAPL"] == 47499490
    assert pd.isna(px["Close"].loc[last, "HALT"]) and px["Close"].loc[last, "OK"] == 343.37
    assert fetch.fill_last_day({f: df.drop(columns=["HALT"]) for f, df in px.items()}, lambda s: 1 / 0) == 0  # nothing missing
