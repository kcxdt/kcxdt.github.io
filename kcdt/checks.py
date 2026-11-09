"""Checks run on every build; the results are shown on 說明及數據狀態 (nothing here stops a publish)."""
import pandas as pd

KC_FIELDS = ("mcap", "sq", "sa", "inst", "beta")  # fundamentals KC's default screener reads


def coverage(stocks):
    """(stocks with any fundamentals, stocks with every field the default screener needs)."""
    part = sum(1 for s in stocks if s.get("fts"))
    full = sum(1 for s in stocks if all(s.get(k) is not None for k in KC_FIELDS))
    return part, full


def fund_late(stocks):
    """Reported after the fundamentals were last fetched: the numbers are still last quarter's (業績後待更新)."""
    return [s["s"] for s in stocks if s.get("erep") and s.get("fts") and s["erep"] >= s["fts"]]


def past_due(stocks, asof):
    """Expected report dates up to `asof` with no confirmed report, as the page counts them at the end of that day:
    dates that had already passed at the build (eold), plus dates on `asof` itself that nobody confirmed."""
    out = []
    for s in stocks:
        d = s.get("eold") or (s.get("edate") if s.get("edate") == asof and not s.get("econf") else None)
        if d and not (s.get("erep") and s["erep"] >= d):
            out.append(s["s"])
    return out


def pm_expected(now):
    """The New York date of the 盤前需知 that should exist at `now` (New York time): today from 09:30 on a weekday
    (the brief runs 05:50 with backups until 09:30), else the weekday before."""
    now = pd.Timestamp(now)
    d = now.normalize()
    if now.weekday() >= 5 or now.hour * 60 + now.minute < 570:
        d -= pd.offsets.BDay(1)
    return d.strftime("%Y-%m-%d")


def run(data, today=None, now=None):
    """[{lv: 0 ok / 1 look / 2 problem, t: what was checked and what was found, k: key, n: count}]
    k/n let the page compare its own numbers (status table, front-page summary) with these."""
    out = []
    add = lambda lv, t, k=None, n=None: out.append({x: y for x, y in dict(lv=lv, t=t, k=k, n=n).items() if y is not None})
    st = data["stocks"]
    n = len(st)

    part, full = coverage(st)
    add(0 if full >= part * 0.8 else 1,
        f"基本面覆蓋：{part:,} 隻有部分資料，{full:,} 隻齊備預設篩選所需資料（市值、營收增長、機構持股、Beta）")

    ind = sum(1 for s in st if s.get("ind"))
    add(0 if ind >= n * 0.95 else 1, f"細分行業（Industry）：{ind:,} / {n:,} 隻有資料（新股每日補）")
    eqn = sum(1 for s in st if s.get("eqv"))
    eqd = sum(1 for s in st if s.get("eqv") and s.get("eqd"))
    add(0 if eqd >= eqn * 0.9 else 1, f"EPS 增長標明比較邊兩季：{eqd:,} / {eqn:,} 隻")

    today = pd.Timestamp(today or pd.Timestamp.now(tz="America/New_York").date())
    lag = len(pd.bdate_range(pd.Timestamp(data["asof"]), today)) - 1
    add(0 if lag <= 1 else 2, f"價格日期：{data['asof']}，" + ("係最近一個交易日" if lag <= 1 else f"落後 {lag} 個交易日"))
    late = len(fund_late(st))
    add(0 if late <= 50 else 1, f"出咗業績但基本面未更新：{late} 隻（選股器代號旁邊標「業績後待更新」，每日優先更新）",
        "late", late)
    old = len(past_due(st, data["asof"]))
    add(0 if old <= 30 else 1, f"預計業績日已過但未確認公布：{old} 隻（計到 {data['asof']} 為止；下次業績日顯示「未知」）",
        "past", old)
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="America/New_York")
    want, pm = pm_expected(now), data.get("pm_d")
    add(0 if pm and pm >= want else 1,
        f"盤前需知：最新報告 {pm or '未有'}，" + ("係應有嘅最新一份" if pm and pm >= want else f"應該有 {want} 嘅報告"), "pm")
    for key, name in (("econ", "經濟數據"), ("news", "市場新聞")):
        if key in data:
            add(0 if data.get(key) else 1, f"{name}：{len(data.get(key) or [])} 項")
    if data.get("fng") is not None:
        add(0 if data["fng"].get("live") else 1, "恐懼與貪婪指數：" + ("今日數據" if data["fng"].get("live") else
                                                                f"拎唔到最新，顯示 {data['fng'].get('ts')} 嘅數值"))

    big = sorted((s for s in st if s.get("chg") is not None and abs(s["chg"]) >= 50), key=lambda s: -abs(s["chg"]))
    add(0 if not big else 1, "單日升跌 50% 以上：" + ("冇" if not big else
                                                  "、".join(f"{s['s']} {s['chg']:+.0f}%" for s in big[:6]) + "（請核對係咪拆股或數據錯誤）"))
    bad = [s["s"] for s in st if not s.get("c") or s["c"] <= 0 or not 1 <= s.get("rsr", 0) <= 99]
    add(0 if not bad else 2, "價格同 RS 數值：" + ("正常" if not bad else f"{len(bad)} 隻異常，例如 {'、'.join(bad[:6])}"))
    flagged = sum(1 for s in st if set(s.get("ew") or []) & {"turn", "base"})
    add(0, f"EPS 增長百分比要小心睇（轉虧為盈或基數接近零）：{flagged} 隻，表入面有 ⚠ 標示")

    h = data["hist"][-1]
    ok = h["score"] == sum(h["p"].values())
    if data.get("tech"):
        t = data["tech"]["hist"][-1]
        ok = ok and t["score"] == sum(t["p"].values())
    add(0 if ok else 2, "風險分摘要同 8 項指標明細：" + ("一致" if ok else "唔一致"))
    # what the front page prints (市場重點, 市場寬度 bars, 50 日線 chart) must be the same number
    for key, hk, name in (("ndx", "n50", "Nasdaq-100"), ("usa", "b50", "全美股")):
        b = data["breadth"][key]
        want = round(b["a50"] / b["n"] * 100, 1) if b["n"] else None
        got = h.get(hk)
        add(0 if got == want else 2, f"{name} 企 50 日線上：市場重點同走勢圖 {got}%，市場寬度 {want}%（{b['a50']}／{b['n']}）"
            + ("，一致" if got == want else "，唔一致"), "b50" + key, got)
    b = data["breadth"]["usa"]
    # a50 counts closes above the line before rounding, so a d50 shown as 0.0 may be either side
    ok = b["n"] == n and b["adv"] == sum(1 for s in st if (s["chg"] or 0) > 0) and \
        sum(1 for s in st if s["d50"] is not None and s["d50"] > 0) <= b["a50"] <= \
        sum(1 for s in st if s["d50"] is not None and s["d50"] >= 0)
    add(0 if ok else 2, "市場寬度摘要同個股明細：" + ("一致" if ok else "唔一致"))
    ok = data.get("fcov") == [part, full]
    add(0 if ok else 2, "選股器同數據狀態頁嘅基本面數目：" + ("一致" if ok else "唔一致"))
    return out
