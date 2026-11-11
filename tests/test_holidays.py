"""Bank holidays and early closes for the economic calendar."""
from kcdt.holidays import calendar_rows, us_holidays
from kcdt.mkt import HOLIDAYS


def test_stock_closures_match_the_trading_calendar():
    shut = {r["d"] for y in (2026, 2027) for r in us_holidays(y) if r["stock"] == "closed"}
    assert shut == HOLIDAYS


def test_sifma_2026_and_half_days():
    H = {r["d"]: r for r in us_holidays(2026)}
    # SIFMA 2026: full closes incl. Columbus / Veterans Day; Good Friday a 12:00 early close (jobs report day)
    for d in ("2026-10-12", "2026-11-11"):
        assert (H[d]["stock"], H[d]["bond"], H[d]["bank"]) == ("open", "closed", "closed")
    assert (H["2026-04-03"]["stock"], H["2026-04-03"]["bond"], H["2026-04-03"]["bond_t"]) == ("closed", "early", "12:00")
    assert {d for d, r in H.items() if r["bond"] == "early" and r["bond_t"] == "14:00"} == \
        {"2026-05-22", "2026-07-02", "2026-11-27", "2026-12-24", "2026-12-31"}
    # Longbridge US half days
    assert {d for d, r in H.items() if r["stock"] == "early"} == {"2026-11-27", "2026-12-24"}
    # 4 July on Saturday: markets shut Friday 3 July, the Fed stays open
    assert (H["2026-07-03"]["stock"], H["2026-07-03"]["bank"]) == ("closed", "open")


def test_calendar_rows_and_forexfactory_extra():
    days = ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27"]
    rows = calendar_rows(days, [{"d": "2026-11-24", "title": "Bank Holiday", "impact": "Holiday"},
                                {"d": "2026-11-26", "title": "Bank Holiday", "impact": "Holiday"}])
    assert [(r["d"], r["hol"]["full"]) for r in rows] == [("2026-11-24", True), ("2026-11-26", True), ("2026-11-27", False)]
    assert rows[1]["note"] == "股市、債市、銀行休市" and rows[1]["imp"] == 3
    assert rows[2]["note"] == "股市提早 13:00 收市（美東）；債市提早 14:00 收市（美東）"
    assert rows[2]["u"] == "2026-11-27T05:00Z"
