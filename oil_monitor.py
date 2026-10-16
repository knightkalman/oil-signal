#!/usr/bin/env python3
"""Collect public oil prices and energy news into a JSON Lines file."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import json
import logging
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


LOGGER = logging.getLogger("oil_monitor")
USER_AGENT = "oil-monitor/1.0 (public data collector)"
HTTP_TIMEOUT_SECONDS = 20
PRICE_SYMBOLS = {
    "wti_crude": "CL=F",
    "brent_crude": "BZ=F",
}
NEWS_FEEDS = {
    "oilprice": "https://oilprice.com/rss/main",
    "eia_today_in_energy": "https://www.eia.gov/rss/todayinenergy.xml",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def request_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read()


def sentiment_for_change(
    price: float, previous_close: float | None
) -> tuple[float | None, float | None, str]:
    if previous_close is None or previous_close == 0:
        return None, None, "unknown"

    change = price - previous_close
    change_percent = change / previous_close * 100
    sentiment = "bullish" if change > 0 else "bearish" if change < 0 else "neutral"
    return round(change, 6), round(change_percent, 4), sentiment


def fetch_price(name: str, symbol: str) -> dict[str, object]:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(symbol, safe='')}?range=5d&interval=1d"
    )
    payload = json.loads(request_bytes(url))
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise ValueError(f"price provider returned an error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise ValueError("price provider returned no chart data")

    result = results[0]
    metadata = result.get("meta", {})
    quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = [value for value in quote_data.get("close", []) if value is not None]
    current_price = metadata.get("regularMarketPrice")
    if current_price is None and closes:
        current_price = closes[-1]
    if current_price is None:
        raise ValueError("price provider returned no current price")

    previous_close = closes[-2] if len(closes) >= 2 else None
    change, change_percent, sentiment = sentiment_for_change(
        float(current_price),
        float(previous_close) if previous_close is not None else None,
    )
    timestamp = metadata.get("regularMarketTime")
    if timestamp is None:
        timestamps = result.get("timestamp") or []
        timestamp = timestamps[-1] if timestamps else None

    return {
        "name": name,
        "symbol": symbol,
        "price": float(current_price),
        "currency": metadata.get("currency", "USD"),
        "unit": "barrel",
        "price_timestamp": (
            datetime.fromtimestamp(timestamp, timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
            if timestamp is not None
            else None
        ),
        "previous_close": (
            float(previous_close) if previous_close is not None else None
        ),
        "change": change,
        "change_percent": change_percent,
        "change_basis": "previous_daily_close",
        "price_change_sentiment": sentiment,
        "source": "Yahoo Finance chart API",
        "source_url": url,
    }


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", " ", value))).strip()


def parse_published_at(value: str | None) -> str | None:
    if not value:
        return None
    try:
        published = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_feed(
    source: str, feed_url: str, content: bytes
) -> list[dict[str, str | None]]:
    root = ET.fromstring(content)
    articles = []
    for item in root.findall(".//item"):
        title = clean_text(item.findtext("title"))
        link = clean_text(item.findtext("link"))
        if not title or not link:
            continue
        identifier = clean_text(item.findtext("guid")) or link
        articles.append(
            {
                "id": f"{source}:{identifier}",
                "source": source,
                "title": title,
                "url": link,
                "published_at": parse_published_at(item.findtext("pubDate")),
                "summary": clean_text(item.findtext("description")),
                "feed_url": feed_url,
            }
        )
    return articles


def initialize_state(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_news (
            id TEXT PRIMARY KEY,
            first_seen_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def collect_cycle(connection: sqlite3.Connection, output_path: Path) -> dict:
    record = {
        "schema_version": 1,
        "collected_at": utc_now(),
        "prices": {},
        "news": [],
        "errors": [],
    }

    for name, symbol in PRICE_SYMBOLS.items():
        try:
            record["prices"][name] = fetch_price(name, symbol)
        except Exception as error:
            LOGGER.error("Could not fetch %s price: %s", name, error)
            record["errors"].append(
                {
                    "source": f"price:{name}",
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    news_ids = set()
    for source, feed_url in NEWS_FEEDS.items():
        try:
            articles = parse_feed(source, feed_url, request_bytes(feed_url))
            for article in articles:
                article_id = article["id"]
                if article_id in news_ids:
                    continue
                news_ids.add(article_id)
                existing = connection.execute(
                    "SELECT 1 FROM seen_news WHERE id = ?", (article_id,)
                ).fetchone()
                if existing is None:
                    record["news"].append(article)
        except Exception as error:
            LOGGER.error("Could not fetch %s news: %s", source, error)
            record["errors"].append(
                {
                    "source": f"news:{source}",
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        output.write("\n")

    if news_ids:
        connection.executemany(
            "INSERT OR IGNORE INTO seen_news (id, first_seen_at) VALUES (?, ?)",
            [(article_id, record["collected_at"]) for article_id in news_ids],
        )
        connection.commit()
    return record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Continuously collect public oil prices and energy news."
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=900,
        help="Seconds between collection cycles (default: 900).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/oil_monitor.jsonl"),
        help="Append-only JSON Lines output path.",
    )
    parser.add_argument(
        "--state-db",
        type=Path,
        default=Path("data/oil_monitor_state.sqlite3"),
        help="SQLite file used to avoid emitting the same news article twice.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Collect one cycle and exit.",
    )
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be a positive number of seconds")
    return args


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = parse_args()
    args.state_db.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(args.state_db) as connection:
        initialize_state(connection)
        while True:
            record = collect_cycle(connection, args.output)
            LOGGER.info(
                "Wrote cycle: %d prices, %d new articles, %d errors",
                len(record["prices"]),
                len(record["news"]),
                len(record["errors"]),
            )
            if args.once:
                return
            time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        LOGGER.info("Stopping oil monitor.")
