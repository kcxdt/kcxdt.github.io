"""Close-update timing: which close should be on the site, backup runs, intraday rows."""
import json

import pandas as pd

from kcdt.__main__ import drop_open_session, snapshot_done
from kcdt.mkt import close_due, is_trading_day

NY = "America/New_York"
T = lambda s: pd.Timestamp(s, tz=NY)


def test_close_due():
    assert close_due(T("2026-11-09 17:50")) == "2026-11-09"   # Monday after the close
    assert close_due(T("2026-11-09 15:00")) == "2026-11-06"   # Monday before the close -> Friday
    assert close_due(T("2026-11-08 12:00")) == "2026-11-06"   # Sunday
    assert close_due(T("2026-11-27 09:00")) == "2026-11-25"   # after Thanksgiving (26th closed)
    assert close_due(T("2026-11-27 17:00")) == "2026-11-27"   # half day still trades
    assert not is_trading_day("2026-12-25") and is_trading_day("2026-12-24")


def test_snapshot_done(tmp_path):
    (tmp_path / "2026-11-09.json").write_text(json.dumps({"built": "2026-11-09T22:05Z"}))
    (tmp_path / "2026-11-10.json").write_text(json.dumps({"built": "2026-11-10T16:00Z"}))  # intraday build
    assert snapshot_done("2026-11-09", tmp_path)
    assert not snapshot_done("2026-11-10", tmp_path)
    assert not snapshot_done("2026-11-11", tmp_path)


def test_drop_open_session():
    idx = pd.to_datetime(["2026-11-06", "2026-11-09", "2026-11-10"])
    px = {"Close": pd.DataFrame({"A": [1.0, 2.0, 3.0]}, index=idx)}
    assert len(drop_open_session(px, T("2026-11-10 11:00"))["Close"]) == 2   # still trading
    assert len(drop_open_session(px, T("2026-11-10 16:30"))["Close"]) == 3   # closed
    assert len(drop_open_session(px, T("2026-11-11 05:00"))["Close"]) == 3   # next morning


def test_pack_stocks():
    from kcdt.__main__ import pack_stocks, render
    data = {"asof": "2026-11-09", "built": "2026-11-09T22:05Z",
            "stocks": [{"s": "AAA", "c": 1.5, "us": None}, {"s": "BBB", "c": 2.0, "ind": "Semis", "x50": False}]}
    js = pack_stocks(data)
    body = json.loads(js[len("window.KCDT_STOCKS="):js.index(";if(window.stocksReady)")])
    rows = [{k: v for k, v in zip(body["k"], r) if v is not None} for r in body["r"]]   # what the page rebuilds
    assert rows == [{"s": "AAA", "c": 1.5}, {"s": "BBB", "c": 2.0, "ind": "Semis", "x50": False}]
    assert body["asof"] == "2026-11-09"
    html = render(data)
    assert '"stocks"' not in html and "stocks.js?v=20261109T2205Z" in html
