"""python -m kcdt  ->  writes site/index.html and site/data.json"""
import json
import os
import pathlib
import sys
import time

import pandas as pd
from datetime import datetime, timezone

from .checks import run as run_checks
from .compute import build, stock_pages, wants_fundamentals
from .mkt import HOLIDAYS, NY, close_due
from .fetch import (earnings_calendar, earnings_dates, economic_calendar, fear_greed, fetch_prices, fundamentals, headlines, profiles,
                    nasdaq_quarters, universe)

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "site"


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


SITE = "https://kcxdt.github.io/"
# Link previews (WhatsApp, Telegram...) and 「加到主畫面」; the images are copied from kcdt/static by write_static()
META = ('<meta property="og:type" content="website"><meta property="og:site_name" content="KCDT">'
        '<meta property="og:title" content="KCDT 美股趨勢溫度計">'
        '<meta property="og:description" content="每日美股趨勢數據：趨勢風險分、市場寬度、熱力圖、選股器、業績日曆。只供教育用途。">'
        f'<meta property="og:url" content="{SITE}"><meta property="og:image" content="{SITE}og.png">'
        '<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">'
        '<meta name="twitter:card" content="summary_large_image">'
        '<meta name="theme-color" content="#111318"><link rel="manifest" href="manifest.webmanifest">'
        '<link rel="icon" type="image/png" sizes="192x192" href="icon-192.png">'
        '<link rel="apple-touch-icon" href="icon-180.png"><meta name="apple-mobile-web-app-title" content="KCDT">')
MANIFEST = {"name": "KCDT 美股趨勢溫度計", "short_name": "KCDT", "start_url": "./", "scope": "./", "display": "standalone",
            "background_color": "#111318", "theme_color": "#111318", "lang": "zh-HK",
            "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"}]}


def write_static():
    for f in (pathlib.Path(__file__).parent / "static").glob("*.png"):
        (OUT / f.name).write_bytes(f.read_bytes())
    (OUT / "manifest.webmanifest").write_text(json.dumps(MANIFEST, ensure_ascii=False), encoding="utf-8")


def pack_stocks(data):
    """stocks.js: the stock rows as one key list plus value rows (a third smaller than repeating every key), set on
    window.KCDT_STOCKS for the page's stocksReady(). `asof` lets the page spot a copy from another build."""
    keys = []
    for s in data["stocks"]:
        keys += [k for k in s if k not in keys]
    rows = [[s.get(k) for k in keys] for s in data["stocks"]]
    for r in rows:
        while r and r[-1] is None:
            r.pop()
    body = json.dumps({"asof": data["asof"], "k": keys, "r": rows}, ensure_ascii=False, separators=(",", ":"))
    return f"window.KCDT_STOCKS={body};if(window.stocksReady)stocksReady();"


def write_page(data):
    """site/index.html (everything but the stock rows) and site/stocks.js."""
    (OUT / "index.html").write_text(render(data), encoding="utf-8")
    (OUT / "stocks.js").write_text(pack_stocks(data), encoding="utf-8")


def render(data):
    tpl = (pathlib.Path(__file__).parent / "template.html").read_text(encoding="utf-8")
    light = {k: v for k, v in data.items() if k != "stocks"}
    body = tpl.replace("/*DATA*/", json.dumps(light, ensure_ascii=False, separators=(",", ":")))
    # fetched while the page itself is still arriving; ?v= so a new build never pairs with yesterday's cached rows
    ver = "".join(c for c in str(data.get("built") or data.get("asof") or "") if c.isalnum())
    stocks = (f'<script async src="stocks.js?v={ver}" onerror="window.KCDT_STOCKS_ERR=1;'
              'if(window.stocksFailed)stocksFailed()"></script>')
    head = ('<!doctype html><html lang="zh-HK"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">' + META +
            "<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}"
            "body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>" + stocks + "</head><body>")
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
    Returns (when the latest 盤前需知 was made, its New York date), or (None, None) if there is none yet."""
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
            return json.loads((pm_out / f"{days[-1]}.json").read_text(encoding="utf-8")).get("t"), days[-1]
    return None, None


def write_stock_pages(pages):
    folder = OUT / "s"
    folder.mkdir(parents=True, exist_ok=True)
    for t, page in pages.items():
        (folder / f"{t}.json").write_text(json.dumps(page, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


# How many slow one-by-one Yahoo / Nasdaq calls a run may make. The close build ("close") only takes the
# companies that just reported plus a few others, so the new close is on the site within ~10 minutes; the midday
# run ("warm", refresh.yml) works through the rest of the cache without publishing anything.
BUDGET = {
    "close": dict(fund=60, nq=120, prof=30, edates=80),
    "warm": dict(fund=300, nq=400, prof=450, edates=400),
}


def snapshot_done(day, folder):
    """True when the history branch already has `day`'s snapshot, built after that day's close."""
    f = pathlib.Path(folder) / f"{day}.json"
    if not f.exists():
        return False
    try:
        built = json.loads(f.read_text(encoding="utf-8")).get("built")
    except ValueError:
        return False
    after = pd.Timestamp(f"{day} 16:20", tz=NY)
    return bool(built) and pd.Timestamp(built.replace("Z", "+00:00")) >= after


def drop_open_session(px, now=None):
    """Remove today's row while New York is still trading: it holds intraday prices, not a close."""
    now = now or pd.Timestamp.now(tz=NY)
    last = px["Close"].index[-1]
    if last.strftime("%Y-%m-%d") == now.strftime("%Y-%m-%d") and now.hour * 60 + now.minute < 980:
        print(f"dropping {last:%Y-%m-%d}: the session is still open ({now:%H:%M} New York)")
        return {f: df.iloc[:-1] for f, df in px.items()}
    return px


