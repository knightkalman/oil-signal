#!/usr/bin/env python3
"""Build the encrypted Oil Signal Agent API static files for GitHub Pages."""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.util
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import tempfile
from zoneinfo import ZoneInfo

try:
    from cryptography.exceptions import InvalidTag as _CryptoInvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM
except ImportError:  # pragma: no cover - fallback uses libcrypto via ctypes
    _AESGCM = None
    _CryptoInvalidTag = Exception


LOGGER = logging.getLogger("oil_monitor.build_api")
ENDPOINT_URL = "https://knightkalman.github.io/oil-signal/api/"
PBKDF2_ITERATIONS = 210_000
SALT_BYTES = 16
NONCE_BYTES = 12
TAG_BYTES = 16
KEY_BYTES = 32
CHICAGO_TZ = ZoneInfo("America/Chicago")
BENCHMARKS = (
    ("wti_crude", "WTI crude", "CL=F"),
    ("brent_crude", "Brent crude", "BZ=F"),
)
ALLOWED_DIRECTIONS = ("bullish", "bearish", "mixed", "neutral", "unclear")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def derive_key(
    api_key: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS
) -> bytes:
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("API key must be a non-empty string")
    return hashlib.pbkdf2_hmac(
        "sha256",
        api_key.encode("utf-8"),
        salt,
        iterations,
        dklen=KEY_BYTES,
    )


def _openssl_aes_gcm(
    encrypt: bool,
    key: bytes,
    nonce: bytes,
    data: bytes,
    tag: bytes | None = None,
) -> tuple[bytes, bytes]:
    lib_name = ctypes.util.find_library("crypto") or "libcrypto.so.3"
    libcrypto = ctypes.CDLL(lib_name)

    EVP_CIPHER_CTX_new = libcrypto.EVP_CIPHER_CTX_new
    EVP_CIPHER_CTX_new.restype = ctypes.c_void_p
    EVP_CIPHER_CTX_free = libcrypto.EVP_CIPHER_CTX_free
    EVP_CIPHER_CTX_free.argtypes = [ctypes.c_void_p]

    EVP_aes_256_gcm = libcrypto.EVP_aes_256_gcm
    EVP_aes_256_gcm.restype = ctypes.c_void_p

    EVP_CipherInit_ex = libcrypto.EVP_CipherInit_ex
    EVP_CipherInit_ex.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    EVP_CIPHER_CTX_ctrl = libcrypto.EVP_CIPHER_CTX_ctrl
    EVP_CIPHER_CTX_ctrl.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
    ]
    EVP_CipherUpdate = libcrypto.EVP_CipherUpdate
    EVP_CipherUpdate.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    EVP_CipherFinal_ex = libcrypto.EVP_CipherFinal_ex
    EVP_CipherFinal_ex.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_int),
    ]

    EVP_CTRL_GCM_SET_IVLEN = 0x9
    EVP_CTRL_GCM_GET_TAG = 0x10
    EVP_CTRL_GCM_SET_TAG = 0x11

    ctx = EVP_CIPHER_CTX_new()
    if not ctx:
        raise RuntimeError("Could not allocate OpenSSL EVP_CIPHER_CTX")
    try:
        enc_flag = 1 if encrypt else 0
        if EVP_CipherInit_ex(ctx, EVP_aes_256_gcm(), None, None, None, enc_flag) != 1:
            raise RuntimeError("EVP_CipherInit_ex cipher setup failed")
        if EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, len(nonce), None) != 1:
            raise RuntimeError("EVP_CIPHER_CTX_ctrl IV length failed")
        if EVP_CipherInit_ex(ctx, None, None, key, nonce, enc_flag) != 1:
            raise RuntimeError("EVP_CipherInit_ex key/nonce setup failed")

        out_buf = ctypes.create_string_buffer(len(data) + TAG_BYTES)
        out_len = ctypes.c_int(0)
        if EVP_CipherUpdate(ctx, out_buf, ctypes.byref(out_len), data, len(data)) != 1:
            raise RuntimeError("EVP_CipherUpdate failed")
        total_len = out_len.value

        if not encrypt:
            if tag is None or len(tag) != TAG_BYTES:
                raise ValueError("Invalid GCM authentication tag length")
            tag_buf = ctypes.create_string_buffer(tag, TAG_BYTES)
            if (
                EVP_CIPHER_CTX_ctrl(
                    ctx, EVP_CTRL_GCM_SET_TAG, TAG_BYTES, ctypes.cast(tag_buf, ctypes.c_void_p)
                )
                != 1
            ):
                raise RuntimeError("EVP_CIPHER_CTX_ctrl set tag failed")

        final_buf = ctypes.create_string_buffer(TAG_BYTES)
        final_len = ctypes.c_int(0)
        if EVP_CipherFinal_ex(ctx, final_buf, ctypes.byref(final_len)) != 1:
            raise ValueError("AES-GCM authentication failed (invalid API key or tampered payload)")
        output = out_buf.raw[:total_len] + final_buf.raw[: final_len.value]

        if encrypt:
            tag_out = ctypes.create_string_buffer(TAG_BYTES)
            if (
                EVP_CIPHER_CTX_ctrl(
                    ctx, EVP_CTRL_GCM_GET_TAG, TAG_BYTES, ctypes.cast(tag_out, ctypes.c_void_p)
                )
                != 1
            ):
                raise RuntimeError("EVP_CIPHER_CTX_ctrl get tag failed")
            return output, tag_out.raw
        return output, tag or b""
    finally:
        EVP_CIPHER_CTX_free(ctx)


