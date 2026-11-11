"""US bank holidays and market early closes, for the economic calendar (shown as ★ rows).

What closes differs by day, so each row says it for three markets:
- 銀行 (bank): Federal Reserve holiday schedule. A holiday on Sunday is observed on Monday; one on Saturday is not
  moved (the Fed and banks stay open on the Friday).
- 債市 (bond): SIFMA recommendations. Full close on every federal holiday (Saturday -> Friday, except New Year's Day),
  14:00 ET early close on the trading day before New Year's Day, Memorial Day, Independence Day and Christmas and the
  day after Thanksgiving. Good Friday: a 12:00 early close when the jobs report comes out that day (SIFMA 2026:
  Fri 3 Apr), else a full close.
- 股市 (stock): NYSE / Nasdaq. Closed on federal holidays except Columbus Day and Veterans Day, and on Good Friday
  (Saturday -> Friday, except New Year's Day). 13:00 ET early close on 3 July (when 4 July is Tue-Fri), the day after
  Thanksgiving and Christmas Eve (when Christmas is Tue-Fri).
Checked against SIFMA's 2026 US list and Longbridge's US trading / half days (2026-11-27, 2026-12-24); the full-day
stock closures match kcdt/mkt.py HOLIDAYS (tests/test_holidays.py). One-off closures (a national day of mourning)
are not in the rules: ForexFactory's "Bank Holiday" rows add those for the current week.
"""
import datetime as dt
from zoneinfo import ZoneInfo

CLOSED, EARLY, OPEN = "closed", "early", "open"
# English name, Chinese name
NAMES = {
    "new_year": ("New Year's Day", "元旦"), "mlk": ("Martin Luther King Jr. Day", "馬丁路德金紀念日"),
    "presidents": ("Presidents' Day", "總統日"), "good_friday": ("Good Friday", "耶穌受難日"),
    "memorial": ("Memorial Day", "陣亡將士紀念日"), "juneteenth": ("Juneteenth", "六月節"),
    "independence": ("Independence Day", "美國獨立日"), "labor": ("Labor Day", "勞動節"),
    "columbus": ("Columbus Day", "哥倫布日"), "veterans": ("Veterans Day", "退伍軍人節"),
    "thanksgiving": ("Thanksgiving Day", "感恩節"), "christmas": ("Christmas Day", "聖誕節"),
}


def _nth(year, month, weekday, n):
    """n-th weekday (0 = Monday) of the month; n = -1 for the last one."""
    if n > 0:
        d = dt.date(year, month, 1)
        d += dt.timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))
        return d
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = (h + l_ - 7 * m + 114) % 31 + 1
    return dt.date(year, month, day)


def _federal(year):
    """{key: actual date} of the federal holidays in `year` (before any weekend shift)."""
    return {
        "new_year": dt.date(year, 1, 1), "mlk": _nth(year, 1, 0, 3), "presidents": _nth(year, 2, 0, 3),
        "memorial": _nth(year, 5, 0, -1), "juneteenth": dt.date(year, 6, 19), "independence": dt.date(year, 7, 4),
        "labor": _nth(year, 9, 0, 1), "columbus": _nth(year, 10, 0, 2), "veterans": dt.date(year, 11, 11),
        "thanksgiving": _nth(year, 11, 3, 4), "christmas": dt.date(year, 12, 25),
    }


def _row(day, key, stock, bond, bank, stock_t=None, bond_t=None, before=None):
    en, zh = NAMES[key]
    return dict(d=day.isoformat(), key=key, en=en, zh=zh, stock=stock, bond=bond, bank=bank,
                stock_t=stock_t, bond_t=bond_t, before=before)


