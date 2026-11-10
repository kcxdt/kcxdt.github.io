"""US market days, for deciding whether a close update is due (GitHub cron backups, the page's late warning)."""
import pandas as pd

NY = "America/New_York"
# NYSE full-day closures (half days such as the day after Thanksgiving still trade). Checked against Longbridge's
# US trading days for 2026; 2027 from the NYSE rules (Good Friday 26 Mar, Juneteenth Sat -> Fri 18 Jun,
# 4 July Sun -> Mon 5 Jul, Christmas Sat -> Fri 24 Dec). Add the next year before it starts.
HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
    "2027-09-06", "2027-11-25", "2027-12-24",
}


def is_trading_day(day):
    d = pd.Timestamp(day)
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in HOLIDAYS


def close_due(now):
    """The New York date whose close should be on the site at `now` (New York time): today once it is 16:30 on a
    trading day, else the trading day before."""
    now = pd.Timestamp(now)
    d = now.normalize().tz_localize(None) if now.tzinfo else now.normalize()
    if not (is_trading_day(d) and now.hour * 60 + now.minute >= 990):
        d -= pd.Timedelta(days=1)
        while not is_trading_day(d):
            d -= pd.Timedelta(days=1)
    return d.strftime("%Y-%m-%d")