def encrypt_aes_gcm(key: bytes, nonce: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    if _AESGCM is not None:
        combined = _AESGCM(key).encrypt(nonce, plaintext, None)
        return combined[:-TAG_BYTES], combined[-TAG_BYTES:]
    return _openssl_aes_gcm(True, key, nonce, plaintext)


def decrypt_aes_gcm(
    key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes
) -> bytes:
    if _AESGCM is not None:
        try:
            return _AESGCM(key).decrypt(nonce, ciphertext + tag, None)
        except (_CryptoInvalidTag, ValueError) as error:
            raise ValueError(
                "AES-GCM authentication failed (invalid API key or tampered payload)"
            ) from error
    plaintext, _ = _openssl_aes_gcm(False, key, nonce, ciphertext, tag)
    return plaintext


def encrypt_payload(
    payload: dict,
    api_key: str,
    *,
    generated_at: str | None = None,
    iterations: int = PBKDF2_ITERATIONS,
) -> dict:
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("API key is required to encrypt the Oil Signal API payload")
    salt = secrets.token_bytes(SALT_BYTES)
    nonce = secrets.token_bytes(NONCE_BYTES)
    key = derive_key(api_key.strip(), salt, iterations=iterations)
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    ciphertext, tag = encrypt_aes_gcm(key, nonce, plaintext)
    timestamp = generated_at or payload.get("generated_at") or utc_now()
    return {
        "schema_version": 1,
        "endpoint": ENDPOINT_URL,
        "auth_required": True,
        "status": "encrypted",
        "generated_at": timestamp,
        "encryption": {
            "algorithm": "AES-256-GCM",
            "kdf": "PBKDF2-HMAC-SHA256",
            "iterations": iterations,
            "salt_b64": base64.b64encode(salt).decode("ascii"),
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "tag_b64": base64.b64encode(tag).decode("ascii"),
            "payload_encoding": "json+utf8",
        },
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
    }


def decrypt_envelope(envelope: dict, api_key: str) -> dict:
    if not isinstance(envelope, dict):
        raise ValueError("API envelope must be a JSON object")
    if envelope.get("status") == "unauthorized_stub" or "ciphertext_b64" not in envelope:
        error_msg = envelope.get("error") or "Encrypted payload is not available"
        raise ValueError(f"Cannot decrypt envelope: {error_msg}")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("A non-empty API key is required for decryption")

    encryption = envelope.get("encryption")
    if not isinstance(encryption, dict):
        raise ValueError("Envelope is missing encryption metadata")
    if encryption.get("algorithm") != "AES-256-GCM":
        raise ValueError(f"Unsupported encryption algorithm: {encryption.get('algorithm')}")
    if encryption.get("kdf") != "PBKDF2-HMAC-SHA256":
        raise ValueError(f"Unsupported KDF: {encryption.get('kdf')}")

    iterations = int(encryption.get("iterations", PBKDF2_ITERATIONS))
    if iterations <= 0:
        raise ValueError("Invalid PBKDF2 iteration count")

    salt = base64.b64decode(encryption["salt_b64"])
    nonce = base64.b64decode(encryption["nonce_b64"])
    tag = base64.b64decode(encryption["tag_b64"])
    ciphertext = base64.b64decode(envelope["ciphertext_b64"])

    key = derive_key(api_key.strip(), salt, iterations=iterations)
    plaintext = decrypt_aes_gcm(key, nonce, ciphertext, tag)
    result = json.loads(plaintext.decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Decrypted payload must be a JSON object")
    return result


def assess_price_validity(quote: dict | None, reference_time: datetime) -> dict:
    if not isinstance(quote, dict):
        return {
            "status": "insufficient",
            "label": "No recent quote",
            "direction": "unknown",
            "reason": "The latest collection did not return this price.",
        }
    try:
        price = float(quote.get("price"))
        previous = float(quote.get("previous_close"))
    except (TypeError, ValueError):
        return {
            "status": "insufficient",
            "label": "Insufficient data",
            "direction": "unknown",
            "reason": "A positive current price and previous daily close are required.",
        }

    if price <= 0 or previous <= 0:
        return {
            "status": "insufficient",
            "label": "Insufficient data",
            "direction": "unknown",
            "reason": "A positive current price and previous daily close are required.",
        }

    timestamp = parse_time(quote.get("price_timestamp"))
    if timestamp is None:
        return {
            "status": "insufficient",
            "label": "Unverified timestamp",
            "direction": "unknown",
            "reason": "The provider did not supply a usable quote timestamp.",
        }

    age_seconds = (reference_time - timestamp).total_seconds()
    if age_seconds < -5 * 60:
        return {
            "status": "insufficient",
            "label": "Unverified timestamp",
            "direction": "unknown",
            "reason": "The provider quote timestamp is unexpectedly in the future.",
        }

    direction = (
        "bullish" if price > previous else "bearish" if price < previous else "neutral"
    )
    if age_seconds > 48 * 3600:
        return {
            "status": "stale",
            "label": "Stale quote",
            "direction": direction,
            "reason": "The provider quote timestamp is more than 48 hours old.",
        }
    return {
        "status": "valid",
        "label": "Valid · ≤48h",
        "direction": direction,
        "reason": "Positive current and previous-close prices; quote timestamp is within 48 hours.",
    }


def weekend_window_for_friday(year: int, month: int, day: int) -> dict[str, datetime]:
    close_local = datetime(year, month, day, 16, 0, 0, tzinfo=CHICAGO_TZ)
    sunday_date = datetime(year, month, day, tzinfo=timezone.utc).date() + timedelta(days=2)
    open_local = datetime(
        sunday_date.year,
        sunday_date.month,
        sunday_date.day,
        17,
        0,
        0,
        tzinfo=CHICAGO_TZ,
    )
    return {
        "close": close_local.astimezone(timezone.utc),
        "open": open_local.astimezone(timezone.utc),
    }


def weekend_window_after(dt: datetime) -> dict[str, datetime]:
    local = dt.astimezone(CHICAGO_TZ)
    # In Python: Monday=0 .. Friday=4, Saturday=5, Sunday=6
    days_since_friday = (local.weekday() - 4) % 7
    friday_date = local.date() - timedelta(days=days_since_friday)
    return weekend_window_for_friday(friday_date.year, friday_date.month, friday_date.day)


def current_weekend_window(now: datetime) -> dict[str, datetime] | None:
    local = now.astimezone(CHICAGO_TZ)
    weekday = local.weekday()
    hour = local.hour
    days_since_friday = 1 if weekday == 5 else 2 if weekday == 6 else 0
    if not days_since_friday and (weekday != 4 or hour < 16):
        return None
    if weekday == 6 and hour >= 17:
        return None
    friday_date = local.date() - timedelta(days=days_since_friday)
    window = weekend_window_for_friday(friday_date.year, friday_date.month, friday_date.day)
    if window["close"] <= now < window["open"]:
        return window
    return None


def load_records(data_dir: Path) -> list[dict]:
    records: list[dict] = []
    for path in sorted(data_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if isinstance(record, dict) and parse_time(record.get("collected_at")):
                records.append(record)
    records.sort(key=lambda item: parse_time(item.get("collected_at")) or datetime.min.replace(tzinfo=timezone.utc))
    return records


def build_benchmark_series(
    records: list[dict], symbol_key: str, reference_time: datetime
) -> tuple[list[dict], list[dict]]:
    """Return (raw_time_ordered_points, deduplicated_observations)."""
    by_timestamp: dict[int, dict] = {}
    dedup_list: list[dict] = []

    for record in records:
        collected_at = parse_time(record.get("collected_at"))
        quote = (record.get("prices") or {}).get(symbol_key)
        if not isinstance(quote, dict):
            continue
        try:
            price = float(quote.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue

        previous_close = None
        if quote.get("previous_close") is not None:
            try:
                previous_close = float(quote["previous_close"])
            except (TypeError, ValueError):
                previous_close = None

        change = None
        change_percent = None
        if previous_close and previous_close > 0:
            change = round(price - previous_close, 6)
            change_percent = round((price - previous_close) / previous_close * 100, 4)

        quote_time = parse_time(quote.get("price_timestamp")) or collected_at
        if quote_time is None:
            continue

        assessment = assess_price_validity(quote, quote_time)
        direction = quote.get("price_change_sentiment") or assessment["direction"]
        if direction not in ("bullish", "bearish", "neutral"):
            direction = assessment["direction"]

        history = quote.get("history")
        if isinstance(history, list) and history:
            for hist_point in history:
                if not isinstance(hist_point, dict):
                    continue
                pt_time = parse_time(hist_point.get("timestamp"))
                try:
                    pt_price = float(hist_point.get("price"))
                except (TypeError, ValueError):
                    continue
                if pt_time is None or pt_price <= 0:
                    continue
                epoch_ms = int(pt_time.timestamp() * 1000)
                by_timestamp[epoch_ms] = {
                    "time": pt_time,
                    "timestamp": format_utc(pt_time),
                    "price": pt_price,
                    "previous_close": previous_close,
                    "change": change,
                    "change_percent": change_percent,
                    "direction": direction,
                    "validity": assessment["status"],
                }
        else:
            epoch_ms = int(quote_time.timestamp() * 1000)
            by_timestamp[epoch_ms] = {
                "time": quote_time,
                "timestamp": format_utc(quote_time),
                "price": price,
                "previous_close": previous_close,
                "change": change,
                "change_percent": change_percent,
                "direction": direction,
                "validity": assessment["status"],
            }

        # Deduplicate consecutive identical (price_timestamp, price, direction)
        ts_str = format_utc(quote_time)
        collected_str = format_utc(collected_at) if collected_at else ts_str
        if (
            dedup_list
            and dedup_list[-1]["price_timestamp"] == ts_str
            and dedup_list[-1]["price"] == price
            and dedup_list[-1]["direction"] == direction
        ):
            dedup_list[-1]["last_collected_at"] = collected_str
            dedup_list[-1]["repeat_count"] += 1
        else:
            dedup_list.append(
                {
                    "time": quote_time,
                    "price_timestamp": ts_str,
                    "first_collected_at": collected_str,
                    "last_collected_at": collected_str,
                    "repeat_count": 1,
                    "price": price,
                    "previous_close": previous_close,
                    "change": change,
                    "change_percent": change_percent,
                    "direction": direction,
                    "validity": assessment["status"],
                }
            )

    raw_points = [by_timestamp[key] for key in sorted(by_timestamp)]
    return raw_points, dedup_list


def compress_price_directions(
    raw_points: list[dict], dedup_observations: list[dict]
) -> dict:
    if not raw_points:
        return {
            "current_regime": None,
            "regime_segments": [],
            "inflection_points": [],
            "step_changes": [],
            "window_summaries": {},
        }

    # 1. Build directional regime segments (run-length encoding of direction vs previous daily close)
    regime_segments: list[dict] = []
    inflection_points: list[dict] = []

    for point in raw_points:
        direction = point["direction"]
        if not regime_segments or regime_segments[-1]["direction"] != direction:
            if regime_segments:
                prev_seg = regime_segments[-1]
                inflection_points.append(
                    {
                        "timestamp": point["timestamp"],
                        "from_direction": prev_seg["direction"],
                        "to_direction": direction,
                        "price": point["price"],
                        "previous_close": point["previous_close"],
                        "change_percent": point["change_percent"],
                    }
                )
            regime_segments.append(
                {
                    "direction": direction,
                    "start_time": point["timestamp"],
                    "end_time": point["timestamp"],
                    "_start_dt": point["time"],
                    "_end_dt": point["time"],
                    "start_price": point["price"],
                    "end_price": point["price"],
                    "high_price": point["price"],
                    "low_price": point["price"],
                    "net_change": 0.0,
                    "net_change_percent": 0.0,
                    "samples": 1,
                }
            )
        else:
            seg = regime_segments[-1]
            seg["end_time"] = point["timestamp"]
            seg["_end_dt"] = point["time"]
            seg["end_price"] = point["price"]
            seg["high_price"] = max(seg["high_price"], point["price"])
            seg["low_price"] = min(seg["low_price"], point["price"])
            seg["samples"] += 1
            if seg["start_price"] > 0:
                seg["net_change"] = round(seg["end_price"] - seg["start_price"], 6)
                seg["net_change_percent"] = round(
                    (seg["end_price"] - seg["start_price"]) / seg["start_price"] * 100,
                    4,
                )

    for seg in regime_segments:
        duration_hours = (seg.pop("_end_dt") - seg.pop("_start_dt")).total_seconds() / 3600.0
        seg["duration_hours"] = round(max(0.0, duration_hours), 2)

    current_regime = dict(regime_segments[-1]) if regime_segments else None

    # 2. Build point-to-point step direction changes across deduplicated observations
    step_changes: list[dict] = []
    for idx in range(1, len(dedup_observations)):
        prev_obs = dedup_observations[idx - 1]
        curr_obs = dedup_observations[idx]
        delta = round(curr_obs["price"] - prev_obs["price"], 6)
        if delta == 0:
            continue
        delta_pct = (
            round(delta / prev_obs["price"] * 100, 4)
            if prev_obs["price"] > 0
            else None
        )
        step_dir = "up" if delta > 0 else "down"
        step_changes.append(
            {
                "from_timestamp": prev_obs["price_timestamp"],
                "to_timestamp": curr_obs["price_timestamp"],
                "from_price": prev_obs["price"],
                "to_price": curr_obs["price"],
                "step_change": delta,
                "step_change_percent": delta_pct,
                "step_direction": step_dir,
                "daily_sentiment": curr_obs["direction"],
            }
        )

    # 3. Compute multi-horizon window summaries matching the chart buttons (24h, 7d, 30d, all)
    latest_time = raw_points[-1]["time"]
    windows = {
        "24h": latest_time - timedelta(days=1),
        "7d": latest_time - timedelta(days=7),
        "30d": latest_time - timedelta(days=30),
        "all": datetime.min.replace(tzinfo=timezone.utc),
    }
    window_summaries: dict[str, dict] = {}
    for label, cutoff in windows.items():
        subset = [pt for pt in raw_points if pt["time"] >= cutoff]
        if not subset:
            continue
        open_price = subset[0]["price"]
        close_price = subset[-1]["price"]
        prices = [pt["price"] for pt in subset]
        high_price = max(prices)
        low_price = min(prices)
        net_change = round(close_price - open_price, 6)
        net_change_pct = (
            round(net_change / open_price * 100, 4) if open_price > 0 else 0.0
        )
        range_pct = (
            round((high_price - low_price) / low_price * 100, 4)
            if low_price > 0
            else 0.0
        )
        window_summaries[label] = {
            "start_time": subset[0]["timestamp"],
            "end_time": subset[-1]["timestamp"],
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "change": net_change,
            "change_percent": net_change_pct,
            "range_percent": range_pct,
            "period_direction": (
                "bullish"
                if net_change > 0
                else "bearish"
                if net_change < 0
                else "neutral"
            ),
            "samples": len(subset),
        }

    return {
        "current_regime": current_regime,
        "regime_segments": regime_segments,
        "inflection_points": inflection_points,
        "step_changes": step_changes,
        "window_summaries": window_summaries,
    }


def compute_weekend_windows(
    series_by_key: dict[str, list[dict]], now: datetime
) -> tuple[list[dict[str, str]], dict[str, str] | None]:
    windows_by_close: dict[int, dict[str, datetime]] = {}
    for points in series_by_key.values():
        for idx in range(1, len(points)):
            previous = points[idx - 1]["time"]
            nxt = points[idx]["time"]
            if (nxt - previous).total_seconds() < 36 * 3600:
                continue
            window = weekend_window_after(previous)
            if (
                previous > window["close"]
                or nxt < window["open"]
                or (window["close"] - previous).total_seconds() > 24 * 3600
                or (nxt - window["open"]).total_seconds() > 24 * 3600
            ):
                continue
            windows_by_close[int(window["close"].timestamp())] = window

    active = current_weekend_window(now)
    active_formatted = None
    if active is not None:
        windows_by_close[int(active["close"].timestamp())] = active
        active_formatted = {
            "close": format_utc(active["close"]),
            "open": format_utc(active["open"]),
            "note": (
                "Dotted weekend chart segments carry forward the last traded price "
                "while WTI futures are closed; they are not live weekend quotes."
            ),
        }

    formatted_windows = [
        {
            "close": format_utc(windows_by_close[key]["close"]),
            "open": format_utc(windows_by_close[key]["open"]),
        }
        for key in sorted(windows_by_close)
    ]
    return formatted_windows, active_formatted


def build_news_sentiment(records: list[dict], reference_time: datetime) -> dict:
    articles_by_id: dict[str, dict] = {}
    for record in records:
        collected_at = record.get("collected_at")
        analysis_status = (record.get("news_analysis") or {}).get(
            "status", "not_configured"
        )
        for article in record.get("news") or []:
            if not isinstance(article, dict) or not article.get("id"):
                continue
            entry = {
                "id": article["id"],
                "source": article.get("source"),
                "title": article.get("title"),
                "url": article.get("url"),
                "published_at": article.get("published_at"),
                "first_seen_at": article.get("first_seen_at") or collected_at,
                "summary": article.get("summary"),
                "analysis": article.get("analysis"),
                "analysis_history_count": len(article.get("analysis_history") or []),
                "analysis_status": (
                    "completed"
                    if isinstance(article.get("analysis"), dict)
                    else analysis_status
                ),
            }
            articles_by_id[article["id"]] = entry

    sorted_articles = sorted(
        articles_by_id.values(),
        key=lambda item: (
            parse_time(item.get("published_at") or item.get("first_seen_at"))
            or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )

    counts = {direction: 0 for direction in ALLOWED_DIRECTIONS}
    counts["unassessed"] = 0

    recent_cutoff = reference_time - timedelta(hours=24)
    recent_counts = {direction: 0 for direction in ALLOWED_DIRECTIONS}
    recent_counts["unassessed"] = 0
    recent_total = 0

    weighted_num = 0.0
    weighted_den = 0.0

    for article in sorted_articles:
        analysis = article.get("analysis")
        direction = (
            analysis.get("direction") if isinstance(analysis, dict) else None
        )
        bucket = direction if direction in ALLOWED_DIRECTIONS else "unassessed"
        counts[bucket] += 1

        if isinstance(analysis, dict) and direction in ALLOWED_DIRECTIONS:
            confidence = analysis.get("confidence")
            weight = (
                float(confidence) / 100.0
                if isinstance(confidence, (int, float)) and 0 <= confidence <= 100
                else 0.5
            )
            score = 1.0 if direction == "bullish" else -1.0 if direction == "bearish" else 0.0
            weighted_num += score * weight
            weighted_den += weight

        pub_dt = parse_time(article.get("published_at") or article.get("first_seen_at"))
        if pub_dt is not None and pub_dt >= recent_cutoff:
            recent_total += 1
            recent_counts[bucket] += 1

    assessed_total = sum(counts[d] for d in ALLOWED_DIRECTIONS)
    net_score = (
        round((counts["bullish"] - counts["bearish"]) / assessed_total, 4)
        if assessed_total > 0
        else 0.0
    )
    confidence_weighted_score = (
        round(weighted_num / weighted_den, 4) if weighted_den > 0 else 0.0
    )

    dominant_direction = "unassessed"
    if assessed_total > 0:
        dominant_direction = max(
            ALLOWED_DIRECTIONS,
            key=lambda d: (counts[d], 1 if d in ("bullish", "bearish") else 0),
        )

    return {
        "summary": {
            "total_stories": len(sorted_articles),
            "assessed_stories": assessed_total,
            "by_direction": counts,
            "dominant_direction": dominant_direction,
            "net_sentiment_score": net_score,
            "confidence_weighted_sentiment_score": confidence_weighted_score,
            "recent_24h": {
                "total": recent_total,
                **recent_counts,
            },
        },
        "articles": sorted_articles,
    }


def build_api_payload(
    data_dir: Path, *, now: datetime | None = None
) -> dict:
    reference_time = now or datetime.now(timezone.utc)
    generated_at = format_utc(reference_time)
    records = load_records(data_dir)
    latest_record = records[-1] if records else None
    latest_prices = (latest_record.get("prices") if latest_record else None) or {}

    prices_snapshot: dict[str, dict] = {}
    direction_compression: dict[str, object] = {
        "description": (
            "Compressed directional regimes (run-length encoded vs previous daily close), "
            "direction inflection points, point-to-point step changes, and multi-horizon "
            "OHLC window summaries (24h, 7d, 30d, all)."
        )
    }
    raw_series_by_key: dict[str, list[dict]] = {}
    dedup_series_by_key: dict[str, list[list[object]]] = {}

    for key, label, default_symbol in BENCHMARKS:
        quote = latest_prices.get(key)
        validity = assess_price_validity(quote, reference_time)
        if isinstance(quote, dict):
            prices_snapshot[key] = {
                "name": label,
                "symbol": quote.get("symbol") or default_symbol,
                "price": quote.get("price"),
                "currency": quote.get("currency", "USD"),
                "unit": quote.get("unit", "barrel"),
                "price_timestamp": quote.get("price_timestamp"),
                "previous_close": quote.get("previous_close"),
                "change": quote.get("change"),
                "change_percent": quote.get("change_percent"),
                "change_basis": quote.get("change_basis", "previous_daily_close"),
                "price_change_sentiment": quote.get(
                    "price_change_sentiment", validity["direction"]
                ),
                "validity": validity,
                "source": quote.get("source"),
                "provider_observations": quote.get("provider_observations", []),
            }
            if quote.get("provider_errors"):
                prices_snapshot[key]["provider_errors"] = quote["provider_errors"]
        else:
            prices_snapshot[key] = {
                "name": label,
                "symbol": default_symbol,
                "price": None,
                "currency": "USD",
                "unit": "barrel",
                "price_timestamp": None,
                "previous_close": None,
                "change": None,
                "change_percent": None,
                "change_basis": "previous_daily_close",
                "price_change_sentiment": "unknown",
                "validity": validity,
                "source": None,
                "provider_observations": [],
            }

        raw_points, dedup_obs = build_benchmark_series(records, key, reference_time)
        raw_series_by_key[key] = raw_points
        direction_compression[key] = compress_price_directions(raw_points, dedup_obs)
        dedup_series_by_key[key] = [
            [
                obs["price_timestamp"],
                obs["price"],
                obs["previous_close"],
                obs["change_percent"],
                obs["direction"],
                obs["validity"],
                obs["repeat_count"],
            ]
            for obs in dedup_obs
        ]

    weekend_windows, active_weekend = compute_weekend_windows(
        raw_series_by_key, reference_time
    )

    all_times = [
        pt["time"]
        for series in raw_series_by_key.values()
        for pt in series
    ]
    time_range = (
        {
            "start": format_utc(min(all_times)),
            "end": format_utc(max(all_times)),
        }
        if all_times
        else {"start": None, "end": None}
    )

    news_sentiment = build_news_sentiment(records, reference_time)

    return {
        "schema_version": 1,
        "endpoint": ENDPOINT_URL,
        "generated_at": generated_at,
        "latest_collection_at": (
            latest_record.get("collected_at") if latest_record else None
        ),
        "total_collections_analyzed": len(records),
        "market_status": {
            "is_weekend_closed": active_weekend is not None,
            "active_weekend_window": active_weekend,
        },
        "prices": prices_snapshot,
        "price_direction_compression": direction_compression,
        "chart_data": {
            "time_range": time_range,
            "weekend_windows": weekend_windows,
            "deduplicated_series": {
                "columns": [
                    "price_timestamp",
                    "price",
                    "previous_close",
                    "change_percent",
                    "direction",
                    "validity",
                    "repeat_count",
                ],
                **dedup_series_by_key,
            },
        },
        "news_sentiment": news_sentiment,
    }


GATEWAY_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>Oil Signal Agent API</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0d151a;
      --panel: #142128;
      --line: #2b3e47;
      --text: #e7f0ec;
      --muted: #8fa5a7;
      --green: #76d6a0;
      --red: #ff8d82;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 20px;
      background: var(--bg);
      color: var(--text);
      font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .auth-bar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
      margin-bottom: 14px;
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel);
    }
    .auth-bar label {
      color: var(--muted);
      font-weight: 700;
    }
    .auth-bar input {
      flex: 1 1 260px;
      padding: 7px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--bg);
      color: var(--text);
      font: inherit;
    }
    .auth-bar button {
      padding: 7px 14px;
      border: 1px solid var(--green);
      border-radius: 6px;
      background: #1b3a31;
      color: var(--green);
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    .auth-bar button:hover {
      filter: brightness(1.15);
    }
    pre {
      margin: 0;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel);
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }
  </style>
</head>
<body>
  <form class="auth-bar" id="api-key-form">
    <label for="api-key-input">OIL_SIGNAL_API_KEY:</label>
    <input id="api-key-input" type="password" placeholder="Paste API key to decrypt payload…" autocomplete="off" spellcheck="false">
    <button type="submit">Decrypt API Payload</button>
  </form>
  <pre id="oil-signal-api-json" aria-live="polite">{"status":"authenticating","endpoint":"https://knightkalman.github.io/oil-signal/api/"}</pre>
  <script>
    "use strict";

    const output = document.querySelector("#oil-signal-api-json");
    const form = document.querySelector("#api-key-form");
    const input = document.querySelector("#api-key-input");

    function writeJson(payload) {
      window.OIL_SIGNAL_API_DATA = payload;
      output.textContent = JSON.stringify(payload, null, 2);
    }

    function readApiKey() {
      const searchParams = new URLSearchParams(window.location.search);
      const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ""));
      return (
        searchParams.get("api_key") ||
        searchParams.get("key") ||
        hashParams.get("api_key") ||
        hashParams.get("key") ||
        ""
      ).trim();
    }

    function base64ToBytes(b64) {
      const binary = atob(b64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
      }
      return bytes;
    }

    async function decryptEnvelope(envelope, apiKey) {
      const enc = envelope.encryption;
      if (!enc || enc.algorithm !== "AES-256-GCM" || enc.kdf !== "PBKDF2-HMAC-SHA256") {
        throw new Error("Unsupported envelope encryption parameters");
      }
      const salt = base64ToBytes(enc.salt_b64);
      const nonce = base64ToBytes(enc.nonce_b64);
      const tag = base64ToBytes(enc.tag_b64);
      const ciphertext = base64ToBytes(envelope.ciphertext_b64);

      const combined = new Uint8Array(ciphertext.length + tag.length);
      combined.set(ciphertext, 0);
      combined.set(tag, ciphertext.length);

      const keyMaterial = await crypto.subtle.importKey(
        "raw",
        new TextEncoder().encode(apiKey),
        { name: "PBKDF2" },
        false,
        ["deriveKey"]
      );
      const cryptoKey = await crypto.subtle.deriveKey(
        {
          name: "PBKDF2",
          salt,
          iterations: Number(enc.iterations),
          hash: "SHA-256"
        },
        keyMaterial,
        { name: "AES-GCM", length: 256 },
        false,
        ["decrypt"]
      );
      const decryptedBuffer = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: nonce, tagLength: 128 },
        cryptoKey,
        combined
      );
      return JSON.parse(new TextDecoder().decode(decryptedBuffer));
    }

    async function loadAndDecrypt(apiKey) {
      if (!apiKey) {
        writeJson({
          status: 401,
          error: "Unauthorized: API key required",
          endpoint: "https://knightkalman.github.io/oil-signal/api/",
          usage: {
            manual_input: "Enter your OIL_SIGNAL_API_KEY in the input box above and click Decrypt API Payload",
            url_query_or_hash: "https://knightkalman.github.io/oil-signal/api/?api_key=<OIL_SIGNAL_API_KEY>",
            direct_encrypted_envelope: "https://knightkalman.github.io/oil-signal/api/index.json",
            encryption: "AES-256-GCM with PBKDF2-HMAC-SHA256 (210000 iterations)"
          }
        });
        return;
      }

      writeJson({ status: "decrypting", endpoint: "https://knightkalman.github.io/oil-signal/api/" });
      try {
        const response = await fetch(`index.json?t=${Date.now()}`, { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`Envelope request failed (${response.status})`);
        }
        const envelope = await response.json();
        if (envelope.status === "unauthorized_stub" || !envelope.ciphertext_b64) {
          writeJson({
            status: 503,
            error: envelope.error || "Encrypted API payload is not currently configured."
          });
          return;
        }
        const payload = await decryptEnvelope(envelope, apiKey);
        if (window.location.search.includes("api_key=") || window.location.search.includes("key=")) {
          const cleanUrl = new URL(window.location.href);
          cleanUrl.searchParams.delete("api_key");
          cleanUrl.searchParams.delete("key");
          window.history.replaceState({}, document.title, cleanUrl.toString());
        }
        writeJson(payload);
      } catch (error) {
        writeJson({
          status: 403,
          error: "Forbidden: Invalid API key or corrupted encrypted envelope",
          endpoint: "https://knightkalman.github.io/oil-signal/api/"
        });
      }
    }

    form.addEventListener("submit", event => {
      event.preventDefault();
      loadAndDecrypt(input.value.trim());
    });

    const initialKey = readApiKey();
    if (initialKey) {
      input.value = initialKey;
    }
    loadAndDecrypt(initialKey);
  </script>