def us_holidays(year):
    """Every holiday and early-close row of `year`, sorted by date."""
    out = {}
    one = dt.timedelta(days=1)

    def put(r):
        k = r["d"]
        if k in out:  # never let an early-close row overwrite a holiday
            return
        out[k] = r

    fed = _federal(year)
    for key, d in fed.items():
        sat, sun = d.weekday() == 5, d.weekday() == 6
        obs = d - one if sat else d + one if sun else d
        if sat and key == "new_year":
            continue  # 1 Jan on Saturday: nothing moves to 31 Dec (and the Fed never moves Saturday holidays)
        stock = OPEN if key in ("columbus", "veterans") else CLOSED
        bank = OPEN if sat else CLOSED
        put(_row(obs, key, stock, CLOSED, bank))
    gf = _easter(year) - 2 * one
    jobs = gf.day <= 7  # first Friday of the month: the jobs report comes out
    put(_row(gf, "good_friday", CLOSED, EARLY if jobs else CLOSED, OPEN, bond_t="12:00" if jobs else None))

    def bday_before(d):
        d -= one
        while d.weekday() >= 5 or d.isoformat() in out:
            d -= one
        return d

    # early closes (SIFMA 14:00 for bonds, NYSE 13:00 for stocks)
    tg = fed["thanksgiving"] + one
    put(_row(tg, "thanksgiving", EARLY, EARLY, OPEN, "13:00", "14:00", before=False))
    j4 = fed["independence"]
    if 1 <= j4.weekday() <= 4:  # 4 July on Tue-Fri: 3 July trades a half day
        put(_row(j4 - one, "independence", EARLY, EARLY, OPEN, "13:00", "14:00", before=True))
    else:
        obs = j4 - one if j4.weekday() == 5 else j4 + one if j4.weekday() == 6 else j4
        put(_row(bday_before(obs), "independence", OPEN, EARLY, OPEN, None, "14:00", before=True))
    xm = fed["christmas"]
    if 1 <= xm.weekday() <= 4:
        put(_row(xm - one, "christmas", EARLY, EARLY, OPEN, "13:00", "14:00", before=True))
    else:
        obs = xm - one if xm.weekday() == 5 else xm + one if xm.weekday() == 6 else xm
        put(_row(bday_before(obs), "christmas", OPEN, EARLY, OPEN, None, "14:00", before=True))
    mem = fed["memorial"]
    put(_row(bday_before(mem), "memorial", OPEN, EARLY, OPEN, None, "14:00", before=True))
    ny = dt.date(year + 1, 1, 1)  # bonds close early on the last trading day of the year
    put(_row(bday_before(ny + one if ny.weekday() == 6 else ny), "new_year", OPEN, EARLY, OPEN, None, "14:00", before=True))
    if not jobs:  # a full-close Good Friday gets a 14:00 bond close the day before
        put(_row(bday_before(gf), "good_friday", OPEN, EARLY, OPEN, None, "14:00", before=True))
    return [out[k] for k in sorted(out)]


def _say(r):
    """Chinese line: what is closed or closes early, and what still opens."""
    m = {"stock": "股市", "bond": "債市", "bank": "銀行"}
    closed = [m[k] for k in ("stock", "bond", "bank") if r[k] == CLOSED]
    parts = ["、".join(closed) + "休市"] if closed else []
    parts += [f"{m[k]}提早 {r[k + '_t']} 收市（美東）" for k in ("stock", "bond") if r[k] == EARLY]
    if r["stock"] == OPEN:
        parts.append("股市照常交易")
    elif r["bank"] == OPEN and r["before"] is None:
        parts.append("銀行照常營業")
    return "；".join(parts)


def _midnight(d):
    """00:00 New York on day d, as the calendar's UTC time (13:00 or 12:00 Hong Kong, the same date)."""
    t = dt.datetime.fromisoformat(d).replace(tzinfo=ZoneInfo("America/New_York"))
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def calendar_rows(days, ff=None):
    """Economic-calendar rows for the holidays and early closes on `days` (New York dates 'YYYY-MM-DD'),
    plus any ForexFactory USD holiday on those days the rules don't know about."""
    want = set(days)
    years = {int(d[:4]) for d in want}
    out = []
    for y in sorted(years):
        for r in us_holidays(y):
            if r["d"] not in want:
                continue
            full = r["before"] is None
            name = r["zh"] if full else (f"{r['zh']}翌日" if r["before"] is False else f"{r['zh']}前夕")
            out.append(dict(d=r["d"], u=_midnight(r["d"]), e=f"Bank Holiday - {r['en']}" if full else f"Early Close - {r['en']}",
                            a=None, c=None, p=None, hol=dict(zh=name, full=full, stock=r["stock"], bond=r["bond"],
                                                             bank=r["bank"], stock_t=r["stock_t"], bond_t=r["bond_t"]),
                            note=_say(r), imp=3, src="https://www.sifma.org/resources/guides-playbooks/holiday-schedule"))
    have = {r["d"] for r in out if r["hol"]["full"]}
    for x in ff or []:
        if (x.get("impact") or "").lower() != "holiday":
            continue
        d = (x.get("d") or "")[:10]
        if d in want and d not in have:
            have.add(d)
            out.append(dict(d=d, u=_midnight(d), e=f"Bank Holiday - {x.get('title') or 'Bank Holiday'}", a=None, c=None, p=None,
                            hol=dict(zh="美國假期", full=True, stock=None, bond=None, bank=CLOSED),
                            note="ForexFactory 列為美國銀行假期；股市同債市有冇開請睇交易所公告", imp=3))
    return sorted(out, key=lambda r: r["d"])
