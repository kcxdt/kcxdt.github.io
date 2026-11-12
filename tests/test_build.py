"""Offline check: synthetic prices + fundamentals -> build() -> rendered page."""
import numpy as np
import pandas as pd

from kcdt.compute import DOW30, INDICES, SECTORS, build, kc_technical, stock_pages, wants_fundamentals

GICS = ["Information Technology", "Communication Services", "Consumer Discretionary",
        "Financials", "Health Care", "Industrials", "Energy"]


def fake_prices(tickers, days=500, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2026-10-27", periods=days)
    rets = rng.normal(0.0005, 0.018, (days, len(tickers)))
    close = pd.DataFrame(100 * np.exp(rets.cumsum(0)), index=idx, columns=tickers)
    close["^VIX"] = 15 + 5 * rng.random(days)
    high = close * (1 + rng.random(close.shape) * 0.01)
    low = close * (1 - rng.random(close.shape) * 0.01)
    opn = close.shift(1).fillna(close)
    vol = pd.DataFrame(rng.integers(20_000, 5_000_000, close.shape), index=idx, columns=tickers).astype(float)
    return {"Open": opn, "High": high, "Low": low, "Close": close, "Volume": vol}


def fake_meta(universe):
    m = pd.DataFrame({
        "n": [f"Company {t}" for t in universe],
        "gics": [GICS[i % len(GICS)] for i in range(len(universe))],
        "ndx": [i < 100 for i in range(len(universe))],
        "sp500": [i < 1000 for i in range(len(universe))], "sp400": False, "sp600": False,
        "us": [True if i % 3 else None for i in range(len(universe))],
    }, index=universe)
    m["idx"] = m["ndx"] | m["sp500"]
    return m


def fake_fund(tickers, seed=2):
    rng = np.random.default_rng(seed)
    out = {}
    inds = ["Semiconductors", "Software - Application", "Banks - Regional", None, "Semiconductors", "Biotechnology"]
    for i, t in enumerate(tickers):
        rev = sorted(rng.uniform(1e8, 1e10, 8).tolist(), reverse=bool(rng.integers(0, 2)))
        out[t] = dict(mcap=float(rng.uniform(3e8, 3e12)), beta=float(rng.uniform(0.3, 2.5)),
                      inst=float(rng.uniform(0, 1)), sq=float(rng.uniform(-0.2, 0.8)),
                      eq=float(rng.uniform(-0.5, 1.5)), pe=float(rng.uniform(5, 80)), ysec=None,
                      ipo=1.2e12, edate=1.8e9, revq=rev, epsq=[1.5, 1.2, 1.0, 0.8, 0.7], reva=[5e9, 4e9, 3e9],
                      epsa=[4.0, 3.0, 2.5], epsh=[1.5, 1.2, 1.0, 0.8, 0.7, 0.6, 0.5, 0.4],
                      efc=[0.3, 0.2], rfc=[0.25, None], _ts=1.7616e9,
                      qend="2026-09-30", erep=1.7616e9 + 86400, ind=inds[i % len(inds)])
        # some EPS percentages that need a warning: loss to profit, tiny base, still a loss
        eps = {3: [0.3, 0.2, 0.1, 0.0, -0.1], 4: [0.65, 0.4, 0.2, 0.1, 0.02], 5: [-0.2, -0.3, -0.4, -0.4, -0.5]}.get(i % 7)
        if eps:
            out[t].update(epsq=eps, epsh=[eps[0] + 0.2] + eps[1:] + [0.1, 0.1, 0.1])
    return out


def test_eps_yoy():
    from kcdt.compute import eps_yoy
    r = eps_yoy([0.30, 0, 0, 0, -0.10], [0.45, 0, 0, 0, 0.05])  # loss a year ago -> profit; adjusted far from GAAP
    assert r["ew"] == ["turn", "gap"] and r["eqv"] == [0.3, -0.1] and "eqd" not in r
    r = eps_yoy([0.65, 0, 0, 0, 0.02], [0.66, 0, 0, 0, 0.30], "2026-06-30")  # tiny base: +3,150%
    assert r["ew"] == ["base"] and r["eq"] == 3150.0 and r["eqa"] == 120.0 and r["eqd"] == ["2026-06", "2025-06"]
    r = eps_yoy([-0.2, 0, 0, 0, -0.5], None)
    assert r["ew"] == ["loss"] and r["eqa"] is None
    assert eps_yoy([1.0, 0.9], [1.0])["eq"] is None


def test_nasdaq_qend():
    from kcdt.compute import nasdaq_qend
    f = dict(qend="2026-09-27", revq=[94.9e9, 94.0e9], epsq=[1.64, 1.57], nrev=[94.93e9], neps=[1.64])
    assert nasdaq_qend(f) == "2026-09-27"  # same quarter as Yahoo's statement
    assert nasdaq_qend({**f, "nrev": [94.04e9], "neps": [1.57]}) == "2026-06-27"  # Nasdaq one quarter behind
    assert nasdaq_qend({**f, "nrev": [80e9]}) is None  # can't tell which quarter
    assert nasdaq_qend({**f, "revq": [], "nrev": []}) == "2026-09-27"  # no revenue: EPS decides
    assert nasdaq_qend({**f, "qend": None}) is None


def test_build():
    universe = [f"S{i:04d}" for i in range(1100)] + DOW30[:5]
    tickers = sorted(set(universe + DOW30 + [t for t, _ in INDICES] + [t for t, _ in SECTORS] + ["^NDX", "^VXN", "QQQ"]))
    px = fake_prices(tickers)
    # a partial "today" row where only VIX has traded
    extra = pd.Timestamp("2026-10-28")
    for k in px:
        px[k].loc[extra] = np.nan
    px["Close"].loc[extra, "^VIX"] = 16.0
    meta = fake_meta(universe)
    want = wants_fundamentals(px, meta)
    assert len(want) >= 100
    hot = kc_technical(px, meta)
    assert hot and hot <= set(want)  # the strict technical pass is inside the looser fundamentals pool
    fng = dict(score=42.0, rating="fear", prev=40.1, w1=35.0, m1=55.2, y1=60.0, ts="2026-10-27",
               hist=[["2026-%02d-%02d" % (1 + i // 28, 1 + i % 28), 30 + (i * 7) % 50] for i in range(250)], live=True)
    fund = fake_fund(want)
    past = pd.Timestamp("2026-10-21 16:00", tz="UTC").timestamp()  # noon in New York
    a, b = want[1], want[2]  # both were expected on 10-21; only b shows up on Nasdaq's calendar with an actual EPS
    fund[a].update(edate=past, erep=past - 90 * 86400)
    fund[b].update(edate=past, erep=past - 90 * 86400)
    other = next(t for t in universe if t not in fund)  # no fundamentals lookup: sector/industry from profiles
    fund[want[3]].update(epsq=[1.5, 1.2, 1.0, 0.8], nrev=list(fund[want[3]]["revq"]), neps=[1.5, 1.2, 1.0, 0.8, 0.7])
    w4 = want[4]  # on Nasdaq's calendar without a time; Yahoo has the same day with a time
    fund[w4].update(edate=pd.Timestamp("2026-10-30 12:00", tz="UTC").timestamp(), eest=False)
    w5, w6 = [t for t in want[5:] if t != "S0001"][:2]  # Nasdaq without a time: w5 Yahoo set another day (Yahoo wins), w6 nobody confirms
    fund[w5].update(edate=pd.Timestamp("2026-11-03 21:00", tz="UTC").timestamp(), eest=False)
    fund[w6].update(edate=pd.Timestamp("2026-11-03 21:00", tz="UTC").timestamp(), eest=True)
    w7, gap = [t for t in want[10:] if t != "S0001"][:2]  # w7 reported 10-24 but the data is still the June quarter: 業績後待更新
    rep7 = pd.Timestamp("2026-10-23 21:00", tz="UTC").timestamp()
    fund[w7].update(qend="2026-06-30", erep=rep7, edate=None, _ts=rep7 + 86400)
    # one stock without a bar on the last day: its price is filled from the day before
    for k in px:
        px[k].loc[pd.Timestamp("2026-10-27"), gap] = np.nan
    d = build(px, meta, fund, fng, {"S0001": ["2026-10-29", "amc"], w4: ["2026-10-30", None, 12.5],
                                    w5: ["2026-11-09", None, 9.0], w6: ["2026-11-09", None, 9.0],
                                    other: ["2026-10-30", "bmo", 321.0]}, {b: "2026-10-22"},
              {other: dict(ysec="Industrials", ind="Airlines")})
    assert d["asof"] == "2026-10-27"
    assert len(d["hist"]) == 60 and len(d["stocks"]) == 1105
    assert all(0 <= h["score"] <= 16 for h in d["hist"])
    assert d["breadth"]["ndx"]["n"] == 100 and d["nfund"] == len(want)
    assert d["breadth"]["usa"]["n"] == 1105 and d["breadth"]["all"]["n"] == 1000
    assert len(d["minis"]) == 5 and d["minis"][0]["n"] == "Nasdaq-100" and len(d["minis"][0]["c"]) == 60
    ranks = [s["rsr"] for s in d["stocks"]]
    assert min(ranks) == 1 and max(ranks) == 99
    f0 = next(s for s in d["stocks"] if s["s"] == want[0])
    assert f0["g3e"] == 100.0 and f0["yr"] is True and f0["e0"] == 30.0 and f0["r1"] is None  # min of 1.5/0.7, 1.2/0.6, 1.0/0.5
    assert d["fng"]["score"] == 42.0
    s1 = next(s for s in d["stocks"] if s["s"] == "S0001")
    assert s1["edate"] == "2026-10-29" and s1["etime"] == "amc"
    s4 = next(s for s in d["stocks"] if s["s"] == w4)
    assert s4["edate"] == "2026-10-30" and s4["etime"] == "bmo" and s4["econf"] and "ecap" not in s4
    s5, s6 = (next(s for s in d["stocks"] if s["s"] == t) for t in (w5, w6))
    assert s5["edate"] == "2026-11-03" and s5["etime"] == "amc" and s5["econf"]
    assert s6["edate"] == "2026-11-09" and s6["etime"] is None and "econf" not in s6
    assert len(d["tech"]["hist"]) == 60 and 0 <= d["tech"]["hist"][-1]["score"] <= 16
    assert set(d["panic"]) == {"on", "day"}
    assert set(d["rebound"]) == {"on", "day", "ma20", "close"}
    assert all(1 <= s["prsr"] <= 99 and "pd50" in s and "px50" in s for s in d["stocks"])
    # reported after the last fetch, but the data's latest quarter is recent enough: not 業績後待更新
    assert f0["fts"] and f0["qend"] == "2026-09-30" and f0["erep"] >= f0["fts"] and "fst" not in f0
    s7 = next(s for s in d["stocks"] if s["s"] == w7)
    assert s7["fst"] is True
    sg = next(s for s in d["stocks"] if s["s"] == gap)
    assert sg["fill"] == 1 and sg["pxd"] == "2026-10-26" and sg["chg"] is None and sg["vol"] is None and not sg["nh"]
    assert sg["c"] is not None and sg["d50"] is not None  # the filled close still places it against its averages
    assert d["breadth"]["usa"]["adv"] + d["breadth"]["usa"]["dec"] <= 1104
    assert f0["eq"] == 114.3 and f0["eqa"] == 114.3 and f0["eqv"] == [1.5, 0.7] and f0["eqd"] == ["2026-09", "2025-09"]
    assert f0["ew"] is None and f0["g3eb"] == "a" and f0["ni"] is not None and f0["ind"]
    so = next(s for s in d["stocks"] if s["s"] == other)
    assert so["sec"] == "Industrials" and so["ind"] == "Airlines" and "fts" not in so
    assert so["edate"] == "2026-10-30" and so["ecap"] == 321.0  # market cap from the calendar for sorting
    f3 = next(s for s in d["stocks"] if s["s"] == want[3])  # GAAP EPS from Nasdaq, dated by Yahoo's revenue
    assert f3["eqv"] == [1.5, 0.7] and f3["eqd"] == ["2026-09", "2025-09"]
    sa, sb = (next(s for s in d["stocks"] if s["s"] == t) for t in (a, b))
    assert sa["eold"] == "2026-10-21" and sa["edate"] is None  # passed, not confirmed: next date unknown
    assert "eold" not in sb and sb["erep"] == "2026-10-22" and sb["edate"] is None
    assert d["fcov"][0] >= d["fcov"][1] > 0
    # the front-page summary, the 50-day chart and the 市場寬度 bars print one number
    for key, hk in (("ndx", "n50"), ("usa", "b50")):
        b = d["breadth"][key]
        assert d["hist"][-1][hk] == round(b["a50"] / b["n"] * 100, 1), (key, d["hist"][-1][hk], b)
    from kcdt.checks import run
    checks = run(d, today="2026-10-28")
    assert checks and all(c["lv"] in (0, 1, 2) for c in checks)
    assert not [c for c in checks if c["lv"] == 2], checks
    by = {c.get("k"): c for c in checks}
    assert by["b50ndx"]["lv"] == 0 and by["late"]["n"] == sum(1 for s in d["stocks"] if s.get("fst")) >= 1
    assert by["fill"]["n"] == 1
    assert by["past"]["n"] >= 1 and by["pm"]["lv"] == 1  # no 盤前需知 in this build
    assert len(d["leaders"]) == 30 and set(d["leaders"]) <= set(DOW30)
    pages = stock_pages(px, d["stocks"])
    nv = pages[d["stocks"][0]["s"]]
    assert len(pages) == 1105 and len(nv["d"]) == 260 and nv["d"][-1] == "2026-10-27"
    assert nv["m200"][-1] is not None and len(nv["v"]) == 260
    return d, pages


if __name__ == "__main__":
    test_nasdaq_qend()
    import json, pathlib, sys
    from kcdt import __main__ as m
    d, pages = test_build()
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    ev = lambda h, e, a, c, p: dict(d=(now + dt.timedelta(hours=h)).strftime("%Y-%m-%d"),
                                    u=(now + dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%MZ"), e=e, a=a, c=c, p=p)
    d["econ"] = [ev(-50, "GDP Price Index", "2.1%", "2.0%", "1.9%"), ev(-49, "Crude Oil Inventories", "-1.9M", "0.1M", "-3.1M"),
                 {**ev(-3, "Fed Interest Rate Decision", None, "4.00%", "4.00%"), "note": "議息會議結果", "imp": 3}, {**ev(-3, "Redbook", "9.3%", None, "9.0%"), "note": "截至 10/24 嗰星期 · 大型零售商同店銷售按年"},
                 {**ev(20, "Core PCE Price Index", None, "0.3%", "0.2%"), "per": "按月", "ref": "2026 年 9 月", "imp": 3},
                 {**ev(20, "Core PCE Price Index", None, "3.1%", "3.0%"), "per": "按年", "ref": "2026 年 9 月", "imp": 3},
                 {**ev(80, "CPI", None, "0.2%", "0.3%"), "unconf": True, "ref": "2026 年 10 月"},
                 {**ev(80, "CPI", None, "2.9%", "3.0%"), "unconf": True, "ref": "2026 年 10 月"},
                 {**ev(-4, "FOMC Statement", None, None, None), "note": "議息會議結果", "imp": 3, "link": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm"},
                 {**ev(-3.5, "FOMC Press Conference", None, None, None), "note": "議息會議後記者會", "imp": 3, "link": "https://www.federalreserve.gov/monetarypolicy/fomcpresconf20261028.htm"},
                 ev(30, "Initial Jobless Claims", "198K", "198K", "194K"), ev(70, "Natural Gas Storage", None, "75B", "77B")]
    d["news"] = [dict(t="2026-10-28T09:00:00Z", title="Stocks rally as <tech> leads", link="https://example.com/a", src="Nasdaq", cat="Markets"),
                 dict(t="2026-10-28T08:00:00Z", title="MSFT Q1 2027 Earnings Call Transcript", link="https://example.com/b", src="Motley Fool", cat="Earnings"),
                 dict(t="2026-10-28T07:00:00Z", title="Chip stocks climb", link="https://example.com/c", src="Nasdaq", cat="Technology")]
    d["news_t"] = d["built"] = (now - dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%MZ")
    from kcdt.checks import run as run_checks
    d["checks"] = run_checks(d)
    m.OUT = pathlib.Path("site")
    m.OUT.mkdir(exist_ok=True)
    m.write_page(d)
    (m.OUT / "data.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    # The intraday refresh rebuilds the page from data.json; an empty fetch keeps the last good copy
    from kcdt import refresh
    refresh.economic_calendar, refresh.headlines = (lambda **k: []), (lambda: [])
    refresh.main()
    assert json.loads((m.OUT / "data.json").read_text())["econ"] == d["econ"]
    m.write_stock_pages(pages)
    # A past day in the history folder plus today's, so the 歷史 view has two dates to pick
    import tempfile
    hist = pathlib.Path(tempfile.mkdtemp())
    old = m.snapshot(d)
    past = (dt.date.fromisoformat(d["asof"]) - dt.timedelta(days=4)).isoformat()
    old.update(d=past, kc=old["kc"][:3], kc_in=old["kc"][:1] and [old["kc"][0][0]], kc_out=["ZZZ"])
    (hist / f"{past}.json").write_text(json.dumps(old, ensure_ascii=False))
    m.keep_history(d, hist)
    days = json.loads((m.OUT / "h" / "index.json").read_text())
    assert days[0] == past and days[-1] == d["asof"], days
    # A fake 盤前需知 so the page has something to show
    from kcdt import premarket as pm
    day = lambda v, p: dict(v=v, d=d["asof"], chg=round(v - p, 2), pct=round((v / p - 1) * 100, 2))
    mkt = [dict(k="VIX", n="VIX 恐慌指數", sym="^VIX", link=pm.QUOTE + "^VIX", eod=True, **day(17.2, 16.91)),
           dict(k="US10Y", n="美國 10 年期國債息率", u="%", src="US Treasury", link="https://home.treasury.gov/", eod=True, **day(4.98, 5.05))]
    world = [dict(k="HSI", n="恒生指數", sym="^HSI", link=pm.QUOTE + "^HSI", eod=True, **day(22958.65, 23094.83))]
    st = d["stocks"]
    idx = [dict(k="NDX", n="Nasdaq-100 指數", sym="^NDX", link=pm.QUOTE + "^NDX", eod=True, **day(25000.5, 25300.1)),
           dict(k="SPX", n="標普 500 指數", sym="^GSPC", link=pm.QUOTE + "^GSPC", eod=True, **day(6800.2, 6790.0))]
    etf = [dict(k="QQQ", n="Nasdaq-100 ETF", link=pm.QUOTE + "QQQ", c=610.0, v=605.2, pct=-0.79, t=d["built"], when="pre")]
    brief = dict(d=pd.Timestamp(now).tz_convert("America/New_York").strftime("%Y-%m-%d"), t=d["built"], asof=d["asof"], idx=idx, etf=etf, mkt=mkt, world=world, econ=d["econ"],
                 earn_today=[dict(s=x["s"], n=x.get("n"), tm=tm, est=1.23, ndx=bool(x.get("ndx")), mcap=x.get("mcap"))
                             for x, tm in zip([x for x in st if x.get("ndx")][:5], ["bmo", "amc", "amc", None, "bmo"])],
                 earn_done=[dict(s=x["s"], n=x.get("n"), d=d["asof"], tm="amc", ndx=True, mcap=x.get("mcap"), est=1.96, rep=1.89, sur=-3.54)
                            for x in [x for x in st if x.get("ndx")][5:7]],
                 movers={x["s"]: dict(c=x["c"], v=round(x["c"] * (1 + (i % 9 - 4) / 100), 2), pct=float(i % 9 - 4),
                                      t=d["built"], when="pre") for i, x in enumerate([x for x in st if x.get("ndx")])},
                 score=[38, 50])
    pm.publish(brief, hist)
    assert json.loads((m.OUT / "pm" / "index.json").read_text()) == [brief["d"]]
    assert m.keep_history(d, hist) == (brief["t"], brief["d"])  # the next daily build carries the briefs over
    d["pm_t"], d["pm_d"] = brief["t"], brief["d"]
    m.write_page(d)
    print("ok", d["asof"], d["hist"][-1]["score"], len(d["stocks"]), d["breadth"])


def test_breadth_ignores_missing_closes():
    """A stock with no close on the last day is left out of the % series, as it is out of the counts."""
    universe = [f"S{i:04d}" for i in range(300)]
    tickers = sorted(set(universe + DOW30 + [t for t, _ in INDICES] + [t for t, _ in SECTORS] + ["^NDX", "^VXN", "QQQ"]))
    px = fake_prices(tickers, seed=3)
    for k in px:
        px[k].iloc[-1, px[k].columns.get_loc("S0005")] = np.nan  # a Nasdaq-100 member without today's bar
    d = build(px, fake_meta(universe), {}, None, {}, {}, {}, {})
    b = d["breadth"]["ndx"]
    assert d["hist"][-1]["n50"] == round(b["a50"] / b["n"] * 100, 1)


def test_checks_helpers():
    from kcdt.checks import past_due, pm_expected
    ny = "America/New_York"
    assert pm_expected(pd.Timestamp("2026-11-09 08:00", tz=ny)) == "2026-11-06"  # Monday before 09:30 -> Friday's
    assert pm_expected(pd.Timestamp("2026-11-09 10:00", tz=ny)) == "2026-11-09"
    assert pm_expected(pd.Timestamp("2026-11-08 20:00", tz=ny)) == "2026-11-06"  # Sunday
    st = [dict(s="A", eold="2026-11-03"), dict(s="B", eold="2026-11-03", erep="2026-11-04"),
          dict(s="C", edate="2026-11-06"), dict(s="D", edate="2026-11-06", econf=True), dict(s="E", edate="2026-11-09")]
    assert past_due(st, "2026-11-06") == ["A"]  # C is due on the day itself: not counted until the day after


def test_old_yahoo_dates_are_not_past_due():
    """Yahoo keeps the last report's date: fetched after it, a set date is the last report and an old guess is nothing."""
    universe = [f"S{i:04d}" for i in range(300)]
    tickers = sorted(set(universe + DOW30 + [t for t, _ in INDICES] + [t for t, _ in SECTORS] + ["^NDX", "^VXN", "QQQ"]))
    px = fake_prices(tickers, seed=4)  # last day 2026-10-27
    ts = lambda d: pd.Timestamp(d + " 16:00", tz="America/New_York").timestamp()
    seen = ts("2026-10-25")
    yd = {"S0001": {"ts": ts("2025-03-07"), "est": False, "_ts": seen},   # closed-end fund: last report long ago
          "S0002": {"ts": ts("2025-09-01"), "est": True, "_ts": seen},    # an old guess
          "S0003": {"ts": ts("2026-10-22"), "est": True, "_ts": seen},    # a guess already past when fetched
          "S0004": {"ts": ts("2026-10-22"), "est": True, "_ts": ts("2026-10-20")},  # fetched before it: expected, missed
          "S0005": {"ts": ts("2026-10-22"), "est": False, "_ts": ts("2026-10-20")},  # the company's own date: reported
          "S0006": {"ts": ts("2026-10-27"), "est": True, "_ts": ts("2026-10-20")},   # a guess for today
          "S0007": {"ts": ts("2026-11-03"), "est": True, "_ts": ts("2026-10-20")}}   # a guess a week after a report
    d = build(px, fake_meta(universe), {}, None, {}, {"S0007": "2026-10-26"}, {}, yd)
    s = {x["s"]: x for x in d["stocks"]}
    assert s["S0001"]["erep"] == "2025-03-07" and "eold" not in s["S0001"] and not s["S0001"].get("edate")
    assert "eold" not in s["S0002"] and not s["S0002"].get("erep")
    assert "eold" not in s["S0003"] and s["S0004"]["eold"] == "2026-10-22"
    assert s["S0005"]["erep"] == "2026-10-22" and "eold" not in s["S0005"]
    assert s["S0006"]["edate"] == "2026-10-27" and s["S0007"]["erep"] == "2026-10-26" and not s["S0007"].get("edate")
    from kcdt.checks import past_due
    assert past_due(d["stocks"], d["asof"]) == ["S0004"]


def test_nasdaq_qend_by_eps_when_revenue_differs():
    from kcdt.compute import nasdaq_qend
    bank = dict(qend="2026-09-30", revq=[381e6, 413e6], epsq=[0.94, 1.11, None, None, None],
                nrev=[555e6, 577e6], neps=[0.94, 1.11, 0.84, 0.81, 0.67])
    assert nasdaq_qend(bank) == "2026-09-30"
    assert nasdaq_qend({**bank, "neps": [1.11, 0.84, 0.81]}) is None  # Nasdaq a quarter behind: only one EPS to compare
    assert nasdaq_qend({**bank, "epsq": [1.3, 0.94, 1.11], "neps": [0.94, 1.11, 0.84]}) == "2026-06-30"


def test_nasdaq_qend_by_revenue_cut_to_millions():
    from kcdt.compute import fundamentals_row, nasdaq_qend
    # basic against diluted EPS (1.01 / 0.99) but two revenue quarters agree
    hto = dict(qend="2026-09-30", revq=[250229000.0, 210474000.0, 183293000.0], epsq=[0.99, 0.62, 0.49, 0.45, None],
               nrev=[250e6, 210e6, 183e6], neps=[1.01, 0.63, 0.49, 0.44, 1.28])
    assert nasdaq_qend(hto) == "2026-09-30"
    assert fundamentals_row({**hto, "mcap": 3e9})["eqd"] == ["2026-09", "2025-09"]
    # Nasdaq a quarter behind, $51.642M shown as 51,000,000
    prsu = dict(qend="2026-09-30", revq=[251530000.0, 133493000.0, 51642000.0], epsq=[2.95, 0.54, -0.9, -0.91, None],
                nrev=[133e6, 51e6, 57e6], neps=[0.55, None, None, 2.6, 0.21])
    assert nasdaq_qend(prsu) == "2026-06-30"
    assert fundamentals_row({**prsu, "mcap": 1e9})["eqd"] == ["2026-06", "2025-06"]
    assert nasdaq_qend({**prsu, "nrev": [134e6, 51e6]}) is None  # a million above: not the same quarter


def test_issuer_accounts_are_not_a_notes_eps():
    from kcdt.compute import fundamentals_row
    rzc = dict(mcap=None, ind=None, _v=2, nrev=[6637e6] * 8, neps=[7.01, 4.98, 6.91, 3.81, 2.7, 4.27, 2.2, 2.34])
    row = fundamentals_row(rzc)
    assert row["eqv"] is None and row["g3r"] is None and row["g3e"] is None


def test_fund_stale_by_quarter():
    from kcdt.checks import fund_stale
    assert not fund_stale(dict(erep="2026-11-05", fts="2026-11-05", qend="2026-09-30"))  # fetched on the day, has Q3
    assert fund_stale(dict(erep="2026-11-05", fts="2026-11-06", qend="2026-06-30"))      # fetched after, still Q2
    assert not fund_stale(dict(erep="2026-09-28", fts="2026-09-01", qend="2026-06-30"))  # 90 days late filer, current
    assert fund_stale(dict(erep="2026-11-05", fts="2026-11-01"))  # no quarter date: old rule
    assert not fund_stale(dict(erep="2026-11-05", qend="2026-06-30"))  # never fetched


def test_premarket_marks_bars_still_trading():
    from kcdt.premarket import still_open
    now = pd.Timestamp("2026-11-05 05:47", tz="America/New_York")  # 18:47 Hong Kong
    assert still_open("CL=F", "2026-11-05", now) and still_open("^VIX", "2026-11-05", now)
    assert still_open("^GDAXI", "2026-11-05", now)  # Frankfurt closes 17:30
    assert not still_open("^HSI", "2026-11-05", now) and not still_open("^N225", "2026-11-05", now)
    assert not still_open("^NDX", "2026-11-04", now)


def test_yahoo_report_time_in_new_york():
    from kcdt.compute import ny_day, ny_session, yahoo_next
    nov = pd.Timestamp("2026-11-17 21:00", tz="UTC").timestamp()     # 16:00 New York (winter, UTC-5)
    jul = pd.Timestamp("2026-07-28 20:05", tz="UTC").timestamp()     # 16:05 New York (summer, UTC-4)
    late = pd.Timestamp("2026-11-18 01:00", tz="UTC").timestamp()    # 20:00 New York on the 17th
    pre = pd.Timestamp("2026-11-18 13:30", tz="UTC").timestamp()     # 08:30 New York
    mid = pd.Timestamp("2026-11-19 05:00", tz="UTC").timestamp()     # midnight New York: date only
    assert ny_session(nov) == "amc" and ny_session(jul) == "amc" and ny_session(pre) == "bmo"
    assert ny_day(late) == "2026-11-17" and ny_session(late) == "amc"
    assert ny_session(mid) is None and ny_day(mid) == "2026-11-19"
    # a fundamentals copy from before the next date was out loses to a newer earnings-date check
    f = {"edate": pd.Timestamp("2026-08-27 20:05", tz="UTC").timestamp(), "_ts": pre - 30 * 86400}
    assert yahoo_next(f, {"ts": nov, "est": False, "_ts": pre - 86400}) [:3] == ("2026-11-17", "amc", True)
    assert yahoo_next(f, {"ts": nov, "est": True, "_ts": pre - 86400})[:3] == ("2026-11-17", None, False)  # a guess
    assert yahoo_next({"edate": nov, "eest": False, "_ts": 1}, None)[:3] == ("2026-11-17", "amc", True)
    assert yahoo_next({"edate": nov, "_ts": 1}, None)[:3] == ("2026-11-17", None, False)  # older copy: no flag
    assert yahoo_next(None, {"ts": nov, "est": False, "_ts": nov + 5 * 86400})[3] == "2026-11-22"  # fetched after it
