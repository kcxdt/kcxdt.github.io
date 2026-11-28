from copy import deepcopy

from kcdt import sec


def fact(begin, end, value, filed="2026-10-26"):
    return dict(start=begin, end=end, val=value, filed=filed, form="10-Q", accn="0000046195-26-000062")


def test_matching_quarters_and_partial_inputs():
    entries = [fact("2026-07-01", "2026-09-30", 1.49),
               fact("2025-07-01", "2025-09-30", 1.20),
               fact("2026-01-01", "2026-09-30", 4.27),
               fact("2026-07-01", "2026-09-30", 99, "2026-12-01")]
    payload = {"cik": 46195, "facts": {"us-gaap": {
        "EarningsPerShareDiluted": {"units": {"USD/shares": entries}},
        "Revenues": {"units": {"EUR": entries}}}}}
    metrics = sec.extract(payload, "2026-10-26", "2026-11-27")
    row = dict(qend="2026-06-30", fst=True, sq=10, eq=100, g3r=30)
    sec.apply(row, metrics)
    assert row["eqv"] == [1.49, 1.2] and row["eq"] == 24.2
    assert row["eqd"] == ["2026-09", "2025-09"] and row["fst"]
    assert row["sq"] is None and row["g3r"] is None and row["eps3"] is None
    metrics["revenue"] = deepcopy(metrics["eps"])
    metrics["revenue"]["values"] = [120, 110, 105, 102, 100, 100, 100]
    sec.apply(row, metrics)
    assert "fst" not in row and row["sq"] == 20 and row["g3r"] == 5
    assert row["rev3"] and row["qend"] == "2026-09-30"


def test_outage_keeps_cache_and_boh_disclosure(tmp_path, monkeypatch):
    def offline(*args, **kwargs):
        raise sec.requests.ConnectionError("offline")
    monkeypatch.setattr(sec.requests, "get", offline)
    row = dict(s="BOH", erep="2026-10-26", qend="2026-06-30", fst=True)
    status = sec.supplement([row], "2026-11-27", tmp_path / "cache.json")
    assert status["unavailable"] and row["eq"] == 24.2 and row["fst"]
    assert row["secQuarter"]["metrics"]["eps"]["url"] == sec.BOH_RELEASE