def say(msg, t0=time.time()):
    print(f"[{(time.time() - t0) / 60:4.1f} min] {msg}", flush=True)


def main():
    mode = os.environ.get("KCDT_MODE", "close")
    budget = BUDGET[mode]
    hist = os.environ.get("KCDT_HISTORY", ROOT / "hist")
    out = os.environ.get("GITHUB_OUTPUT")
    if "--if-due" in sys.argv:
        # a scheduled backup run: stop when the day's close is already published
        now = pd.Timestamp.now(tz=NY)
        day = close_due(now)
        if day != now.strftime("%Y-%m-%d") or snapshot_done(day, hist):
            print(f"not due ({now:%Y-%m-%d %H:%M} New York; last close {day} "
                  f"{'already published' if snapshot_done(day, hist) else 'is not today'})")
            if out:
                open(out, "a").write("run=false\n")
            return
    meta = universe()
    px = drop_open_session(fetch_prices(meta))
    say(f"prices to {px['Close'].index[-1]:%Y-%m-%d}")
    want = wants_fundamentals(px, meta)
    fund = fundamentals(want, ROOT / "cache" / "fundamentals.json", max_calls=budget["fund"])
    say("fundamentals")
    nq = nasdaq_quarters(want, ROOT / "cache" / "nasdaq_quarters.json", max_calls=budget["nq"],
                         reports={t: r["erep"] for t, r in fund.items() if r.get("erep")})
    fund = {t: {**rec, **nq.get(t, {}), "_ts": max(rec.get("_ts", 0), nq.get(t, {}).get("_ts", 0))}
            for t, rec in fund.items()}
    prof = profiles(list(meta.index), ROOT / "cache" / "profiles.json", have={t for t, r in fund.items() if r.get("ind")},
                    max_calls=budget["prof"])
    say("nasdaq quarters, profiles")
    earn, done = earnings_calendar(cache_file=ROOT / "cache" / "earnings_done.json")
    # Yahoo's next date for everything without a set one yet (Nasdaq's dates without a time can be projections)
    soon = {t for t, e in earn.items() if e[1]} | {t for t, r in fund.items() if (r.get("edate") or 0) > time.time()}
    # Nasdaq-100 first, then Nasdaq dates without a time (they can be projections: BABA, DELL, ORCL), then by size
    cap = lambda t: (fund.get(t, {}).get("mcap") or 0) / 1e9 or (earn.get(t) or [None] * 3)[2] or 0
    order = sorted(meta.index, key=lambda t: (not meta.at[t, "ndx"], not (t in earn and not earn[t][1]), -cap(t), t))
    ydates = earnings_dates(order, ROOT / "cache" / "earnings_dates.json", known=soon, max_calls=budget["edates"])
    say("earnings calendar and dates")
    if mode == "warm":
        print("warm run: caches refreshed, nothing published")
        return
    data = build(px, meta, fund, fear_greed(ROOT / "cache" / "fear_greed.json"), earn, done, prof, ydates)
    data["econ"], data["econ_t"] = economic_calendar(labels_file="cache/econ_labels.json"), now_utc()
    data["news"], data["news_t"] = headlines(), now_utc()
    if len(data["stocks"]) < 1000:
        sys.exit(f"only {len(data['stocks'])} stocks computed, refusing to publish")
    data["built"] = now_utc()
    data["mhol"] = sorted(HOLIDAYS)
    OUT.mkdir(exist_ok=True)
    write_static()
    data["pm_t"], data["pm_d"] = keep_history(data, hist)
    data["checks"] = run_checks(data)
    for c in data["checks"]:
        print("check:", "ok" if c["lv"] == 0 else "LOOK" if c["lv"] == 1 else "PROBLEM", c["t"])
    (OUT / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    write_page(data)
    write_stock_pages(stock_pages(px, data["stocks"]))
    if out:
        open(out, "a").write("run=true\n")
    say("site written")
    print(f"built {data['asof']}: score {data['hist'][-1]['score']}, "
          f"{len(data['stocks'])} stocks, {data['nfund']} with fundamentals")
    have = lambda k: sum(1 for x in data["stocks"] if x.get(k) is not None)
    tech = data["tech"]["hist"][-1]["score"] if data["tech"] else None
    print(f"coverage: 3q revenue {have('g3r')}, 3q eps {have('g3e')}, 2y {have('yr')}, eps forecast {have('e0')}; "
          f"tech score {tech}, panic {data['panic']}, fear & greed {(data['fng'] or {}).get('score')}")


if __name__ == "__main__":
    main()
