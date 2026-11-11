"""Economic calendar labels: which period a release covers, and whether a % is m/m, y/y or q/q.

Nasdaq's calendar gives neither, so:
- m/m, y/y and q/q come from ForexFactory's weekly calendar, whose titles say it ("Core PCE Price Index m/m"),
  matched to Nasdaq's rows by release time and forecast. ForexFactory only publishes the current week, so the
  numbers of confirmed rows are remembered (cache/econ_labels.json): a later release's "previous" is the
  confirmed actual. Rows that still can't be told apart are flagged unconfirmed, never guessed.
- The reference period follows each release's fixed schedule (CPI in November covers October, GDP covers
  the last finished quarter, jobless claims cover the week to last Saturday). Events with no period
  (rate decisions, auctions, speeches) get a short note on what they are instead.
"""
import re

import pandas as pd
import requests

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
PER = {"m/m": "按月", "y/y": "按年", "q/q": "按季（年率）"}
BLS_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
# Releases ForexFactory doesn't list in both versions: the BLS index behind each, (seasonally adjusted for the
# monthly change, unadjusted for the 12-month change), which is how BLS itself quotes them.
BLS_SERIES = [
    (r"^ppi ex\.? ?food/energy/transport$", "WPSFD49116", "WPUFD49116"),  # final demand less foods, energy, trade services
    (r"^core ppi$", "WPSFD49104", "WPUFD49104"),
    (r"^ppi$", "WPSFD4", "WPUFD4"),
    (r"^import price index$", "EIUIR", "EIUIR"),
    (r"^export price index$", "EIUIQ", "EIUIQ"),
]
MOM_OF_LEVEL = r"^(existing home sales|new home sales|building permits|housing starts)$"

# Events that have no reference period: say what they are. A callable gets the release date.
NOTES = [
    (r"FOMC Statement|Fed Interest Rate Decision", "議息會議結果"),
    (r"FOMC Press Conference", "議息會議後記者會"),
    (r"FOMC.*Minutes", "上次議息會議紀錄"),
    (r"Speaks|Speech|Testimony", "聯儲官員講話"),
    (r"Auction", "國債拍賣 · 數字係得標息率"),
    (r"GDPNow", "亞特蘭大聯儲對今季 GDP 嘅即時估算（年率）"),
    (r"Fed's Balance Sheet|Reserve Balances", lambda d: f"截至 {_back(d, 2).month}/{_back(d, 2).day}（星期三）"),
    (r"Redbook", lambda d: f"截至 {_back(d, 5).month}/{_back(d, 5).day} 嗰星期 · 大型零售商同店銷售按年"),
    (r"ADP.*Weekly", "ADP 每週就業估算（4 星期平均）"),
]


