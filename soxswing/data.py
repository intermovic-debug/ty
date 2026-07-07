from __future__ import annotations

import json
import csv
import time
from datetime import UTC, datetime, timedelta
from io import StringIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import Bar, IntradayBar


class MarketDataError(RuntimeError):
    pass


def fetch_daily_bars(symbol: str, days: int = 180) -> list[Bar]:
    try:
        return _fetch_yahoo_daily_bars(symbol, days)
    except MarketDataError:
        return _fetch_stooq_daily_bars(symbol)


def _fetch_yahoo_daily_bars(symbol: str, days: int = 180) -> list[Bar]:
    end = int(time.time())
    start_dt = datetime.now(tz=UTC) - timedelta(days=days)
    start = int(start_dt.timestamp())
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?period1={start}&period2={end}&interval=1d"
    )

    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise MarketDataError(f"Failed to download {symbol}: {exc}") from exc

    result = payload.get("chart", {}).get("result")
    if not result:
        raise MarketDataError(f"No chart result for {symbol}")

    chart = result[0]
    timestamps = chart.get("timestamp") or []
    quote = (chart.get("indicators", {}).get("quote") or [{}])[0]
    bars: list[Bar] = []

    for index, timestamp in enumerate(timestamps):
        values = {
            "open": quote.get("open", [None])[index],
            "high": quote.get("high", [None])[index],
            "low": quote.get("low", [None])[index],
            "close": quote.get("close", [None])[index],
            "volume": quote.get("volume", [0])[index],
        }
        if any(values[key] is None for key in ("open", "high", "low", "close")):
            continue
        bars.append(
            Bar(
                date=datetime.fromtimestamp(timestamp, tz=UTC).date(),
                open=float(values["open"]),
                high=float(values["high"]),
                low=float(values["low"]),
                close=float(values["close"]),
                volume=int(values["volume"] or 0),
            )
        )

    if len(bars) < 30:
        raise MarketDataError(f"Not enough bars for {symbol}: {len(bars)}")
    return bars


def _fetch_stooq_daily_bars(symbol: str) -> list[Bar]:
    url = f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=20) as response:
            text = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise MarketDataError(f"Failed to download {symbol} from fallback: {exc}") from exc

    bars: list[Bar] = []
    for row in csv.DictReader(StringIO(text)):
        try:
            bars.append(
                Bar(
                    date=datetime.strptime(row["Date"], "%Y-%m-%d").date(),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=int(row["Volume"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    if len(bars) < 30:
        sample = text.strip().replace("\n", " | ")[:160]
        raise MarketDataError(
            f"Not enough fallback bars for {symbol}: {len(bars)}. Response sample: {sample}"
        )
    return bars[-220:]


def fetch_intraday_bars(symbol: str, interval: str = "15m", range_days: int = 5) -> list[IntradayBar]:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?range={range_days}d&interval={interval}&includePrePost=false"
    )
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})

    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise MarketDataError(f"Failed to download intraday {symbol}: {exc}") from exc

    result = payload.get("chart", {}).get("result")
    if not result:
        raise MarketDataError(f"No intraday chart result for {symbol}")

    chart = result[0]
    timestamps = chart.get("timestamp") or []
    quote = (chart.get("indicators", {}).get("quote") or [{}])[0]
    bars: list[IntradayBar] = []

    for index, timestamp in enumerate(timestamps):
        values = {
            "open": quote.get("open", [None])[index],
            "high": quote.get("high", [None])[index],
            "low": quote.get("low", [None])[index],
            "close": quote.get("close", [None])[index],
            "volume": quote.get("volume", [0])[index],
        }
        if any(values[key] is None for key in ("open", "high", "low", "close")):
            continue
        bars.append(
            IntradayBar(
                timestamp=datetime.fromtimestamp(timestamp, tz=UTC),
                open=float(values["open"]),
                high=float(values["high"]),
                low=float(values["low"]),
                close=float(values["close"]),
                volume=int(values["volume"] or 0),
            )
        )

    if len(bars) < 40:
        raise MarketDataError(f"Not enough intraday bars for {symbol}: {len(bars)}")
    return bars
