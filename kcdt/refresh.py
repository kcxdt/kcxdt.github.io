"""python -m kcdt.refresh -> re-fetch the economic calendar and news into an already built site/.

The full build runs once after the US close; this runs a few times during the US day so releases
(08:30 / 10:00 New York, FOMC at 14:00) show their actual numbers the same evening in Hong Kong.
It also looks up today's earnings results (pre-market reporters in the morning, after-close ones in the
evening: the close build dispatches this run when it finishes), so the 盤前需知 page shows them that night."""
import json

import pandas as pd

from .__main__ import OUT, now_utc, write_page
from .checks import fund_stale, run as run_checks
from .compute import enrich_earnings
from .fetch import economic_calendar, headlines


def apply_live_results(data):
    live = data.get("earn_live") or {}
    if not live.get("d"):
        return
    reports = {r["s"]: r for r in live.get("rows", []) if r.get("rep") is not None}
    for row in data["stocks"]:
        report = reports.get(row["s"])
        if report and live["d"] >= (row.get("erep") or ""):
            enrich_earnings(row, {"reported": live["d"], "report_session": report.get("tm")}, {})
            row["fst"] = fund_stale(row)


def main():
    path = OUT / "data.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    econ = economic_calendar(labels_file="cache/econ_labels.json", days_file="cache/econ_days.json",
                             prev=data.get("econ"))
    if econ:  # keep the last good copy when Nasdaq fails
        data["econ"], data["econ_t"] = econ, now_utc()
    news = headlines()
    if news:
        data["news"], data["news_t"] = news, now_utc()
    try:
        from .premarket import live_results
        live = live_results(data, pd.Timestamp.now(tz="America/New_York"), data.get("earn_live"))
        if live is not None and (live["rows"] or not (data.get("earn_live") or {}).get("rows")):
            data["earn_live"] = live
    except Exception as e:  # results are a bonus; never lose the econ / news refresh over them
        print("live results failed:", e)
    apply_live_results(data)
    if not data.get("etfs"):  # the close build makes the ETF map; a site built before that feature gets it here
        try:
            from .etfs import etf_map
            data["etfs"] = etf_map([s["s"] for s in data["stocks"]], "cache/etfs.json")
        except Exception as e:
            print("ETF map failed:", e)
    try:
        # the page is rebuilt from today's template, so the stored checks must follow today's rules too: after a
        # rule change (12 Nov 2026, past-due dates) the page counted 6 while the checks from the close build said 15
        data["checks"] = run_checks(data)
    except Exception as e:
        print("checks failed, keeping the close build's:", e)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    write_page(data)
    print(f"refreshed: {len(econ)} econ rows, {len(news)} headlines (empty = kept the last good copy)")


if __name__ == "__main__":
    main()
