import json, sys, urllib.request
sys.path.insert(0, ".")
from kcdt.etfs import fetch_rows, single_stock
d = json.load(urllib.request.urlopen("https://kcxdt.github.io/data.json", timeout=30))
pool = [s["s"] for s in d["stocks"]]
print("POOL|" + ",".join(pool))
rows = fetch_rows()
print("rows", len(rows))
print("ROWS|" + json.dumps(rows))
m = single_stock(rows, set(pool))
print("n under", len(m), "n funds", sum(len(v) for v in m.values()))
for u, v in sorted(m.items(), key=lambda kv: -len(kv[1])):
    print("MAP|" + u + "|" + " ; ".join(f"{e[0]} {e[2]}{e[3]} {e[1]}" for e in v))
