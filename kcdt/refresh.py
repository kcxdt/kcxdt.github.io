"""python -m kcdt.refresh -> re-fetch the economic calendar and news into an already built site/.

The full build runs once after the US close; this runs a few times during the US day so releases
(08:30 / 10:00 New York, FOMC at 14:00) show their actual numbers the same evening in Hong Kong."""
import json

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
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    write_page(data)
    print(f"refreshed: {len(econ)} econ rows, {len(news)} headlines (empty = kept the last good copy)")


if __name__ == "__main__":
    main()
