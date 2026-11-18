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
    days = pd.bdate_range("2026-11-13", periods=8)
    px = {key: pd.DataFrame({"A": [80., None, 19.] + [20.] * 5}, index=days) for key in ("Open", "Close", "High", "Low", "Volume")}
    bars = pd.DataFrame({"Stock Splits": [0, 4, 0] + [0] * 5}, index=days)
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


def test_chart_only_exports_observed_consistent_bars():
    from kcdt.compute import stock_pages
    days = pd.bdate_range("2026-11-10", periods=4)
    prices = {k: pd.DataFrame({"^GSPC": [100.] * 4, "A": [20., None, 25., 22.]}, index=days)
              for k in ("Open", "High", "Low", "Close", "Volume")}
    prices["High"].loc[days[2], "A"] = 24
    chart = stock_pages(prices, [{"s": "A"}])["A"]
    assert chart["cl"] == [20., None, 25., 22.]
    assert chart["o"] == chart["h"] == chart["l"] == [20., None, None, 22.]
    assert chart["v"][1] is None

def test_cold_calendar_fallback_preserves_new_feed_rows(monkeypatch):
    from types import SimpleNamespace
    from kcdt import econ, fetch
    seen = []
    def published(url, **kwargs):
        seen.append(url)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"econ": [
            {"d": "2026-11-17", "e": "Existing event", "a": "old"},
            {"d": "2026-11-18", "e": "Missing day", "a": "1"},
            {"d": "2026-10-01", "e": "Outside window"}]})
    monkeypatch.setattr(fetch.requests, "get", published)
    monkeypatch.setattr(fetch, "_econ_day", lambda day: [{"eventName": "Existing event", "actual": "new"}]
                        if day == "2026-11-18" else [])
    monkeypatch.setattr(fetch.time, "sleep", lambda _: None)
    monkeypatch.setattr(econ, "forexfactory", lambda: [])
    rows = fetch.economic_calendar(today="2026-11-18", published_url="https://example.test/data.json")
    assert seen == ["https://example.test/data.json"]
    assert [(r["d"],r["e"],r["a"]) for r in rows if not r.get("hol")] == [
        ("2026-11-17","Existing event","new"), ("2026-11-18","Missing day","1")]

def test_supplement_quotes_checks_basis_and_preserves_primary():
    from kcdt.verified import supplement_prices
    days = pd.to_datetime(["2026-11-13", "2026-11-16", "2026-11-17"])
    close = pd.DataFrame({"EMBJ": [19.6825, None, 18.90], "WLYB": [50.02, 50.02, None]}, index=days)
    px = {k: close.copy() for k in ("Open","High","Low","Close","Volume")}
    supplement_prices(px)
    assert px["Close"].iloc[-1]["WLYB"] == 50.37
    assert round(px["Close"]["EMBJ"].pct_change().iloc[-1]*100,2) == .69
    px["Close"].loc[days[-1],"WLYB"] = 51.
    supplement_prices(px)
    assert px["Close"].iloc[-1]["WLYB"] == 51.
    px["Close"] = close.copy()
    px["Close"].loc[days[0],"EMBJ"] = 78.73
    supplement_prices(px)
    assert pd.isna(px["Close"].iloc[1]["EMBJ"])


def test_verified_records_have_effective_dates_and_do_not_replace_later_results():
    from kcdt.verified import security_directory, apply_issuer_report
    panel = pd.DataFrame(index=pd.to_datetime(["2026-11-13"]))
    assert "NWE" not in security_directory(panel,"2026-11-13")
    assert security_directory(panel,"2026-11-17")["NWE"]["history"][-1][0]=="2026-11-13"
    row={"s":"NVDA","qend":"2026-07-31"}
    apply_issuer_report(row,"2026-11-16")
    assert "issuer" not in row
    apply_issuer_report(row,"2026-11-17")
    assert row["issuer"]["quarter"]=="2026-10-25" and row["qend"]=="2026-07-31"
    row={"s":"NVDA","erep":"2027-02-25"}
    apply_issuer_report(row,"2027-02-26")
    assert "issuer" not in row
