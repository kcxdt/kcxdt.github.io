"""python -m kcdt  ->  writes site/index.html and site/data.json"""
import json
import os
import pathlib
import sys
import time
from datetime import datetime, timezone

from .checks import run as run_checks
from .compute import build, stock_pages, wants_fundamentals
from .fetch import (earnings_calendar, earnings_dates, economic_calendar, fear_greed, fetch_prices, fundamentals, headlines, profiles,
                    nasdaq_quarters, universe)

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "site"


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def render(data):
    tpl = (pathlib.Path(__file__).parent / "template.html").read_text(encoding="utf-8")
    body = tpl.replace("/*DATA*/", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    head = ('<!doctype html><html lang="zh-HK"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            "<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}"
            "body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style></head><body>")
    return head + body + "</body></html>"


# KC's default screener (same as FA in template.html), for the daily snapshot
EXCL = {"Consumer Cyclical", "Consumer Defensive", "Basic Materials", "Financial Services", "Healthcare", "Real Estate",
        "Industrials"}


def pass_kc(s, y=False):
    """KC's default conditions on today's numbers, or yesterday's technicals when y is True."""
    d50, x50, vol, c = (s.get("pd50"), s.get("px50"), s.get("pvol"), s["c"] / (1 + (s["chg"] or 0) / 100)) if y else \
        (s["d50"], s["x50"], s["vol"], s["c"])
    ok = lambda k, f: s.get(k) is not None and f(s[k])
    return (s.get("us") is not False and ok("mcap", lambda v: v >= 0.3) and ok("sq", lambda v: v > 5)
            and ok("sa", lambda v: v > 0) and ok("inst", lambda v: v > 10) and d50 is not None and d50 > 0 and bool(x50)
            and ok("beta", lambda v: v > 1) and (vol or 0) > 50000 and c > 20 and s.get("sec") not in EXCL)


def snapshot(data):
    """What the page showed today, kept for 歷史日期檢視 (never rebuilt later from newer data)."""
    st = data["stocks"]
    kc = [s for s in st if pass_kc(s)]
    was = {s["s"] for s in st if s.get("pd50") is not None and pass_kc(s, y=True)}
    row = lambda s: [s["s"], s.get("n"), s["c"], s["chg"], s["rsr"], s["d50"], s["tt"], s["mvp"], s.get("sec"), s.get("edate")]
    return dict(d=data["asof"], built=data.get("built"), hist=data["hist"][-2:], rules=data["rules"],
                tech=dict(hist=data["tech"]["hist"][-2:], rules=data["tech"]["rules"]) if data.get("tech") else None,
                breadth=data["breadth"], sectors=data["sectors"], rebound=data.get("rebound"), panic=data.get("panic"),
                fng={k: (data.get("fng") or {}).get(k) for k in ("score", "rating")},
                kc=[row(s) for s in sorted(kc, key=lambda s: -s["rsr"])], kc_in=sorted({s["s"] for s in kc} - was),
                kc_out=sorted(was - {s["s"] for s in kc}))


def keep_history(data, folder):
    """Save today's snapshot into `folder` (a checkout of the history branch) and publish all of them under site/h/.
    Returns when the latest 盤前需知 was made (None if there is none yet)."""
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{data['asof']}.json").write_text(json.dumps(snapshot(data), ensure_ascii=False, separators=(",", ":")),
                                                 encoding="utf-8")
    out = OUT / "h"
    out.mkdir(parents=True, exist_ok=True)
    days = sorted(f.stem for f in folder.glob("20*.json"))
    for d in days:
        (out / f"{d}.json").write_bytes((folder / f"{d}.json").read_bytes())
    (out / "index.json").write_text(json.dumps(days), encoding="utf-8")
    # 盤前需知 briefs (written by kcdt.premarket) are kept in the same folder under pm/
    pm, pm_out = folder / "pm", OUT / "pm"
    if pm.is_dir():
        pm_out.mkdir(parents=True, exist_ok=True)
        for f in pm.glob("20*.json"):
            (pm_out / f.name).write_bytes(f.read_bytes())
        days = sorted(f.stem for f in pm_out.glob("20*.json"))
        (pm_out / "index.json").write_text(json.dumps(days), encoding="utf-8")
        if days:
            return json.loads((pm_out / f"{days[-1]}.json").read_text(encoding="utf-8")).get("t")
    return None


def write_stock_pages(pages):
    folder = OUT / "s"
    folder.mkdir(parents=True, exist_ok=True)
    for t, page in pages.items():
        (folder / f"{t}.json").write_text(json.dumps(page, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    meta = universe()
    px = fetch_prices(meta)
    want = wants_fundamentals(px, meta)
    fund = fundamentals(want, ROOT / "cache" / "fundamentals.json")
    nq = nasdaq_quarters(want, ROOT / "cache" / "nasdaq_quarters.json",
                         reports={t: r["erep"] for t, r in fund.items() if r.get("erep")})
    fund = {t: {**rec, **nq.get(t, {}), "_ts": max(rec.get("_ts", 0), nq.get(t, {}).get("_ts", 0))}
            for t, rec in fund.items()}
    prof = profiles(list(meta.index), ROOT / "cache" / "profiles.json", have={t for t, r in fund.items() if r.get("ind")})
    earn, done = earnings_calendar(cache_file=ROOT / "cache" / "earnings_done.json")
    # Yahoo's next date for everything without a set one yet (Nasdaq's dates without a time can be projections)
    soon = {t for t, e in earn.items() if e[1]} | {t for t, r in fund.items() if (r.get("edate") or 0) > time.time()}
    order = sorted(meta.index, key=lambda t: (not meta.at[t, "ndx"], t not in fund, t))
    ydates = earnings_dates(order, ROOT / "cache" / "earnings_dates.json", known=soon)
    data = build(px, meta, fund, fear_greed(ROOT / "cache" / "fear_greed.json"), earn, done, prof, ydates)
    data["econ"], data["econ_t"] = economic_calendar(labels_file="cache/econ_labels.json"), now_utc()
    data["news"], data["news_t"] = headlines(), now_utc()
    if len(data["stocks"]) < 1000:
        sys.exit(f"only {len(data['stocks'])} stocks computed, refusing to publish")
    data["built"] = now_utc()
    data["checks"] = run_checks(data)
    for c in data["checks"]:
        print("check:", "ok" if c["lv"] == 0 else "LOOK" if c["lv"] == 1 else "PROBLEM", c["t"])
    OUT.mkdir(exist_ok=True)
    data["pm_t"] = keep_history(data, os.environ.get("KCDT_HISTORY", ROOT / "hist"))
    (OUT / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (OUT / "index.html").write_text(render(data), encoding="utf-8")
    write_stock_pages(stock_pages(px, data["stocks"]))
    print(f"built {data['asof']}: score {data['hist'][-1]['score']}, "
          f"{len(data['stocks'])} stocks, {data['nfund']} with fundamentals")
    have = lambda k: sum(1 for x in data["stocks"] if x.get(k) is not None)
    tech = data["tech"]["hist"][-1]["score"] if data["tech"] else None
    print(f"coverage: 3q revenue {have('g3r')}, 3q eps {have('g3e')}, 2y {have('yr')}, eps forecast {have('e0')}; "
          f"tech score {tech}, panic {data['panic']}, fear & greed {(data['fng'] or {}).get('score')}")


if __name__ == "__main__":
    main()
