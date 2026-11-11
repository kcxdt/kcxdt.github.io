"""python -m kcdt.refresh -> re-fetch the economic calendar and news into an already built site/.

The full build runs once after the US close; this runs a few times during the US day so releases
(08:30 / 10:00 New York, FOMC at 14:00) show their actual numbers the same evening in Hong Kong.
It also looks up today's earnings results (pre-market reporters in the morning, after-close ones in the
evening: the close build dispatches this run when it finishes), so the 盤前需知 page shows them that night."""
import json

import pandas as pd

from .__main__ import OUT, now_utc, write_page
from .fetch import economic_calendar, headlines


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
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    write_page(data)
    print(f"refreshed: {len(econ)} econ rows, {len(news)} headlines (empty = kept the last good copy)")


if __name__ == "__main__":
    main()
