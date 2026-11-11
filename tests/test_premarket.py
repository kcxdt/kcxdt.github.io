"""Offline checks for the 盤前需知 calculations (no network)."""
import sys, types, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
import numpy as np
import pandas as pd
from kcdt import premarket as pm

NY = "America/New_York"


def bars(start, end, base=100.0):
    idx = pd.date_range(pd.Timestamp(start, tz=NY), pd.Timestamp(end, tz=NY), freq="5min", inclusive="left")
    # CME is closed 17:00–18:00 New York
    idx = idx[~((idx.hour == 17))]
    c = base + np.arange(len(idx)) * 0.01
    return pd.DataFrame({"Open": c, "High": c + 0.5, "Low": c - 0.5, "Close": c}, index=idx)


def test_movers():
    now = pd.Timestamp("2026-10-30 06:00", tz=NY)
    b = bars("2026-10-29 04:00", "2026-10-30 06:00")
    b = b[(b.index.hour < 20) | (b.index >= pd.Timestamp("2026-10-30 04:00", tz=NY))]
    m = pm.movers({"AAA": b, "BBB": b[b.index < pd.Timestamp("2026-10-29 19:00", tz=NY)]}, now)
    close = b[b.index < pd.Timestamp("2026-10-29 16:00", tz=NY)]["Close"].iloc[-1]
    assert m["AAA"]["c"] == round(close, 2) and m["AAA"]["when"] == "pre"
    assert m["BBB"]["when"] == "post"
    # run in the evening: compare with today's close, and the quote is after hours
    late = pm.movers({"AAA": b[b.index < pd.Timestamp("2026-10-29 20:00", tz=NY)]}, pd.Timestamp("2026-10-29 22:00", tz=NY))
    assert late["AAA"]["c"] == round(close, 2) and late["AAA"]["when"] == "post"


def test_daily_last_and_prev_bday():
    c = pd.Series([10.0, 11.0], index=pd.to_datetime(["2026-10-28", "2026-10-29"]))
    assert pm.daily_last(c) == dict(v=11.0, d="2026-10-29", chg=1.0, pct=10.0)
    assert pm.prev_bday("2026-11-02") == "2026-10-30"


def test_live_results(monkeypatch):
    data = {"stocks": [{"s": "CRWV", "ndx": True}, {"s": "BIG", "mcap": 50}, {"s": "TINY", "mcap": 2}, {"s": "AM", "mcap": 20}]}
    cal = [{"symbol": "CRWV", "time": "time-after-hours", "epsForecast": "($1.45)"},
           {"symbol": "BIG", "time": "time-pre-market", "epsForecast": "$0.50"},
           {"symbol": "TINY", "time": "time-after-hours", "epsForecast": "$0.10"},
           {"symbol": "AM", "time": "time-after-hours", "epsForecast": "$1.00"}]
    monkeypatch.setattr(pm, "_earn_day", lambda d: cal)
    asked = []
    eps = {"CRWV": dict(est=-1.45, rep=-1.27, sur=12.4), "BIG": dict(est=0.5, rep=0.6, sur=20.0)}
    monkeypatch.setattr(pm, "reported_eps", lambda t, d: asked.append(t) or eps.get(t))
    monkeypatch.setattr(pm, "_yf_bars", lambda *a: {})
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    # 10:00 New York: only the pre-market reporter is looked up; the small cap is left out
    out = pm.live_results(data, pd.Timestamp("2026-11-10 10:00", tz=NY))
    assert asked == ["BIG"] and [r["s"] for r in out["rows"]] == ["CRWV", "BIG", "AM"]
    # 17:00: after-close ones too, and BIG's earlier result is kept without asking again
    asked.clear()
    out = pm.live_results(data, pd.Timestamp("2026-11-10 17:00", tz=NY), out)
    assert asked == ["CRWV", "AM"]
    r = {x["s"]: x for x in out["rows"]}
    assert r["CRWV"]["rep"] == -1.27 and r["BIG"]["rep"] == 0.6 and "rep" not in r["AM"]
    # a weekend or before 07:00 does nothing
    assert pm.live_results(data, pd.Timestamp("2026-11-14 17:00", tz=NY)) is None
    assert pm.live_results(data, pd.Timestamp("2026-11-10 06:00", tz=NY)) is None


if __name__ == "__main__":
    test_movers()
    test_daily_last_and_prev_bday()
    print("ok")


def test_earnings_lists_the_next_trading_day():
    """On Monday 16 Nov the brief already names NVDA, which reports after Tuesday's close."""
    days = {"2026-11-16": [{"symbol": "AAA", "time": "time-pre-market"}],
            "2026-11-17": [{"symbol": "NVDA", "time": "time-after-hours", "epsForecast": "$1.50"}]}
    real = pm._earn_day
    pm._earn_day = lambda d: days.get(d, [])
    try:
        today, done, nxt = pm.earnings("2026-11-16", [{"s": "AAA", "ndx": False, "mcap": 5}, {"s": "NVDA", "ndx": True, "mcap": 4000}])
    finally:
        pm._earn_day = real
    assert [e["s"] for e in today] == ["AAA"] and done == []
    assert nxt == [dict(s="NVDA", n=None, tm="amc", est=1.5, ndx=True, mcap=4000)]
    assert pm.next_bday("2026-11-13") == "2026-11-16"  # Friday -> Monday


def test_due_skips_a_brief_already_on_the_history_branch(tmp_path=None):
    import tempfile, pathlib
    from kcdt import premarket as pm
    hist = pathlib.Path(tmp_path or tempfile.mkdtemp())
    now = pd.Timestamp("2026-11-09 12:40", tz="America/New_York")  # a late backup slot, still before 16:00
    old = pm.OUT
    pm.OUT = hist / "site"  # an older site copy without today's brief
    try:
        assert pm.due(now, hist)
        (hist / "pm").mkdir()
        (hist / "pm" / "2026-11-09.json").write_text("{}")
        assert not pm.due(now, hist)  # the earlier run already made it: no second brief
        assert not pm.due(pd.Timestamp("2026-11-09 16:05", tz="America/New_York"))  # after the close
    finally:
        pm.OUT = old
