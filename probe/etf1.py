import json, re, sys, urllib.request
sys.path.insert(0, ".")
import requests
import pandas as pd
from kcdt.fetch import NASDAQ_UA
try:
    r = requests.get("https://api.nasdaq.com/api/screener/etf?tableonly=true&limit=10000&download=true", headers=NASDAQ_UA, timeout=60)
    print("etf status", r.status_code, len(r.content))
    j = r.json(); data = j.get("data") or {}
    print("data keys", list(data.keys()))
    rows = (data.get("data") or {}).get("rows") or data.get("rows") or (data.get("table") or {}).get("rows") or []
    print("nrows", len(rows)); print(rows[:3])
    kw = re.compile(r"(\b[0-9.]+X\b|LEVERAGED|INVERSE|\bBULL\b|\bBEAR\b|\bLONG\b|\bSHORT\b|OPTION INCOME|YIELD|COVERED CALL|DAILY TARGET|\bDAILY\b)", re.I)
    hits = [x for x in rows if kw.search(x.get("companyName") or x.get("name") or "")]
    print("keyword hits", len(hits))
    for x in hits: print("ETF|" + (x.get("symbol") or "") + "|" + (x.get("companyName") or x.get("name") or ""))
except Exception as e:
    print("etf fail", e)
for sym in ("CLMB", "NVDL", "NVD"):
    try:
        y = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=5m&includePrePost=true", headers={"User-Agent": "Mozilla/5.0"}, timeout=30).json()["chart"]["result"][0]
        print("Y", sym, y["meta"].get("longName"), y["meta"].get("instrumentType"), y["meta"].get("regularMarketPrice"), len(y.get("timestamp") or []))
        if sym == "CLMB":
            q = y["indicators"]["quote"][0]
            df = pd.DataFrame(q, index=pd.to_datetime(y["timestamp"], unit="s", utc=True).tz_convert("America/New_York"))
            for d, g in df.groupby(df.index.date):
                rn = g.between_time("09:30", "15:59").dropna(subset=["close"])
                vw = ((rn.high + rn.low + rn.close) / 3 * rn.volume).sum() / max(rn.volume.sum(), 1)
                print("CLMB", d, "bars", len(g), "reg nonnull", len(rn), "first", rn.index[0].strftime("%H:%M") if len(rn) else None, "lastbar", rn.index[-1].strftime("%H:%M") if len(rn) else None,
                      "open", rn.open.iloc[0] if len(rn) else None, "hi", rn.high.max(), "lo", rn.low.min(), "vol", rn.volume.sum(), "vwap", round(vw, 4), "last", rn.close.iloc[-1] if len(rn) else None)
                if str(d) == "2026-11-12": print(rn.head(8).to_string()); print(rn.tail(4).to_string())
            dd = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/CLMB?range=10d&interval=1d", headers={"User-Agent": "Mozilla/5.0"}, timeout=30).json()["chart"]["result"][0]
            qd = dd["indicators"]["quote"][0]
            for i, t in enumerate(dd["timestamp"]):
                print("CLMB daily", pd.Timestamp(t, unit="s").date(), qd["open"][i], qd["high"][i], qd["low"][i], qd["close"][i], qd["volume"][i])
    except Exception as e:
        print("Y fail", sym, e)
try:
    d = json.load(urllib.request.urlopen("https://kcxdt.github.io/data.json", timeout=30))
    print("asof", d["asof"], "checks", json.dumps(d.get("checks"), ensure_ascii=False)[:3000])
    print("ndx breadth", d["breadth"]["ndx"], "tech pct50", d["tech"]["hist"][-1]["v"]["pct50"], "hist n50", d["hist"][-1].get("n50"))
    print("keys", list(d.keys()))
except Exception as e:
    print("site fail", e)