# Official pages for events with no number, by the New York release date
LINKS = [
    (r"FOMC Statement", "https://www.federalreserve.gov/newsevents/pressreleases/monetary{ymd}a.htm"),
    (r"FOMC Press Conference", "https://www.federalreserve.gov/monetarypolicy/fomcpresconf{ymd}.htm"),
    (r"FOMC.*Minutes", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"),
]


# Where each release is published first, for rows the calendar hasn't updated yet
SOURCES = [
    (r"Jobless Claims|Unemployment Claims", "https://www.dol.gov/ui/data.pdf"),
    (r"Nonfarm|Payrolls|Unemployment Rate|Average Hourly|Average Weekly|Participation|CPI|PPI|JOLT|Employment Cost|"
     r"Productivity|Unit Labor|Import Price|Export Price", "https://www.bls.gov/bls/newsrels.htm"),
    (r"GDP|PCE|Personal Income|Personal Spending|Current Account|Corporate Profits", "https://www.bea.gov/news/current-releases"),
    (r"Retail Sales|Durable|Goods Orders|Factory Orders|Housing Starts|Building Permits|New Home Sales|Construction|"
     r"Business Inventories|Wholesale|Trade Balance|Retail Inventories", "https://www.census.gov/economic-indicators/"),
    (r"Industrial Production|Capacity Utilization", "https://www.federalreserve.gov/releases/g17/current/default.htm"),
    (r"^ISM", "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/"),
    (r"Michigan", "https://www.sca.isr.umich.edu/"),
    (r"CB Consumer Confidence", "https://www.conference-board.org/topics/consumer-confidence"),
    (r"Crude|Gasoline|Distillate|Cushing|Refinery", "https://www.eia.gov/petroleum/supply/weekly/"),
    (r"Natural Gas Storage", "https://ir.eia.gov/ngs/ngs.html"),
    (r"Existing Home Sales|Pending Home", "https://www.nar.realtor/research-and-statistics"),
]


def event_link(name, day):
    for pat, url in LINKS:
        if re.search(pat, name or "", re.I):
            return url.format(ymd=day.replace("-", ""))
    return None


def _back(d, weekday):
    """The last date before d that falls on weekday (Mon=0)."""
    return d - pd.Timedelta(days=(d.weekday() - weekday) % 7 or 7)


# (name pattern, rule): months back from the release month (or a function of the release date), "q" = last finished quarter,
# "w-sat"/"w-fri" = week ending the previous Saturday/Friday. Patterns are tried in order.
PERIOD_RULES = [
    (r"Jobless Claims|Unemployment Claims", "w-sat"),
    (r"Crude|Gasoline|Distillate|Heating Oil|Cushing|Refinery|EIA|Natural Gas Storage|MBA|Mortgage", "w-fri"),
    (r"^(Advance |Prelim |Final )?GDP|Employment Cost Index|Productivity|Unit Labor Costs|Current Account", "q"),
    (r"JOLT|House Price|HPI|Case-Shiller|S&P/CS|Factory Orders|Business Inventories|Construction Spending|"
     r"Consumer Credit|^Trade Balance", 2),
    (r"Wholesale Inventories", lambda d: 2 if d.day <= 20 else 1),  # final early in the month, advance at the end
    (r"CB Consumer Confidence|Michigan|Chicago PMI|Richmond|Philadelphia|Philly|Empire State|NY Empire|"
     r"Dallas Fed|Kansas City Fed|Flash|S&P Global", 0),
    (r"CPI|PPI|PCE|Personal Income|Personal Spending|Retail Sales|Nonfarm|Non-Farm|Unemployment Rate|"
     r"Average Hourly|Durable|Goods Orders|Industrial Production|Capacity Utilization|Housing Starts|"
     r"Building Permits|Home Sales|Goods Trade Balance|Inventories|ISM|Import Price|Export Price|"
     r"Chicago Fed National|ADP Nonfarm|Challenger|Budget|M2 Money", 1),
]


def event_note(name, day):
    """What an event without a reference period is (a rate decision, an auction...), or None."""
    for pat, note in NOTES:
        if re.search(pat, name or "", re.I):
            return note(pd.Timestamp(day)) if callable(note) else note
    return None


def ref_period(name, day):
    """Chinese label for the period a release covers, or None when the schedule isn't known."""
    d = pd.Timestamp(day)
    for pat, rule in PERIOD_RULES:
        if not re.search(pat, name or "", re.I):
            continue
        if rule == "q":
            q = (d.to_period("Q") - 1)
            return f"{q.year} 年第 {q.quarter} 季"
        if rule in ("w-sat", "w-fri"):
            end = _back(d, 5 if rule == "w-sat" else 4)
            return f"截至 {end.month}/{end.day} 嗰星期"
        m = d.to_period("M") - (rule(d) if callable(rule) else rule)
        return f"{m.year} 年 {m.month} 月"
    return None


def _num(x):
    try:
        return float(re.sub(r"[^0-9.\-]", "", x or ""))
    except ValueError:
        return None


def bls_changes(names):
    """The last few months' published changes of each named release, worked out from the BLS index itself:
    {name: {"按月": {0.5, ...}, "按年": {5.0, ...}}}. Names BLS has no series for, or a failed request, are left out."""
    want = {n: (sa, nsa) for n in names for pat, sa, nsa in BLS_SERIES if re.search(pat, n, re.I)}
    if not want:
        return {}
    year = pd.Timestamp.now().year
    try:
        r = requests.post(BLS_URL, json={"seriesid": sorted({x for v in want.values() for x in v}),
                                         "startyear": str(year - 2), "endyear": str(year)}, timeout=40)
        series = {x["seriesID"]: {(int(d["year"]), int(d["period"][1:])): float(d["value"]) for d in x["data"]
                                  if re.fullmatch(r"M(0[1-9]|1[0-2])", d["period"])}
                  for x in r.json()["Results"]["series"]}
    except Exception as e:
        print("bls failed:", e)
        return {}
    out = {}
    for n, (sa, nsa) in want.items():
        res = {"按月": set(), "按年": set()}
        for per, idx, back in (("按月", series.get(sa) or {}, 1), ("按年", series.get(nsa) or {}, 12)):
            for (y, m) in sorted(idx)[-4:]:
                m0 = y * 12 + m - 1 - back
                old = idx.get((m0 // 12, m0 % 12 + 1))
                if old:
                    res[per].add(round((idx[(y, m)] / old - 1) * 100, 1))
        out[n] = res
    print(f"bls: {len(out)} releases checked against the index")
    return out


def forexfactory():
    """This week's US rows from ForexFactory: [{t (UTC), title, impact, forecast, previous}], or [] on failure."""
    try:
        r = requests.get(FF_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        rows = r.json()
    except Exception as e:
        print("forexfactory failed:", e)
        return []
    out = []
    for x in rows:
        if x.get("country") != "USD":
            continue
        try:
            t = pd.Timestamp(x["date"]).tz_convert("UTC").strftime("%Y-%m-%dT%H:%MZ")
        except Exception:
            continue
        out.append(dict(t=t, title=x.get("title") or "", impact=x.get("impact"), f=x.get("forecast"), p=x.get("previous")))
    print(f"forexfactory: {len(out)} US rows this week")
    return out


def label(rows, ff, memory=None, official=None):
    """Add ref (period covered) or note (what the event is), per (按月/按年/按季), unconf (a % row whose
    按月/按年 couldn't be confirmed) and imp (3 high, 2 medium) in place.

    memory: {event name: {"v": {number: per}}}, the numbers of rows ForexFactory confirmed. A release's
    "previous" is the last release's actual, so next month's rows are told apart by that number. Nasdaq's
    row order can't be used: Core CPI came m/m first in February 2026 and y/y first in October.
    official: function(names) -> {name: {"按月": {numbers}, "按年": {numbers}}} (bls_changes), asked only for
    same-name % pairs the two steps above left without a label."""
    memory = {} if memory is None else memory
    for name in list(memory):  # drop the old row-order format
        if name != name.lower() or not isinstance(memory[name], dict) or not isinstance(memory[name].get("v"), dict):
            del memory[name]
    for r in rows:
        r["note"] = event_note(r.get("e"), r["d"])
        r["ref"] = None if r["note"] else ref_period(r.get("e"), r["d"])
        if event_link(r.get("e"), r["d"]):
            r["link"] = event_link(r.get("e"), r["d"])
        else:
            src = next((url for pat, url in SOURCES if re.search(pat, r.get("e") or "", re.I)), None)
            if src:
                r["src"] = src
    key = lambda r: (r.get("e") or "").lower()  # Nasdaq writes "PCE Price index" (y/y) and "PCE price index" (m/m)
    pct = lambda r: (r.get("c") or r.get("p") or r.get("a") or "").endswith("%")
    # 1) ForexFactory: same release time, and the forecast (or previous) is the same number
    for r in rows:
        best = None
        for x in ff:
            if x["t"] != r.get("u"):
                continue
            same = lambda a, b: a is not None and b is not None and abs(a - b) < 1e-6
            if same(_num(x["f"]), _num(r.get("c"))) or same(_num(x["p"]), _num(r.get("p"))):
                best = x
                break
        if best:
            m = re.search(r"\b(m/m|y/y|q/q)\b", best["title"])
            if m and pct(r):
                r["per"] = PER[m.group(1)]
            r["imp"] = {"High": 3, "Medium": 2}.get(best["impact"])
    # 2) Remembered numbers: this row's previous was a confirmed row's actual
    for r in rows:
        if not r.get("per") and pct(r) and r.get("p"):
            known = memory.get(key(r), {}).get("v", {}).get(r["p"])
            if known in PER.values():
                r["per"] = known
    groups = {}
    for r in rows:
        groups.setdefault((r["d"], key(r)), []).append(r)
    # 2b) The agency's own index: a number that is one of its recent monthly changes and none of its 12-month
    # changes is 按月, and the other way round. Both rows of a pair must come out different.
    open_ = sorted({k for (d, k), g in groups.items() if len(g) == 2 and all(pct(x) and not x.get("per") for x in g)})
    known = official(open_) if official and open_ else {}
    for (d, k), g in groups.items():
        if k not in known or len(g) != 2 or any(x.get("per") or not pct(x) for x in g):
            continue
        got = []
        for x in g:
            v = [n for n in (_num(x.get("p")), _num(x.get("a"))) if n is not None]
            hit = {per for per, nums in known[k].items() if v and all(round(n, 1) in nums for n in v)}
            got.append(hit.pop() if len(hit) == 1 else None)
        if None not in got and got[0] != got[1]:
            g[0]["per"], g[1]["per"] = got
    # 3) A same-name % pair where one side is known: the other is the opposite
    for g in groups.values():
        if len(g) == 2 and all(pct(x) for x in g):
            a, b = g
            for x, y in ((a, b), (b, a)):
                if x.get("per") in ("按月", "按年") and not y.get("per"):
                    y["per"] = "按年" if x["per"] == "按月" else "按月"
            if a.get("imp") or b.get("imp"):
                a["imp"] = b["imp"] = max(a.get("imp") or 0, b.get("imp") or 0) or None
    # 3b) Housing counts come as the level ("1.33M") plus one % row, which is the change from the month before
    for g in groups.values():
        p = [x for x in g if pct(x)]
        if len(g) > 1 and len(p) == 1 and not p[0].get("per") and re.search(MOM_OF_LEVEL, key(p[0]), re.I):
            p[0]["per"] = "按月"
    # 4) Remember the numbers of every known row; a number seen in both 按月 and 按年 rows is useless
    for r in rows:
        if r.get("per") and pct(r):
            vals = memory.setdefault(key(r), {"v": {}})["v"]
            for k in ("a", "p"):
                if r.get(k):
                    vals[r[k]] = r["per"] if vals.get(r[k]) in (None, r["per"]) else "?"
            while len(vals) > 24:
                vals.pop(next(iter(vals)))
    # 5) A % row that comes in more than one version but still has no label: say so instead of guessing
    for g in groups.values():
        if len(g) > 1:
            for r in g:
                if pct(r) and not r.get("per"):
                    r["unconf"] = True
    return rows
