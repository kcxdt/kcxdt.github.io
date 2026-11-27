"""Dated source records for gaps in the daily feeds. Dates are New York session dates."""
import json
import re
from pathlib import Path

import pandas as pd


SPECIAL = {
    "ITGR": ("2026-11-17", "Integer Holdings", "已被收購，普通股已停止交易", "https://infomemo.theocc.com/infomemos?number=60240"),
    "NWE": ("2026-11-16", "NorthWestern Energy", "已合併，每股 NWE 換 0.98 股 HZN", "https://ir.brighthorizonenergy.com/news-releases/news-release-details/black-hills-corp-and-northwestern-energy-complete-merger-forming"),
    "RZC": ("2022-09-23", "Reinsurance Group of America", "2052 年到期次級債券，股票 EPS 不適用", "https://www.sec.gov/Archives/edgar/data/898174/000119312522246694/d402501d424b2.htm"),
}


def instrument_type(ticker, name):
    if re.search(r"\b(debentures?|mortgage bonds?)\b", name, re.I) or (ticker == "CCZ" and "ZONES" in name):
        return "上市債務證券"
    if ticker == "GJS" and "STRATS" in name:
        return "結構性信託證券"
    if ticker == "FRMEP" and "Depository Shares" in name:
        return "優先股存託股份"
    return None


def security_directory(close, asof, names=None):
    archive = json.loads(Path(__file__).with_name("retired_closes.json").read_text())
    directory = {}
    for ticker, (date, name, label, url) in SPECIAL.items():
        if asof < date:
            continue
        history = archive.get(ticker, [])
        if ticker == "RZC" and ticker in close:
            history = [[d.strftime("%Y-%m-%d"), round(float(v), 2)] for d, v in close[ticker].dropna().tail(260).items()]
        directory[ticker] = dict(date=date, name=name, label=label, url=url, history=history)
    for ticker, name in (names.items() if names is not None else []):
        kind = instrument_type(ticker, name)
        if kind and ticker not in directory:
            quotes = close[ticker].dropna().tail(260) if ticker in close else {}
            directory[ticker] = dict(name=name, label=kind+"，普通股 EPS 不適用", date=None,
                                     url=f"https://www.nasdaq.com/market-activity/stocks/{ticker.lower()}",
                                     history=[[d.date().isoformat(), round(float(v), 2)] for d, v in quotes.items()])
    return directory


# Official statements. Figures have their own period and basis, separate from
# the screener's complete multi-quarter series (which can still be behind).
REPORTS = {
    "AX": ("2026-10-29", "2026-09-30", "銀行淨利息收入加非利息收入 USD 395.071 百萬", "稀釋 EPS USD 2.25", "https://investors.axosfinancial.com/news-events/press-releases/news-details/2026/Axos-Financial-Inc--Reports-First-Quarter-Fiscal-Year-2027-Results/default.aspx"),
    "BDX": ("2026-11-05", "2026-09-30", "持續經營收入 USD 5,274 百萬", "持續經營 GAAP 稀釋 EPS USD 2.52", "https://investors.bd.com/sec-filings/all-sec-filings/content/0001628280-26-070511/ex9919302026.htm"),
    "GSHD": ("2026-11-09", "2026-09-30", "初步收入 USD 108.818–110.818 百萬", "正式 EPS 待公布，公司正重列過往財報", "https://www.sec.gov/Archives/edgar/data/1726978/000172697826000072/prelim20260930-pressreleas.htm"),
    "ING": ("2026-10-29", "2026-09-30", "銀行總收入 EUR 6,227 百萬", "每股淨業績 EUR 0.65（普通股）", "https://ing.com/investors/financial-performance/quarterly-results/2026"),
    "NRIM": ("2026-10-15", "2026-09-30", "銀行淨利息收入加其他營運收入約 USD 59.7 百萬", "稀釋 EPS USD 0.63", "https://www.sec.gov/Archives/edgar/data/1163370/000116337026000032/exhibit991q32026.htm"),
    "NVDA": ("2026-11-17", "2026-10-25", "營收 USD 115.8 十億", "GAAP 稀釋 EPS USD 2.70；經調整 USD 2.68", "https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Financial-Results-for-Third-Quarter-Fiscal-2027/"),
}


def apply_issuer_report(row, asof):
    ticker = row["s"]
    if ticker == "BRC" and asof >= "2026-11-16" and row.get("erep") == "2026-11-16" and (row.get("qend") or "") <= "2026-07-31":
        row.update(erep="2026-09-03", eold="2026-11-16", issuerPending="https://investors.bradycorp.com/")
        row.pop("lastSession", None)
    # Brady announced its Q1 release date on Nov 18. The 08:30 ET time is the call.
    if ticker == "BRC" and "2026-11-18" <= asof <= "2026-12-08" and (row.get("erep") or "") < "2026-12-08":
        if not row.get("econf") or (row.get("edate") or "") <= "2026-12-08":
            row.update(edate="2026-12-08", econf=True, etime=None,
                       scheduleSource="https://investors.bradycorp.com/news-releases/news-release-details/brady-corporation-announces-first-quarter-2027-earnings")
            for key in ("edend", "emonth", "issuerPending", "eold"):
                row.pop(key, None)
    record = REPORTS.get(ticker)
    if not record:
        return
    date, quarter, revenue, eps, url = record
    if date > asof or date < (row.get("erep") or "") or quarter < (row.get("qend") or ""):
        return
    row.update(erep=date, issuer=dict(date=date, quarter=quarter, revenue=revenue, eps=eps, url=url))


# Confirmed Nasdaq regular-session OHLCV. Apply only to a missing day, and only
# with an overlapping close to reconcile the adjusted basis of the price panel.
BARS = {
    "SNDK": ("2026-11-24", (82.715, 83.82, 74.83, 77.95, 97538190), "2026-11-25", 73.61),
    "EMBJ": ("2026-11-16", (19.39, 19.80, 18.66, 18.77, 4330378), "2026-11-17", 18.90),
    "WLYB": ("2026-11-17", (49.87, 50.53, 49.87, 50.37, 2028), "2026-11-16", 50.02),
}


def supplement_prices(px):
    close = px["Close"]
    for ticker, (day, bar, anchor, expected) in BARS.items():
        if ticker not in close or day not in close.index or anchor not in close.index:
            continue
        if pd.notna(close.at[day, ticker]) or pd.isna(close.at[anchor, ticker]):
            continue
        factor = close.at[anchor, ticker] / expected
        if not 0.99 <= factor <= 1.01:
            continue
        if ticker == "EMBJ" and "2026-11-13" in close.index and close.at["2026-11-13", ticker] > 40 * factor:
            continue  # the split still needs to be reconciled upstream
        close.attrs.setdefault("verified_bars", {})[ticker] = {
            "day": day, "url": f"https://www.nasdaq.com/market-activity/stocks/{ticker.lower()}/historical"}
        for field, value in zip(("Open", "High", "Low", "Close", "Volume"), bar):
            px[field].at[day, ticker] = value if field == "Volume" else value * factor
