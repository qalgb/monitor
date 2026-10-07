#!/usr/bin/env python3
"""Fetch completed US daily closes and publish a consistent YTD snapshot."""

import json
import re
import sys
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


STOCKS = {
    "AAPL": "苹果",
    "MSFT": "微软",
    "GOOGL": "Alphabet",
    "AMZN": "亚马逊",
    "NVDA": "英伟达",
    "META": "Meta",
    "TSLA": "特斯拉",
}
NEW_YORK = ZoneInfo("America/New_York")
OUTPUT = Path(__file__).resolve().parents[1] / "site" / "data.json"


def get_text(url, headers=None):
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=25) as response:
        return response.read().decode("utf-8")


def yahoo_daily(ticker, year):
    start = datetime(year - 1, 12, 15, tzinfo=timezone.utc)
    end = datetime.now(timezone.utc) + timedelta(days=1)
    params = urlencode({
        "period1": int(start.timestamp()),
        "period2": int(end.timestamp()),
        "interval": "1d",
    })
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?{params}"
    data = json.loads(get_text(url, {"User-Agent": "Mozilla/5.0"}))
    chart = data["chart"]
    if chart.get("error") or not chart.get("result"):
        raise ValueError(f"{ticker}: Yahoo chart returned no data: {chart.get('error')}")
    result = chart["result"][0]
    if result["meta"].get("currency") != "USD":
        raise ValueError(f"{ticker}: Yahoo chart returned non-USD prices")
    closes = result["indicators"]["quote"][0]["close"]
    return [
        (datetime.fromtimestamp(ts, NEW_YORK).date().isoformat(), Decimal(str(close)))
        for ts, close in zip(result["timestamp"], closes)
        if close is not None
    ]


def sina_daily(ticker, year):
    url = (
        "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/"
        "var/US_MinKService.getDailyK?" + urlencode({"symbol": ticker, "num": 300})
    )
    text = get_text(url, {"Referer": "https://finance.sina.com.cn/"})
    match = re.search(r"var\((\[.*?\])\)", text, re.DOTALL)
    if not match:
        raise ValueError(f"{ticker}: Sina daily response is invalid")
    return [
        (item["d"], Decimal(str(item["c"])))
        for item in json.loads(match.group(1))
        if item["d"] >= f"{year - 1}-12-15"
    ]


def snapshot(series, source, now=None):
    now = now or datetime.now(timezone.utc)
    market_now = now.astimezone(NEW_YORK)
    year = market_now.year
    rows = []
    dates = set()
    for ticker, name in STOCKS.items():
        daily = sorted(
            (date, close) for date, close in series[ticker]
            if date < market_now.date().isoformat()
            or (date == market_now.date().isoformat()
                and market_now.time() >= time(16, 30))
        )
        anchor = [(date, close) for date, close in daily if date < f"{year}-01-01"]
        current = [(date, close) for date, close in daily if date >= f"{year}-01-01"]
        if not anchor or not current:
            raise ValueError(f"{ticker}: missing previous-year or current-year close")
        base_date, base = anchor[-1]
        date, close = current[-1]
        if base <= 0 or close <= 0:
            raise ValueError(f"{ticker}: invalid close")
        if source == "Sina":
            previous = base
            for _, price in current:
                if price <= 0 or price / previous > Decimal("1.8") or previous / price > Decimal("1.8"):
                    raise ValueError(f"{ticker}: possible split in Sina history; refusing unadjusted YTD")
                previous = price
        dates.add(date)
        rows.append({
            "ticker": ticker,
            "name": name,
            "base_date": base_date,
            "base_close": float(base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "close": float(close.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "ytd_pct": float(((close / base - 1) * 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)),
        })
    if len(dates) != 1:
        raise ValueError(f"Quotes do not share a trading day: {sorted(dates)}")
    as_of = dates.pop()
    if (market_now.date() - datetime.fromisoformat(as_of).date()).days > 7:
        raise ValueError(f"Latest close {as_of} is stale")
    return {
        "as_of": as_of,
        "generated_at": now.isoformat(timespec="seconds"),
        "source": source,
        "stocks": sorted(rows, key=lambda row: row["ytd_pct"], reverse=True),
    }


def update():
    now = datetime.now(timezone.utc)
    errors = []
    for source, fetch in (("Yahoo", yahoo_daily), ("Sina", sina_daily)):
        try:
            series = {ticker: fetch(ticker, now.astimezone(NEW_YORK).year) for ticker in STOCKS}
            result = snapshot(series, source, now)
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
            errors.append(f"{source}: {exc}")
            continue
        if OUTPUT.exists():
            previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
            if previous["as_of"] >= result["as_of"]:
                print(f"No newer completed trading day ({previous['as_of']})")
                return
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Published {result['as_of']} from {source}")
        return
    raise RuntimeError("No reliable snapshot available: " + "; ".join(errors))


if __name__ == "__main__":
    try:
        update()
    except (RuntimeError, OSError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
