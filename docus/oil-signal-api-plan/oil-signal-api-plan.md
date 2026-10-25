# Oil Signal Agent API — Implementation Plan

## 1. Objective & Scope

Create a zero-infrastructure, API-key-protected API endpoint hosted on GitHub Pages at:

- **Endpoint URL:** `https://knightkalman.github.io/oil-signal/api/` (and `https://knightkalman.github.io/oil-signal/api/index.json`)

The API exposes the data powering the Oil Signal chart (`site/index.html`) specifically structured and compressed for consumption by a 3rd-party autonomous agent. It provides:
1. **Current & Historical Price Information** (WTI `CL=F` and Brent `BZ=F` benchmarks, quote validity, provider observations, and range summaries across `24h`, `7d`, `30d`, and `all`).
2. **Compressed Price Direction Changes** (run-length encoded / transition-compressed directional regimes, inflection points, and weekend carry-forward windows matching the chart's logic).
3. **News Sentiments & AI Assessments** (aggregated sentiment counts + per-article Antigravity agent assessments: `direction`, `confidence`, `validity`, `summary`, `rationale`, and post-publication `market_context` price moves).
4. **API-Key-Only Access via Static Encrypted Envelope** (AES-256-GCM authenticated encryption derived from `OIL_SIGNAL_API_KEY`, plus a Dual-Mode Web Gateway at `/api/` that either decrypts in-browser when `?api_key=...` / `#api_key=...` is supplied or serves the encrypted JSON envelope for programmatic agent decryption).

---

## 2. Architecture Overview

Because `https://knightkalman.github.io/oil-signal/` is served by static **GitHub Pages**, traditional server-side HTTP header validation (`401 Unauthorized`) is unavailable without external proxy infrastructure. To enforce strict cryptographic access control where **only holders of the API key can read the payload**:

```
┌────────────────────────┐     ┌─────────────────────────┐     ┌────────────────────────────┐
│   oil_monitor.py       │────▶│    analyze_news.py      │────▶│     build_api.py (NEW)     │
│ Collects WTI/Brent &   │     │ Antigravity AI headline │     │ 1. Aggregates data/oil/*.jsonl
│ RSS energy headlines   │     │ sentiment & reassessment│     │ 2. Compresses direction    │
└────────────────────────┘     └─────────────────────────┘     │    changes & chart series  │
                                                               │ 3. Encrypts payload with   │
                                                               │    OIL_SIGNAL_API_KEY      │
                                                               └──────────────┬─────────────┘
                                                                              │
                                                                              ▼
                                                               ┌────────────────────────────┐
                                                               │   GitHub Pages (site/api/) │
                                                               │ - /api/index.json          │
                                                               │   (AES-256-GCM Envelope)   │
                                                               │ - /api/index.html          │
                                                               │   (Agent Web/JS Gateway)   │
                                                               └────────────────────────────┘
```

### Cryptographic Envelope Design (`AES-256-GCM` + `PBKDF2-HMAC-SHA256`)
- **Secret Source:** Repository secret `OIL_SIGNAL_API_KEY` passed to the GitHub Actions job during the `deploy-dashboard` build step (never committed to Git).
- **Key Derivation:**
  - Generate a random 16-byte `salt` and 12-byte `nonce` (IV) per build.
  - Derive a 32-byte (256-bit) key using `PBKDF2-HMAC-SHA256` (`iterations = 210_000`, standard across Python `hashlib`, Node `crypto`, and Web Crypto API).
- **Compression & Encryption:**
  - Serialize the structured API payload to UTF-8 JSON (and optionally gzip-compress before encryption for compact wire transfer).
  - Encrypt using `AES-256-GCM` (producing ciphertext + 16-byte authentication tag).
  - To avoid adding third-party Python dependencies if desired—or using standard system OpenSSL (`openssl enc` / `cryptography` / pure-Python AES-GCM)—we will use Python's `Subprocess` call to `openssl` or `cryptography` in GitHub Actions (with a zero-dependency fallback / test helper) so the main project's dependency-free philosophy is preserved.

### Dual Consumption Modes for 3rd-Party Agents
1. **Direct Programmatic Fetch (`GET /oil-signal/api/index.json`):**
   - Agent fetches `https://knightkalman.github.io/oil-signal/api/index.json` (or `/oil-signal/api/v1/payload.enc.json`).
   - Decrypts locally in ~5 lines of Python, Node.js, or Go using the shared `OIL_SIGNAL_API_KEY`.
2. **Browser / Web-Fetch Agent Gateway (`GET /oil-signal/api/?api_key=<KEY>` or `#api_key=<KEY>`):**
   - GitHub Pages serves `site/api/index.html` at `https://knightkalman.github.io/oil-signal/api/`.
   - If visited without a key, it returns an `auth_required` JSON status document with instructions.
   - If visited by an agent with a headless browser (or Web Crypto client) passing `?api_key=<KEY>` or `#api_key=<KEY>`, client-side Web Crypto (`crypto.subtle`) fetches `./index.json`, decrypts the AES-256-GCM payload in memory, and replaces the DOM with the raw decrypted JSON (or renders a clean `<pre id="api-response">` JSON payload).

---

## 3. Data & Payload Schema Specification

### 3.1. Encrypted Envelope (`site/api/index.json`)
```json
{
  "schema_version": 1,
  "endpoint": "https://knightkalman.github.io/oil-signal/api/",
  "auth_required": true,
  "encryption": {
    "algorithm": "AES-256-GCM",
    "kdf": "PBKDF2-HMAC-SHA256",
    "iterations": 210000,
    "salt_b64": "<16-byte random salt base64>",
    "nonce_b64": "<12-byte random IV base64>",
    "tag_b64": "<16-byte GCM auth tag base64>",
    "payload_encoding": "json+utf8"
  },
  "generated_at": "2026-10-25T13:20:00Z",
  "ciphertext_b64": "<base64 encoded encrypted JSON payload>"
}
```

### 3.2. Decrypted Agent Payload Schema
Once decrypted with `OIL_SIGNAL_API_KEY`, the plaintext JSON provides a comprehensive, token-efficient view of the chart, prices, direction changes, and news sentiments:

```json
{
  "schema_version": 1,
  "generated_at": "2026-10-25T13:20:00Z",
  "latest_collection_at": "2026-10-25T07:25:29Z",
  "market_status": {
    "is_weekend_closed": true,
    "active_weekend_window": {
      "close": "2026-10-23T21:00:00Z",
      "open": "2026-10-25T22:00:00Z",
      "note": "Dotted weekend chart segments carry forward the last traded price while WTI futures are closed."
    }
  },
  "prices": {
    "wti_crude": {
      "name": "WTI crude",
      "symbol": "CL=F",
      "price": 102.175,
      "currency": "USD",
      "unit": "barrel",
      "price_timestamp": "2026-10-23T20:59:00Z",
      "previous_close": 99.47,
      "change": 2.705,
      "change_percent": 2.7194,
      "change_basis": "previous_daily_close",
      "price_change_sentiment": "bullish",
      "validity": {
        "status": "valid",
        "label": "Valid · ≤48h",
        "reason": "Positive current and previous-close prices; quote timestamp is within 48 hours."
      },
      "source": "Yahoo Finance chart API + Massive Futures API",
      "provider_observations": [
        {"source": "Yahoo Finance chart API", "price": 103.27, "timestamp": "2026-10-23T20:59:58Z"},
        {"source": "Massive Futures API", "price": 101.08, "timestamp": "2026-10-23T20:59:00Z"}
      ]
    },
    "brent_crude": {
      "name": "Brent crude",
      "symbol": "BZ=F",
      "price": 111.68,
      "currency": "USD",
      "unit": "barrel",
      "price_timestamp": "2026-10-23T20:59:00Z",
      "previous_close": 108.66,
      "change": 3.02,
      "change_percent": 2.7793,
      "change_basis": "previous_daily_close",
      "price_change_sentiment": "bullish",
      "validity": {
        "status": "valid",
        "label": "Valid · ≤48h",
        "reason": "Positive current and previous-close prices; quote timestamp is within 48 hours."
      },
      "source": "Yahoo Finance chart API + Massive Futures API",
      "provider_observations": []
    }
  },
  "price_direction_compression": {
    "description": "Compressed directional regimes and inflection points vs previous daily close and point-to-point movements.",
    "wti_crude": {
      "current_regime": {
        "direction": "bullish",
        "since": "2026-10-22T14:00:00Z",
        "duration_hours": 65.4,
        "start_price": 98.4,
        "latest_price": 102.175,
        "net_change": 3.775,
        "net_change_percent": 3.8364,
        "observation_count": 48
      },
      "regime_segments": [
        {
          "direction": "bearish",
          "start_time": "2026-10-19T12:00:00Z",
          "end_time": "2026-10-21T18:00:00Z",
          "start_price": 101.2,
          "end_price": 97.8,
          "high_price": 101.5,
          "low_price": 97.1,
          "change_percent": -3.3597,
          "samples": 32
        }
      ],
      "inflection_points": [
        {
          "timestamp": "2026-10-22T14:00:00Z",
          "from_direction": "bearish",
          "to_direction": "bullish",
          "price": 98.4,
          "previous_close": 97.9
        }
      ],
      "window_summaries": {
        "24h": {"open": 103.27, "close": 102.175, "high": 103.27, "low": 102.175, "change_pct": -1.0603, "samples": 18},
        "7d":  {"open": 96.50,  "close": 102.175, "high": 103.50, "low": 95.80,   "change_pct": 5.8808,  "samples": 190},
        "30d": {"open": 94.10,  "close": 102.175, "high": 103.50, "low": 93.20,   "change_pct": 8.5813,  "samples": 310}
      }
    },
    "brent_crude": {
      "current_regime": {},
      "regime_segments": [],
      "inflection_points": [],
      "window_summaries": {}
    }
  },
  "chart_data": {
    "time_range": {
      "start": "2026-10-16T00:00:00Z",
      "end": "2026-10-25T07:25:29Z"
    },
    "weekend_windows": [
      {"close": "2026-10-23T21:00:00Z", "open": "2026-10-25T22:00:00Z"}
    ],
    "deduplicated_series": {
      "columns": ["timestamp", "price", "previous_close", "change_percent", "direction", "validity"],
      "wti_crude": [
        ["2026-10-23T20:59:58Z", 103.27, 99.47, 3.8202, "bullish", "valid"],
        ["2026-10-23T20:59:00Z", 102.175, 99.47, 2.7194, "bullish", "valid"]
      ],
      "brent_crude": [
        ["2026-10-23T20:59:19Z", 111.7, 108.66, 2.7977, "bullish", "valid"],
        ["2026-10-23T20:59:00Z", 111.68, 108.66, 2.7793, "bullish", "valid"]
      ]
    }
  },
  "news_sentiment": {
    "summary": {
      "total_stories": 42,
      "assessed_stories": 40,
      "by_direction": {
        "bullish": 18,
        "bearish": 9,
        "mixed": 6,
        "neutral": 5,
        "unclear": 2,
        "unassessed": 2
      },
      "net_sentiment_score": 0.225,
      "dominant_direction": "bullish",
      "recent_24h": {
        "total": 4,
        "bullish": 3,
        "bearish": 0,
        "mixed": 1,
        "neutral": 0,
        "unclear": 0
      }
    },
    "articles": [
      {
        "id": "bbc_business:https://www.bbc.co.uk/news/articles/c6n8de1jgrdvo#3",
        "source": "bbc_business",
        "title": "Heating oil costs 'more than double' ahead of winter",
        "url": "https://www.bbc.co.uk/news/articles/c6n8de1jgrdvo?at_medium=RSS&at_campaign=rss",
        "published_at": "2026-10-25T07:12:21Z",
        "first_seen_at": "2026-10-25T07:25:29Z",
        "analysis": {
          "direction": "bullish",
          "confidence": 55,
          "validity": "limited",
          "summary": "Heating oil prices have more than doubled ahead of winter for off-grid residents in Middleton in Teesdale.",
          "rationale": "Surging heating oil prices reflect strong winter seasonal demand and tight distillate markets...",
          "assessed_at": "2026-10-25T07:25:33Z",
          "revision": 1,
          "assessment_type": "new",
          "market_context": {
            "wti_crude": {"from": "2026-10-23T20:59:58Z", "to": "2026-10-23T20:59:58Z", "start_price": 103.27, "latest_price": 103.27, "change_percent": 0.0},
            "brent_crude": {"from": "2026-10-23T20:59:19Z", "to": "2026-10-23T20:59:19Z", "start_price": 111.7, "latest_price": 111.7, "change_percent": 0.0}
          }
        }
      }
    ]
  }
}
```

---

## 4. Compression Logic for Price Direction Changes

Raw 5-minute polling across days generates hundreds of redundant records (especially when markets are closed over weekends or when consecutive 5-minute samples have identical `price_timestamp` or direction). `build_api.py` will compress this for the 3rd-party agent in three layers:

1. **Deduplication of Consecutive Identical Quotes:**
   - Collapse repeated collection cycles where `(symbol, price_timestamp, price)` did not change into a single observation with `first_seen_at`, `last_seen_at`, and `repeat_count`.
2. **Directional Regime Run-Length Encoding (`regime_segments`):**
   - Group consecutive observations sharing the same `price_change_sentiment` (`bullish`, `bearish`, `neutral`) into contiguous segments with:
     - `direction`, `start_time`, `end_time`, `duration_hours`
     - `start_price`, `end_price`, `high_price`, `low_price`, `change_percent`, `samples`
3. **Inflection Points (`inflection_points`):**
   - Extract the exact timestamps where the price direction flipped (e.g., `bearish -> bullish`) or where local price extrema (swing high / swing low exceeding a configurable threshold, e.g., `>= 0.5%` move) occurred.
4. **Multi-Horizon Window Summaries (`window_summaries`):**
   - Pre-compute `24h`, `7d`, `30d`, and `all` OHLC (`open`, `high`, `low`, `close`, `change`, `change_pct`, `volatility_range_pct`, `samples`) matching the 4 range buttons on the chart (`24h`, `7d`, `30d`, `All`).

---

## 5. Implementation Steps & Files to Create / Modify

### Step 1: Create `build_api.py` (Static API Builder & Encryptor)
A standalone Python 3.10+ module (no third-party pip packages required; uses Python standard library `hashlib.pbkdf2_hmac`, `secrets`, `json`, `zoneinfo`, and `openssl enc -aes-256-gcm` or a lightweight standard-library/cryptography bridge) that:
1. Reads all `data/oil/*.jsonl` files in chronological order.
2. Computes quote validity (`valid`, `stale`, `insufficient`) using the exact 48-hour and positive-price rules from `site/index.html`.
3. Computes Chicago-timezone weekend closure windows (`Friday 16:00 America/Chicago` to `Sunday 17:00 America/Chicago`) matching `site/index.html`.
4. Builds the compressed price direction structures (`current_regime`, `regime_segments`, `inflection_points`, `window_summaries`, and compact `deduplicated_series`).
5. Aggregates all news articles and computes the `news_sentiment.summary` distribution (`by_direction`, `net_sentiment_score`, `recent_24h` breakdown) plus the sorted article list with AI assessments.
6. Encrypts the JSON payload using `OIL_SIGNAL_API_KEY` into `site/api/index.json` (and writes `site/api/index.html` gateway).
   - **Fail-safe behavior:** If `OIL_SIGNAL_API_KEY` is missing or empty when `build_api.py` runs, it refuses to write plaintext data and instead writes an explicit `{"error": "API_KEY_NOT_CONFIGURED", "auth_required": true}` stub (or exits non-zero when `--require-key` is passed) so sensitive/gated API output is never accidentally published unencrypted.

### Step 2: Create `site/api/index.html` (Agent Web Gateway & Decryption Endpoint)
Hosted at `https://knightkalman.github.io/oil-signal/api/`:
- Supports URL query parameter `?api_key=<KEY>` or URL fragment `#api_key=<KEY>`.
- Uses the browser's native `crypto.subtle` (`PBKDF2` + `AES-GCM`) to fetch `./index.json`, derive the key, verify the authentication tag, and decrypt the payload.
- Renders the decrypted JSON inside `<pre id="oil-signal-api-json">` and sets `window.OIL_SIGNAL_API_DATA` so both browser-based agents and DOM-scraping agents can read it immediately.
- When accessed without a valid key (or with an invalid key), outputs a structured `401 Unauthorized` JSON error block in `#oil-signal-api-json` explaining how to authenticate via `OIL_SIGNAL_API_KEY`.

### Step 3: Create `oil_signal_client.py` (Reference 3rd-Party Agent Client / CLI)
A lightweight reference client and helper function (`fetch_oil_signal_api(api_key, endpoint_url)`) demonstrating how any 3rd-party Python agent (such as a Hermes subagent or trading agent) fetches `https://knightkalman.github.io/oil-signal/api/index.json` and decrypts it in memory using only `OIL_SIGNAL_API_KEY`.

### Step 4: Update `.github/workflows/oil-monitor.yml`
Update the `deploy-dashboard` job (and trigger paths) to build the encrypted `/api/` artifacts on every 5-minute run:
- Add `build_api.py` to `on.push.paths`.
- In `deploy-dashboard`, add a step:
  ```yaml
  - name: Build encrypted Agent API
    env:
      OIL_SIGNAL_API_KEY: ${{ secrets.OIL_SIGNAL_API_KEY }}
    run: |
      python build_api.py \
        --data-dir data/oil \
        --output-dir site/api
  ```
- **Security Consideration on Public Repo Data:**
  - Currently, `deploy-dashboard` copies raw `data/oil/*.jsonl` to `site/data/oil/` for `site/index.html`.
  - If the public chart (`https://knightkalman.github.io/oil-signal/`) is intended to remain publicly viewable while `/api/` requires an API key, `site/data/oil/` remains for the chart and `site/api/` serves the encrypted compressed agent payload.
  - *(Optional hardening noted in plan)*: If the entire site data should also be gated by the API key, `site/index.html` can also read from the encrypted `site/api/index.json` using a prompt/query key instead of publishing `site/data/oil/*.jsonl`.

### Step 5: Add Unit Tests (`tests/test_build_api.py`)
Comprehensive unit tests in `tests/test_build_api.py` covering:
1. **Direction Compression:** Verifies run-length encoding of `bullish`/`bearish`/`neutral` segments, inflection point detection, and deduplication of identical quote timestamps.
2. **Chart Parity:** Verifies quote validity (`valid`, `stale`, `insufficient`), weekend window detection (`America/Chicago`), and window summaries (`24h`, `7d`, `30d`).
3. **News Sentiment Aggregation:** Verifies counts by direction, net sentiment calculation, and inclusion of `analysis` and `market_context`.
4. **Encryption & Decryption Round-Trip:** Verifies that encrypting with `OIL_SIGNAL_API_KEY` produces valid `AES-256-GCM` ciphertext, that decrypting with the correct key recovers the exact JSON payload, and that decrypting with a wrong key or tampered ciphertext raises an authentication failure.
5. **Missing Key Protection:** Verifies that running without `OIL_SIGNAL_API_KEY` never leaks plaintext data into `site/api/index.json`.

### Step 6: Update `README.md` Documentation
Document:
- How to generate and configure the `OIL_SIGNAL_API_KEY` secret in GitHub Actions (**Settings** > **Secrets and variables** > **Actions**).
- The endpoint URL (`https://knightkalman.github.io/oil-signal/api/` and `https://knightkalman.github.io/oil-signal/api/index.json`).
- Example Python and JavaScript/curl snippets for a 3rd-party agent to query and decrypt the API.

---

## 6. Verification Checklist

- [x] `python3 -m unittest discover -s tests` passes all existing tests (`test_oil_monitor.py`, `test_analyze_news.py`) and new API tests (`test_build_api.py`).
- [x] Local dry-run of `OIL_SIGNAL_API_KEY="test-secret-key" python3 build_api.py --data-dir data/oil --output-dir site/api` generates `site/api/index.json` and `site/api/index.html`.
- [x] Inspecting `site/api/index.json` confirms no plaintext prices or headlines appear in the file—only the `AES-256-GCM` envelope metadata and `ciphertext_b64`.
- [x] Decrypting `site/api/index.json` with `OIL_SIGNAL_API_KEY="test-secret-key"` yields valid JSON containing `prices`, `price_direction_compression`, `chart_data`, and `news_sentiment`.
- [x] Opening `site/api/index.html?api_key=test-secret-key` in a browser decrypts and displays the full JSON payload, whereas opening `site/api/index.html` without the key or with a wrong key returns an unauthorized error object.

