from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from build_api import (
    ENDPOINT_URL,
    assess_price_validity,
    build_api_artifacts,
    build_api_payload,
    decrypt_envelope,
    encrypt_payload,
)
from oil_signal_client import fetch_oil_signal_api, normalize_envelope_url


def make_price_quote(
    name: str,
    symbol: str,
    price: float,
    previous_close: float,
    timestamp: str,
    sentiment: str,
) -> dict:
    change = round(price - previous_close, 6)
    change_percent = round(change / previous_close * 100, 4)
    return {
        "name": name,
        "symbol": symbol,
        "price": price,
        "currency": "USD",
        "unit": "barrel",
        "price_timestamp": timestamp,
        "history": [{"timestamp": timestamp, "price": price}],
        "previous_close": previous_close,
        "change": change,
        "change_percent": change_percent,
        "change_basis": "previous_daily_close",
        "price_change_sentiment": sentiment,
        "source": "Yahoo Finance chart API",
        "provider_observations": [
            {
                "source": "Yahoo Finance chart API",
                "price": price,
                "timestamp": timestamp,
            }
        ],
    }


def write_sample_archive(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    records_day_1 = [
        {
            "schema_version": 1,
            "collected_at": "2026-10-23T14:00:00Z",
            "prices": {
                "wti_crude": make_price_quote(
                    "wti_crude", "CL=F", 98.0, 100.0, "2026-10-23T13:59:00Z", "bearish"
                ),
                "brent_crude": make_price_quote(
                    "brent_crude", "BZ=F", 107.0, 108.0, "2026-10-23T13:59:00Z", "bearish"
                ),
            },
            "news": [
                {
                    "id": "oilprice:story-1",
                    "source": "oilprice",
                    "title": "Refinery maintenance weighs on short-term crude demand",
                    "url": "https://example.com/story-1",
                    "published_at": "2026-10-23T13:30:00Z",
                    "summary": "Seasonal refinery turnarounds reduce crude intake.",
                    "analysis": {
                        "summary": "Refinery maintenance reduces near-term crude demand.",
                        "direction": "bearish",
                        "confidence": 70,
                        "validity": "supported",
                        "rationale": "Lower refinery runs reduce immediate crude demand.",
                        "assessed_at": "2026-10-23T14:00:05Z",
                        "revision": 1,
                        "assessment_type": "new",
                        "market_context": {},
                    },
                }
            ],
            "errors": [],
            "news_analysis": {"status": "completed", "article_count": 1},
        },
        {
            "schema_version": 1,
            "collected_at": "2026-10-23T20:00:00Z",
            "prices": {
                "wti_crude": make_price_quote(
                    "wti_crude", "CL=F", 102.5, 100.0, "2026-10-23T19:59:00Z", "bullish"
                ),
                "brent_crude": make_price_quote(
                    "brent_crude", "BZ=F", 110.0, 108.0, "2026-10-23T19:59:00Z", "bullish"
                ),
            },
            "news": [],
            "errors": [],
            "news_analysis": {"status": "no_news"},
        },
        # Duplicate quote in next 5-min poll (e.g., after Friday close)
        {
            "schema_version": 1,
            "collected_at": "2026-10-23T21:05:00Z",
            "prices": {
                "wti_crude": make_price_quote(
                    "wti_crude", "CL=F", 102.5, 100.0, "2026-10-23T19:59:00Z", "bullish"
                ),
                "brent_crude": make_price_quote(
                    "brent_crude", "BZ=F", 110.0, 108.0, "2026-10-23T19:59:00Z", "bullish"
                ),
            },
            "news": [
                {
                    "id": "bbc_business:story-2",
                    "source": "bbc_business",
                    "title": "Winter heating fuel demand surges",
                    "url": "https://example.com/story-2",
                    "published_at": "2026-10-23T21:00:00Z",
                    "summary": "Cold snap boosts distillate demand.",
                    "analysis": {
                        "summary": "Cold weather forecasts lift heating oil demand.",
                        "direction": "bullish",
                        "confidence": 80,
                        "validity": "supported",
                        "rationale": "Stronger winter distillate demand tightens crude balances.",
                        "assessed_at": "2026-10-23T21:05:10Z",
                        "revision": 1,
                        "assessment_type": "new",
                        "market_context": {
                            "wti_crude": {
                                "from": "2026-10-23T19:59:00Z",
                                "to": "2026-10-23T19:59:00Z",
                                "start_price": 102.5,
                                "latest_price": 102.5,
                                "change_percent": 0.0,
                            }
                        },
                    },
                },
                {
                    "id": "cnbc:story-3",
                    "source": "cnbc_business",
                    "title": "Unassessed breaking energy headline",
                    "url": "https://example.com/story-3",
                    "published_at": "2026-10-23T21:02:00Z",
                    "summary": "Awaiting AI assessment.",
                },
            ],
            "errors": [],
            "news_analysis": {"status": "skipped"},
        },
    ]
    (data_dir / "2026-10-23.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records_day_1) + "\n",
        encoding="utf-8",
    )


class BuildApiTests(unittest.TestCase):
    def test_compresses_price_directions_inflections_and_deduplicates_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "oil"
            write_sample_archive(data_dir)
            now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)

            payload = build_api_payload(data_dir, now=now)

            self.assertEqual(payload["endpoint"], ENDPOINT_URL)
            self.assertEqual(payload["total_collections_analyzed"], 3)
            self.assertTrue(payload["market_status"]["is_weekend_closed"])
            self.assertIsNotNone(payload["market_status"]["active_weekend_window"])

            # Verify latest price and validity
            wti = payload["prices"]["wti_crude"]
            self.assertEqual(wti["price"], 102.5)
            self.assertEqual(wti["previous_close"], 100.0)
            self.assertEqual(wti["price_change_sentiment"], "bullish")
            self.assertEqual(wti["validity"]["status"], "valid")

            # Verify direction compression: 1 bearish -> 1 bullish regime, 1 inflection point
            wti_comp = payload["price_direction_compression"]["wti_crude"]
            self.assertEqual(wti_comp["current_regime"]["direction"], "bullish")
            self.assertEqual(len(wti_comp["regime_segments"]), 2)
            self.assertEqual(wti_comp["regime_segments"][0]["direction"], "bearish")
            self.assertEqual(wti_comp["regime_segments"][1]["direction"], "bullish")
            self.assertEqual(len(wti_comp["inflection_points"]), 1)
            inflection = wti_comp["inflection_points"][0]
            self.assertEqual(inflection["from_direction"], "bearish")
            self.assertEqual(inflection["to_direction"], "bullish")
            self.assertEqual(inflection["price"], 102.5)

            # Verify step change between deduplicated quotes (98.0 -> 102.5)
            self.assertEqual(len(wti_comp["step_changes"]), 1)
            self.assertEqual(wti_comp["step_changes"][0]["step_change"], 4.5)
            self.assertEqual(wti_comp["step_changes"][0]["step_direction"], "up")

            # Verify deduplicated series collapsed the two identical 102.5 quotes into repeat_count=2
            dedup_wti = payload["chart_data"]["deduplicated_series"]["wti_crude"]
            self.assertEqual(len(dedup_wti), 2)
            self.assertEqual(dedup_wti[0][1], 98.0)
            self.assertEqual(dedup_wti[0][6], 1)
            self.assertEqual(dedup_wti[1][1], 102.5)
            self.assertEqual(dedup_wti[1][6], 2)

            # Verify news sentiment summary and articles
            news_summary = payload["news_sentiment"]["summary"]
            self.assertEqual(news_summary["total_stories"], 3)
            self.assertEqual(news_summary["assessed_stories"], 2)
            self.assertEqual(news_summary["by_direction"]["bullish"], 1)
            self.assertEqual(news_summary["by_direction"]["bearish"], 1)
            self.assertEqual(news_summary["by_direction"]["unassessed"], 1)
            self.assertEqual(news_summary["net_sentiment_score"], 0.0)
            self.assertGreater(news_summary["confidence_weighted_sentiment_score"], 0.0)

    def test_price_validity_detects_stale_and_insufficient_quotes(self):
        now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
        stale_quote = {
            "price": 100.0,
            "previous_close": 99.0,
            "price_timestamp": "2026-10-22T10:00:00Z",
        }
        stale_result = assess_price_validity(stale_quote, now)
        self.assertEqual(stale_result["status"], "stale")
        self.assertEqual(stale_result["direction"], "bullish")

        insufficient_quote = {
            "price": 100.0,
            "previous_close": None,
            "price_timestamp": "2026-10-25T11:55:00Z",
        }
        insufficient_result = assess_price_validity(insufficient_quote, now)
        self.assertEqual(insufficient_result["status"], "insufficient")
        self.assertEqual(insufficient_result["direction"], "unknown")

    def test_encrypt_and_decrypt_round_trip_and_wrong_key_rejection(self):
        sample_payload = {
            "schema_version": 1,
            "prices": {"wti_crude": {"price": 102.175}},
            "secret_marker": "sensitive-oil-signal-payload",
        }
        envelope = encrypt_payload(
            sample_payload,
            "super-secret-agent-key",
            generated_at="2026-10-25T12:00:00Z",
            iterations=10_000,
        )
        serialized = json.dumps(envelope)
        self.assertNotIn("sensitive-oil-signal-payload", serialized)
        self.assertNotIn("102.175", serialized)
        self.assertEqual(envelope["encryption"]["algorithm"], "AES-256-GCM")

        decrypted = decrypt_envelope(envelope, "super-secret-agent-key")
        self.assertEqual(decrypted, sample_payload)

        with self.assertRaisesRegex(ValueError, "authentication failed"):
            decrypt_envelope(envelope, "wrong-api-key")

    def test_openssl_libcrypto_fallback_round_trip(self):
        from unittest.mock import patch

        sample_payload = {"schema_version": 1, "prices": {"brent_crude": 111.68}}
        with patch("build_api._AESGCM", None):
            envelope = encrypt_payload(
                sample_payload,
                "fallback-test-key",
                iterations=10_000,
            )
            decrypted = decrypt_envelope(envelope, "fallback-test-key")
            self.assertEqual(decrypted, sample_payload)
            with self.assertRaisesRegex(ValueError, "authentication failed"):
                decrypt_envelope(envelope, "wrong-key")

    def test_build_api_artifacts_fails_closed_when_key_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "oil"
            out_dir = root / "site" / "api"
            write_sample_archive(data_dir)

            stub = build_api_artifacts(data_dir, out_dir, None)
            self.assertEqual(stub["status"], "unauthorized_stub")
            saved_text = (out_dir / "index.json").read_text(encoding="utf-8")
            self.assertNotIn("102.5", saved_text)
            self.assertNotIn("Winter heating fuel", saved_text)
            self.assertTrue((out_dir / "index.html").exists())

            with self.assertRaisesRegex(ValueError, "OIL_SIGNAL_API_KEY is required"):
                build_api_artifacts(data_dir, out_dir, "", require_key=True)

    def test_client_fetches_and_decrypts_built_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "oil"
            out_dir = root / "site" / "api"
            write_sample_archive(data_dir)

            build_api_artifacts(
                data_dir,
                out_dir,
                "agent-shared-key",
                now=datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc),
                iterations=10_000,
            )
            envelope_bytes = (out_dir / "index.json").read_bytes()

            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return None

                def read(self):
                    return envelope_bytes

            captured_urls = []

            def fake_opener(request, timeout):
                captured_urls.append(request.full_url)
                return FakeResponse()

            result = fetch_oil_signal_api(
                "agent-shared-key",
                "https://knightkalman.github.io/oil-signal/api/",
                opener=fake_opener,
            )
            self.assertEqual(
                captured_urls[0],
                "https://knightkalman.github.io/oil-signal/api/index.json",
            )
            self.assertEqual(
                 normalize_envelope_url("https://knightkalman.github.io/oil-signal/api"),
                "https://knightkalman.github.io/oil-signal/api/index.json",
            )
            self.assertEqual(result["prices"]["wti_crude"]["price"], 102.5)
            self.assertEqual(result["news_sentiment"]["summary"]["total_stories"], 3)


if __name__ == "__main__":
    unittest.main()
