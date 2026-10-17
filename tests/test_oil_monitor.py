import json
from unittest import TestCase
from unittest.mock import patch

from oil_monitor import (
    combine_price_quotes,
    fetch_massive_price,
    fetch_price,
    parse_args,
    parse_feed,
)


class OilPriceHistoryTests(TestCase):
    def test_fetch_price_includes_latest_minute_observation_and_daily_sentiment(self):
        payload = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "currency": "USD",
                            "regularMarketPrice": 82.5,
                            "regularMarketTime": 1_792_123_200,
                            "chartPreviousClose": 81.0,
                            "exchangeTimezoneName": "America/New_York",
                        },
                        "timestamp": [1_792_119_600, 1_792_123_200],
                        "indicators": {
                            "quote": [
                                {
                                    "close": [82.0, 82.5],
                                }
                            ]
                        },
                    }
                ],
                "error": None,
            }
        }
        with patch("oil_monitor.request_bytes", return_value=json.dumps(payload).encode()):
            result = fetch_price("wti_crude", "CL=F")

        self.assertEqual(result["previous_close"], 81.0)
        self.assertEqual(result["change"], 1.5)
        self.assertEqual(result["price_change_sentiment"], "bullish")
        self.assertEqual([point["price"] for point in result["history"]], [82.5])
        self.assertEqual(result["price_timestamp"], result["history"][-1]["timestamp"])
        self.assertIn("interval=1m", result["source_url"])

    def test_worker_default_collection_interval_is_five_minutes(self):
        with patch("sys.argv", ["oil_monitor.py", "--once"]):
            self.assertEqual(parse_args().interval, 300)

    def test_price_fallback_uses_latest_hour_when_market_quote_missing(self):
        payload = {
            "chart": {
                "result": [
                    {
                        "meta": {"currency": "USD", "chartPreviousClose": 80.0},
                        "timestamp": [1_792_119_600, 1_792_123_200],
                        "indicators": {
                            "quote": [{"close": [81.0, 82.0]}]
                        },
                    }
                ],
                "error": None,
            }
        }
        with patch("oil_monitor.request_bytes", return_value=json.dumps(payload).encode()):
            result = fetch_price("wti_crude", "CL=F")

        self.assertEqual(result["price"], 82.0)
        self.assertEqual(result["price_timestamp"], result["history"][-1]["timestamp"])
        self.assertEqual(len(result["history"]), 1)

    def test_massive_fetch_selects_front_contract_and_latest_one_minute_bar(self):
        contracts = {
            "results": [
                {
                    "active": True,
                    "product_code": "CL",
                    "ticker": "CLX6",
                    "days_to_maturity": 40,
                },
                {
                    "active": True,
                    "product_code": "CL",
                    "ticker": "CLZ6",
                    "days_to_maturity": 10,
                },
                {
                    "active": False,
                    "product_code": "CL",
                    "ticker": "CLV6",
                    "days_to_maturity": 1,
                },
            ]
        }
        bars = {
            "results": [
                {"close": 82.75, "window_start": 1_792_123_200_000_000_000}
            ]
        }
        requests = []

        def response_for_request(url, headers=None):
            requests.append((url, headers))
            payload = contracts if "/contracts?" in url else bars
            return json.dumps(payload).encode()

        with patch("oil_monitor.request_bytes", side_effect=response_for_request):
            result = fetch_massive_price("wti_crude", "CL", "test-key")

        self.assertEqual(result["symbol"], "CLZ6")
        self.assertEqual(result["price"], 82.75)
        self.assertEqual(len(result["history"]), 1)
        self.assertIn("resolution=1min", requests[1][0])
        self.assertEqual(requests[0][1]["Authorization"], "Bearer test-key")
        self.assertNotIn("test-key", requests[0][0])

    def test_combines_prices_from_matching_minutes_using_the_average(self):
        yahoo = {
            "price": 82.5,
            "price_timestamp": "2026-10-17T10:15:45Z",
            "previous_close": 81.0,
            "source": "Yahoo Finance chart API",
            "source_url": "https://query1.finance.yahoo.com/chart?interval=1m",
        }
        massive = {
            "price": 83.0,
            "price_timestamp": "2026-10-17T10:15:00Z",
            "source": "Massive Futures API",
            "source_url": "https://api.massive.com/futures/v1/aggs/CLZ6",
        }

        result = combine_price_quotes("wti_crude", "CL=F", yahoo, massive)

        self.assertEqual(result["price"], 82.75)
        self.assertEqual(result["price_timestamp"], "2026-10-17T10:15:00Z")
        self.assertEqual(result["change"], 1.75)
        self.assertEqual(result["source"], "Yahoo Finance chart API + Massive Futures API")
        self.assertEqual(len(result["provider_observations"]), 2)

    def test_uses_newer_quote_when_provider_timestamps_do_not_match(self):
        yahoo = {
            "price": 82.5,
            "price_timestamp": "2026-10-17T10:15:00Z",
            "previous_close": 81.0,
            "source": "Yahoo Finance chart API",
            "source_url": "yahoo",
        }
        massive = {
            "price": 83.0,
            "price_timestamp": "2026-10-17T10:16:00Z",
            "source": "Massive Futures API",
            "source_url": "massive",
        }

        result = combine_price_quotes("wti_crude", "CL=F", yahoo, massive)

        self.assertEqual(result["price"], 83.0)
        self.assertEqual(result["source"], "Massive Futures API")
        self.assertEqual(result["source_urls"], ["massive"])
        self.assertEqual(len(result["provider_observations"]), 1)

    def test_fetch_price_falls_back_to_yahoo_and_records_massive_error(self):
        yahoo = {
            "name": "wti_crude",
            "symbol": "CL=F",
            "price": 82.5,
            "price_timestamp": "2026-10-17T10:15:00Z",
            "history": [{"timestamp": "2026-10-17T10:15:00Z", "price": 82.5}],
            "previous_close": 81.0,
            "change": 1.5,
            "change_percent": 1.8519,
            "change_basis": "previous_daily_close",
            "price_change_sentiment": "bullish",
            "source": "Yahoo Finance chart API",
            "source_url": "yahoo",
        }
        with (
            patch.dict("os.environ", {"MASSIVE_API_KEY": "test-key"}),
            patch("oil_monitor.fetch_yahoo_price", return_value=yahoo),
            patch("oil_monitor.fetch_massive_price", side_effect=ValueError("provider unavailable")),
        ):
            result = fetch_price("wti_crude", "CL=F")

        self.assertEqual(result["price"], 82.5)
        self.assertEqual(result["source"], "Yahoo Finance chart API")
        self.assertIn("Massive: ValueError: provider unavailable", result["provider_errors"])

    def test_fetch_price_uses_massive_when_yahoo_fails(self):
        massive = {
            "name": "wti_crude",
            "symbol": "CLZ6",
            "price": 83.0,
            "price_timestamp": "2026-10-17T10:15:00Z",
            "history": [{"timestamp": "2026-10-17T10:15:00Z", "price": 83.0}],
            "previous_close": None,
            "change": None,
            "change_percent": None,
            "change_basis": "previous_daily_close",
            "price_change_sentiment": "unknown",
            "source": "Massive Futures API",
            "source_url": "massive",
        }
        with (
            patch.dict("os.environ", {"MASSIVE_API_KEY": "test-key"}),
            patch("oil_monitor.fetch_yahoo_price", side_effect=ValueError("Yahoo unavailable")),
            patch("oil_monitor.fetch_massive_price", return_value=massive),
        ):
            result = fetch_price("wti_crude", "CL=F")

        self.assertEqual(result["price"], 83.0)
        self.assertEqual(result["source"], "Massive Futures API")
        self.assertEqual(result["price_change_sentiment"], "unknown")
        self.assertIn("Yahoo Finance: ValueError: Yahoo unavailable", result["provider_errors"])


class EnergyNewsFeedTests(TestCase):
    def test_feed_filters_irrelevant_business_stories_and_resolves_links(self):
        feed = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item>
            <title>Oil refinery announces expansion</title>
            <link>/energy/refinery</link>
            <guid>story-1</guid>
            <pubDate>Fri, 16 Oct 2026 12:00:00 GMT</pubDate>
            <description>Additional refining capacity is planned.</description>
          </item>
          <item>
            <title>Technology company reports quarterly earnings</title>
            <link>https://example.com/technology</link>
            <guid>story-2</guid>
            <description>Revenue exceeded expectations.</description>
          </item>
        </channel></rss>"""

        articles = parse_feed(
            "bbc_business",
            "https://feeds.example.com/news/business/rss.xml",
            feed,
            ("oil", "crude", "refinery"),
        )

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["url"], "https://feeds.example.com/energy/refinery")
        self.assertEqual(articles[0]["id"], "bbc_business:story-1")


if __name__ == "__main__":
    import unittest

    unittest.main()
