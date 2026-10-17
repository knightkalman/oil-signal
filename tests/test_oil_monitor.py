import json
from unittest import TestCase
from unittest.mock import patch

from oil_monitor import fetch_price, parse_args, parse_feed


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
