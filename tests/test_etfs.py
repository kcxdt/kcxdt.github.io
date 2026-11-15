"""Single-stock ETF matching from Nasdaq's ETF names (kcdt/etfs.py)."""
from kcdt.etfs import classify, single_stock

POOL = {"NVDA", "TSLA", "AMD", "CIEN", "COR", "ON", "BRK-B", "MSCI", "ETN", "GE", "TSM", "META"}


def test_names():
    c = lambda n: classify(n, POOL)
    assert c("GraniteShares 2x Long NVDA Daily ETF") == ("NVDA", "L", 2.0)
    assert c("GraniteShares 2x Short NVDA Daily ETF") == ("NVDA", "S", 2.0)
    assert c("ETF Opportunities Trust T-Rex 2X Inverse NVIDIA Daily Target E") == ("NVDA", "S", 2.0)
    assert c("Tradr 1.5X Short NVDA Daily ETF") == ("NVDA", "S", 1.5)
    assert c("Direxion Daily NVDA Bear 1X ETF") == ("NVDA", "S", 1.0)
    assert c("Corgi NVDA 2x Daily ETF") == ("NVDA", "L", 2.0)
    assert c("Tidal ETF Trust II YieldMax NVDA Option Income Strategy ETF") == ("NVDA", "I", 1.0)
    assert c("Kurv Yield Premium Strategy Tesla (TSLA) ETF") == ("TSLA", "I", 1.0)
    assert c("Leverage Shares 2X Long NVDA Daily ETF") == ("NVDA", "L", 2.0)  # "LeveraGE" is not GE
    assert c("Tidal Trust II YieldMax BRK.B Option Income Strategy ETF") == ("BRK-B", "I", 1.0)
    assert c("Direxion Daily TSM Bull 2X ETF") == ("TSM", "L", 2.0)
    # not about one stock
    assert c("Direxion Daily Semiconductor Bull 3X ETF") is None
    assert c("Federated Hermes ETF Trust Federated Hermes Short Duration Cor") is None
    assert c("ProShares Short MSCI EAFE") is None
    assert c("ETRACS Monthly Pay 2xLeveraged Preferred Stock ETN") is None
    assert c("Leverage Shares 100% TSLA AND 100% NVDA Daily ETF") is None
    assert c("Invesco QQQ Trust") is None


def test_map():
    rows = [("NVDL", "GraniteShares 2x Long NVDA Daily ETF"), ("NVD", "GraniteShares 2x Short NVDA Daily ETF"),
            ("NVDY", "Tidal ETF Trust II YieldMax NVDA Option Income Strategy ETF"),
            ("NVDX", "ETF Opportunities Trust T-Rex 2X Long NVIDIA Daily Target ETF"),
            ("ACLZ", "Corgi AMD 2x Daily ETF"), ("ACMM", "Corgi AMD 2x Daily ETF"),  # one name on two funds: neither kept
            ("LWLX", "Tradr 2X Long CIEN Daily ETF Tradr 2X Long LWLG Daily ETF"),  # the fund is LWLG's, not CIEN's
            ("CIEX", "Tradr 2X Long CIEN Daily ETF Tradr 2X Long CIEN Daily ETF"),
            ("NVDA", "NVIDIA 2x fake"), ("SMH", "VanEck Semiconductor ETF")]
    m = single_stock(rows, POOL)
    assert [e[0] for e in m["NVDA"]] == ["NVDL", "NVDX", "NVD", "NVDY"]  # long, short, income
    assert m["NVDA"][1][1] == "T-Rex 2X Long NVIDIA Daily Target ETF"  # the legal trust is dropped from the name
    assert m["NVDA"][3][1] == "YieldMax NVDA Option Income Strategy ETF"
    assert "AMD" not in m and m["CIEN"] == [["CIEX", "Tradr 2X Long CIEN Daily ETF", "L", 2.0]]
