import pandas as pd

from kcdt.ipo import directory, searchable


def test_directory_filters_non_equity():
    text = """Symbol|Security Name|Test Issue|ETF
NEW|NewCo Class A Common Stock|N|N
NEWX|NewCo Warrants for Common Stock|N|N
FAKE|Fake Common Stock|Y|N
ETF|ETF Common Shares|N|Y
File Creation Time: 20261119|||
"""
    assert directory(text, "Symbol") == {"NEW": "NewCo Class A Common Stock"}


def test_search_before_sufficient_history_and_promotion():
    days = pd.bdate_range("2026-01-02", periods=65)
    close = pd.DataFrame({"^GSPC": 1., "ONE": [None]*64 + [10.],
                          "TWO": [None]*63 + [10., 11.], "FIFTY": [None]*15 + [20.]*50}, index=days)
    names = {s: s+" Corp" for s in ["ONE", "TWO", "FIFTY", "EMPTY", "MATURE", "BOND"]}
    data = searchable({"Close": close}, [{"s": "MATURE"}], {"BOND": {}},
                      (names, {}, {"SOON": ["Future Corp", "2026-04-20"]}))
    assert set(data) == {"ONE", "TWO", "FIFTY", "EMPTY", "SOON"}
    assert data["SOON"]["expected"] == "2026-04-20"
    assert data["EMPTY"] == {"n": "EMPTY Corp"}
    assert "chg" not in data["ONE"] and "ma50" not in data["TWO"]
    assert data["TWO"]["chg"] == 10 and data["FIFTY"]["ma50"] == 20
    assert "rsr" not in data["FIFTY"]
