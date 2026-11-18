import pandas as pd

from kcdt.compute import enrich_earnings
from kcdt.fetch import earnings_calendar, repair_split_gaps, report_history


def test_estimate_after_confirmed_release():
    eps = pd.Series([1, 2, 3, 4], index=pd.to_datetime(
        ["2026-02-25 21:00Z", "2026-05-20 20:00Z", "2026-08-26 20:00Z", "2026-11-17 21:00Z"]))
    history = report_history(eps)
    row = {"edate": "2026-11-18", "etime": None}
    enrich_earnings(row, {}, history)
    assert row["edate"] is None and row["erep"] == "2026-11-17"
    assert row["emonth"] == "2027-02" and row["lastSession"] == "amc"


def test_calendar_retains_successful_day_when_source_fails(tmp_path, monkeypatch):
    today = "2026-11-17"
    cache = tmp_path / "done.json"
    monkeypatch.setattr("kcdt.fetch._earn_rows", lambda *a, **k: [
        {"symbol": "NVDA", "eps": 0}, {"symbol": "OTHER", "eps": None}])
    upcoming, done = earnings_calendar(days=1, back=0, today=today, cache_file=cache)
    assert done == {"NVDA": today} and "NVDA" not in upcoming
    monkeypatch.setattr("kcdt.fetch._earn_rows", lambda *a, **k: None)
    upcoming, done = earnings_calendar(days=1, back=0, today=today, cache_file=cache)
    assert done == {"NVDA": today} and upcoming["OTHER"][3:] == [today, True]


def test_split_gap_is_corrected_once():
    days = pd.to_datetime(["2026-11-13", "2026-11-16", "2026-11-17"])
    px = {key: pd.DataFrame({"A": [80., None, 19.]}, index=days) for key in ("Open", "Close", "High", "Low", "Volume")}
    bars = pd.DataFrame({"Stock Splits": [0, 4, 0]}, index=days)
    for _ in range(2):
        repair_split_gaps(px, lambda symbol: bars)
    assert px["Close"]["A"].iloc[0] == 20 and px["Volume"]["A"].iloc[0] == 320
    assert pd.isna(px["Close"]["A"].iloc[1])

def test_live_zero_eps_reaches_watchlist_without_losing_next_quarter():
    from kcdt.refresh import apply_live_results
    rows = [{"s":"A", "edate":"2026-11-17", "etime":"amc", "qend":"2026-07-31", "fts":"2026-11-08"},
            {"s":"B", "edate":"2027-02-24"}, {"s":"C"}]
    data = {"stocks":rows, "earn_live":{"d":"2026-11-17", "rows":[{"s":"A","rep":0,"tm":"amc"},
                                                                    {"s":"B","rep":1},{"s":"C","rep":None}]}}
    apply_live_results(data)
    assert rows[0]["fst"] and rows[0]["edate"] is None and rows[0]["lastSession"] == "amc"
    assert rows[1]["edate"] == "2027-02-24" and "erep" not in rows[2]