</body>
</html>
"""


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def build_api_artifacts(
    data_dir: Path,
    output_dir: Path,
    api_key: str | None,
    *,
    now: datetime | None = None,
    require_key: bool = False,
    iterations: int = PBKDF2_ITERATIONS,
) -> dict:
    reference_time = now or datetime.now(timezone.utc)
    generated_at = format_utc(reference_time)
    output_dir.mkdir(parents=True, exist_ok=True)
    envelope_path = output_dir / "index.json"
    gateway_path = output_dir / "index.html"

    cleaned_key = (api_key or "").strip()
    if not cleaned_key:
        if require_key:
            raise ValueError("OIL_SIGNAL_API_KEY is required but was not provided")
        stub = {
            "schema_version": 1,
            "endpoint": ENDPOINT_URL,
            "auth_required": True,
            "status": "unauthorized_stub",
            "generated_at": generated_at,
            "error": "OIL_SIGNAL_API_KEY is not configured; API payload was not published.",
        }
        atomic_write_text(
            envelope_path,
            json.dumps(stub, ensure_ascii=False, separators=(",", ":")) + "\n",
        )
        atomic_write_text(gateway_path, GATEWAY_HTML)
        LOGGER.warning(stub["error"])
        return stub

    payload = build_api_payload(data_dir, now=reference_time)
    envelope = encrypt_payload(
        payload,
        cleaned_key,
        generated_at=generated_at,
        iterations=iterations,
    )
    atomic_write_text(
        envelope_path,
        json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n",
    )
    atomic_write_text(gateway_path, GATEWAY_HTML)
    return envelope


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the API-key-encrypted Oil Signal API for GitHub Pages."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/oil"),
        help="Directory containing daily JSONL collection files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("site/api"),
        help="Output directory for static API artifacts (index.json and index.html).",
    )
    parser.add_argument(
        "--require-key",
        action="store_true",
        help="Exit with an error if OIL_SIGNAL_API_KEY is not set.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    api_key = os.environ.get("OIL_SIGNAL_API_KEY")
    envelope = build_api_artifacts(
        args.data_dir,
        args.output_dir,
        api_key,
        require_key=args.require_key,
    )
    LOGGER.info(
        "Built Oil Signal API at %s (status=%s)",
        args.output_dir,
        envelope.get("status"),
    )


if __name__ == "__main__":
    main()

