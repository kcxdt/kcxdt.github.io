import json, pathlib, urllib.request, sys
sys.path.insert(0, ".")
import pandas as pd
from kcdt.checks import fund_stale, past_due
d = json.load(urllib.request.urlopen("https://kcxdt.github.io/data.json", timeout=30))
st = d["stocks"]; asof = d["asof"]
F = json.loads(pathlib.Path("cache/fundamentals.json").read_text())
NQ = json.loads(pathlib.Path("cache/nasdaq_quarters.json").read_text())
try: YD = json.loads(pathlib.Path("cache/earnings_dates.json").read_text())
except Exception: YD = {}
try: DONE = json.loads(pathlib.Path("cache/earnings_done.json").read_text())
except Exception: DONE = {}
ts = lambda x: pd.Timestamp(x, unit="s", tz="UTC").strftime("%m-%d %H:%M") if x else None
print("asof", asof, "checks", [c for c in d["checks"] if c.get("k") in ("late", "past")])
print("=== late")
for s in st:
    if fund_stale(s):
        f = F.get(s["s"], {})
        print(s["s"], "erep", s.get("erep"), "fts", s.get("fts"), "qend", s.get("qend"), "edate", s.get("edate"), "mcap", s.get("mcap"),
              "| F qend", f.get("qend"), "_ts", ts(f.get("_ts")), "erepF", ts(f.get("erep")), "edateF", ts(f.get("edate")), "eest", f.get("eest"),
              "revq", [round(x/1e6) if x else x for x in (f.get("revq") or [])[:3]], "nrev", [round(x/1e6) if x else x for x in (NQ.get(s["s"], {}).get("nrev") or [])[:3]], "done", DONE.get(s["s"]))
print("=== past")
pd_ = set(past_due(st, asof))
for s in st:
    if s["s"] in pd_:
        f = F.get(s["s"], {}); y = YD.get(s["s"], {})
        print(s["s"], {k: s.get(k) for k in ("eold", "edate", "econf", "erep", "et", "esrc", "mcap", "fts")},
              "| F edate", ts(f.get("edate")), "eest", f.get("eest"), "_ts", ts(f.get("_ts")), "| YD", {k: (ts(v) if k in ("ts", "_ts") else v) for k, v in y.items()}, "done", DONE.get(s["s"]))
