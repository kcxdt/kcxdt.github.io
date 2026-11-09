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
