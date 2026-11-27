import pandas as pd

from kcdt import checks, fetch, premarket
from kcdt.verified import apply_issuer_report, instrument_type


def test_thanksgiving_brief_and_black_friday():
    holiday = pd.Timestamp("2026-11-26 08:00", tz="America/New_York")
    assert not premarket.due(holiday)
    assert premarket.next_bday("2026-11-25") == "2026-11-27"
    assert premarket.prev_bday("2026-11-27") == "2026-11-25"
    assert checks.pm_expected(holiday + pd.Timedelta(hours=3)) == "2026-11-25"
    assert checks.pm_expected(holiday + pd.Timedelta(days=1, hours=3)) == "2026-11-27"


def test_nasdaq_checked_day_and_no_volume():
    days = pd.bdate_range("2026-11-23", periods=3)
    close = pd.DataFrame({"SNDK": [87.71, None, 73.61]}, index=days)
    px = {key: close.copy() for key in ("Close", "High", "Low", "Open", "Volume")}
    rows = [dict(date="11/24/2026", close="77.95", low="74.83", high="83.82", open="82.715", volume="N/A"),
            dict(date="11/25/2026", close="73.61")]
    fetch.reconcile_rows(px, "SNDK", days, rows)
    assert pd.isna(px["Close"].iloc[1, 0])
    rows[0]["volume"] = "97,538,190"
    px["Close"].iloc[-1, 0] *= 8
    fetch.reconcile_rows(px, "SNDK", days, rows)
    assert pd.isna(px["Close"].iloc[1, 0])
    px["Close"].iloc[-1, 0] = 73.61
    fetch.reconcile_rows(px, "SNDK", days, rows)
    assert px["Close"].iloc[1, 0] == 77.95
    assert round(px["Close"]["SNDK"].pct_change().iloc[-1] * 100, 2) == -5.57


def test_pending_estimate_and_security_types():
    row = {"s":"BRC", "eold":"2026-11-16", "erep":"2026-09-03"}
    apply_issuer_report(row,"2026-11-25")
    assert row["edate"] == "2026-12-08" and row["econf"] and not row["etime"]
    assert not checks.past_due([row],"2026-11-25")
    assert instrument_type("AFGB","American Financial Group Subordinated Debentures due 2059")
    assert instrument_type("DFTX","Definium Therapeutics Inc. Common Shares") is None
    assert instrument_type("PBT","Permian Basin Royalty Trust Common Stock") is None
