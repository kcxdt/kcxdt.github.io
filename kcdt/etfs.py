"""Single-stock ETFs (leveraged, inverse and option-income ETFs that track one stock), for the search box and the
stock page's 相關 ETF list (KC, 11-15: searching NVDA should also show NVDL, NVD ...).

Nasdaq's ETF screener lists every US-listed ETF with its full name, e.g. "GraniteShares 2x Long NVDA Daily ETF".
A name that carries a leverage / inverse / income word and exactly one stock of the pool (by ticker, or by a few
company names that issuers spell out, such as "T-Rex 2X Long NVIDIA Daily Target ETF") is filed under that stock.
The last good list is kept in the cache, so a failed fetch keeps yesterday's list instead of emptying it.
"""
import json
import pathlib
import re

import requests

from .fetch import NASDAQ_UA

URL = "https://api.nasdaq.com/api/screener/etf?tableonly=true&limit=10000&download=true"

# Words in a single-stock ETF's name. "Corgi NVDA 2x Daily ETF" only has the factor; it is long.
KIND = re.compile(r"\b(\d+(?:\.\d+)?)\s*X\b|LEVERAGE|INVERSE|\bBULL\b|\bBEAR\b|\bLONG\b|\bSHORT\b|OPTION INCOME|"
                  r"YIELDMAX|YIELDBOOST|DAILY INCOME|PREMIUM|COVERED CALL|DISTRIBUTION TARGET|WEEKLYPAY|TARGET 2X")
SHORT = re.compile(r"\bSHORT\b|\bBEAR\b|INVERSE")
INCOME = re.compile(r"OPTION INCOME|YIELDMAX|YIELDBOOST|DAILY INCOME|PREMIUM|COVERED CALL|DISTRIBUTION|WEEKLYPAY|INCOME")
FACTOR = re.compile(r"\b(\d+(?:\.\d+)?)\s*X\b")
# Index, sector, theme and crypto funds are not about one stock even when their name has a ticker-like word
BROAD = re.compile(r"S&P|NASDAQ|INDEX|SECTOR|SEMICONDUCTOR|INTERNET|CHINA|TREASURY|BOND|BITCOIN|ETHER|CRYPTO|GOLD|"
                   r"SILVER|OIL|GAS|BIOTECH|BANK|REGIONAL|MAGNIFICENT|FANG|INNOVATION|SOCIAL|HOMEBUILD|RETAIL|"
                   r"TRANSPORT|AEROSPACE|ENERGY|UTILIT|REAL ESTATE|MID CAP|SMALL CAP|LARGE CAP|RUSSELL|DOW JONES|"
                   r"EMERGING|EUROPE|JAPAN|INDIA|KOREA|BRAZIL|MEXICO|TECHNOLOGY|FINANCIAL|HEALTH|CONSUMER|INDUSTRIAL|"
                   r"MATERIALS|CANNABIS|QUANTUM|ROBOTIC|ARTIFICIAL|CYBER|CLOUD|SOLAR|URANIUM|NUCLEAR|VOLATILITY|VIX|"
                   r"COMMODIT|CURRENCY|DOLLAR|DURATION|MATURITY|ULTRA SHORT|EAFE|SHORT TERM|PREFERRED|UNIVERSE|MINERS|COPPER|EQUITIES|ETN|MUNICIPAL|"
                   r"\bAND\b|\b&\s*\d|BASKET|\bTOP\b|PORTFOLIO")
# Tickers that are also ordinary words in fund names
STOP = {"A", "AI", "ALL", "AN", "ARE", "BIG", "CASH", "CORE", "DAILY", "ETF", "FUND", "FUNDS", "HIGH", "II", "III", "IT",
        "KEY", "LONG", "MAX", "NET", "NEXT", "NOW", "ON", "ONE", "PLUS", "SHORT", "BULL", "BEAR", "TRUST", "US", "USA",
        "X", "INC", "CO", "LTD", "DATA", "TECH", "BIO", "SEMI", "EV", "TOP", "OPEN", "LIFE", "SAFE", "WELL", "GOOD", "BEST",
        "TARGET", "INCOME", "OPTION", "STRATEGY", "SERIES", "SHARES", "PRO", "ULTRA", "GROWTH", "VALUE", "TOTAL", "WORLD",
        "EQUITY", "DIVIDEND", "YIELD", "PREMIUM", "AMERICA", "GLOBAL", "CAPITAL", "LEVERAGE", "ACTIVE", "FIRST", "TIDAL",
        "DEFIANCE", "TRADR", "CORGI", "KURV", "REX", "T", "ETN", "BDC", "MSCI", "BNY", "JP", "N100", "WEEKLY", "MONTHLY", "PAY", "PERFORMANCE", "BOOST", "SPY", "QQQ"}
# Issuers that spell the company out
ALIAS = {"NVIDIA": "NVDA", "TESLA": "TSLA", "APPLE": "AAPL", "MICROSOFT": "MSFT", "AMAZON": "AMZN", "ALPHABET": "GOOGL",
         "GOOGLE": "GOOGL", "NETFLIX": "NFLX", "BROADCOM": "AVGO", "PALANTIR": "PLTR", "COINBASE": "COIN",
         "MICROSTRATEGY": "MSTR", "ADVANCED MICRO": "AMD", "META PLATFORMS": "META", "SUPER MICRO": "SMCI",
         "TAIWAN SEMICONDUCTOR": "TSM", "ROBINHOOD": "HOOD", "ORACLE": "ORCL", "MICRON": "MU", "UBER": "UBER",
         "BERKSHIRE": "BRK-B", "ELI LILLY": "LLY", "SALESFORCE": "CRM", "SHOPIFY": "SHOP", "RIVIAN": "RIVN"}


# The legal trust in front of the fund's own name ("ETF Opportunities Trust T-Rex 2X Long NVIDIA Daily Target ETF")
TRUST = re.compile(r"^(?:.*?\bTrust(?: (?:III|II|IV|I|V)\b)?|Inside Ownership 100 ETF|Themes ETF)\s+(?=\S)", re.I)


def classify(name, pool):
    """name -> (underlying, kind, factor) or None. kind: L = long, S = short / inverse, I = option income."""
    up = " " + name.upper().replace("-", " ") + " "
    if not KIND.search(up):
        return None
    # the broad-fund words, after taking out company names that contain one (TAIWAN SEMICONDUCTOR, META PLATFORMS)
    plain = up
    for k in ALIAS:
        plain = plain.replace(" " + k + " ", " ")
    if BROAD.search(plain):
        return None
    found = {v for k, v in ALIAS.items() if re.search(r"\b" + k + r"\b", up)}
    # a ticker is written in capitals as a word of its own: "Short Duration Cor" or "DoubleLine" are not COR and LINE
    for tok in re.findall(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,5}(?:\.[A-Z])?(?![A-Za-z0-9])", name):
        t = tok.replace(".", "-")
        if t in pool and t not in STOP:
            found.add(t)
    found &= set(pool)
    if len(found) != 1:
        return None
    (under,) = found
    f = FACTOR.search(up)
    factor = float(f.group(1)) if f else 1.0
    kind = "S" if SHORT.search(up) else "I" if INCOME.search(up) and factor == 1.0 else "L"
    return under, kind, factor


def single_stock(rows, pool):
    """rows: [(symbol, name)] -> {underlying: [[symbol, name, kind, factor], ...]} sorted long, short, income."""
    out = {}
    # Nasdaq's list sometimes repeats one fund's name on a dozen other new funds (11-15: "Corgi ACLS 2x Daily ETF"
    # on 15 symbols); a name seen on more than one symbol can't be trusted, so those are left out
    seen = {}
    for sym, name in rows:
        seen[name] = seen.get(name, 0) + 1
    for sym, name in rows:
        if not sym or not name or sym in pool or seen[name] > 1:
            continue
        # "Tradr 2X Long CIEN Daily ETF Tradr 2X Long LWLG Daily ETF": another fund's name glued in front
        twice = re.match(r"^(\w+ \w+) .*\s(\1 .*)$", name)
        if twice:
            name = twice.group(2)
        c = classify(name, pool)
        if c:
            u, kind, factor = c
            out.setdefault(u, []).append([sym, TRUST.sub("", re.sub(r"\s+", " ", name).strip()), kind, factor])
    order = {"L": 0, "S": 1, "I": 2}
    for u in out:
        out[u].sort(key=lambda e: (order[e[2]], -e[3], e[0]))
    return out


def fetch_rows():
    r = requests.get(URL, headers=NASDAQ_UA, timeout=60)
    r.raise_for_status()
    data = (r.json() or {}).get("data") or {}
    rows = (data.get("data") or {}).get("rows") or data.get("rows") or []
    return [((x.get("symbol") or "").strip().upper(), (x.get("companyName") or x.get("name") or "").strip()) for x in rows]


def etf_map(pool, cache_file):
    """The single-stock ETF map for today's pool; the last good fetch when Nasdaq fails."""
    path = pathlib.Path(cache_file)
    try:
        rows = fetch_rows()
        if len(rows) < 1000:
            raise ValueError(f"only {len(rows)} ETFs listed")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - any failure falls back to the cached list
        print(f"ETF list: {e}; using the cached copy")
        rows = json.loads(path.read_text()) if path.exists() else []
    m = single_stock(rows, set(pool))
    print(f"single-stock ETFs: {sum(len(v) for v in m.values())} funds on {len(m)} stocks")
    return m
