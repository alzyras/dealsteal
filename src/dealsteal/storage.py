"""Small SQLite persistence layer for resumable scans and cached observations."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Listing


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                stats_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS listings (
                item_id TEXT PRIMARY KEY,
                observed_at TEXT NOT NULL,
                listing_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS http_cache (
                cache_key TEXT PRIMARY KEY,
                fetched_at TEXT NOT NULL,
                status INTEGER NOT NULL,
                content_type TEXT,
                body BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS exchange_rates (
                as_of TEXT NOT NULL,
                currency TEXT NOT NULL,
                rate TEXT NOT NULL,
                PRIMARY KEY (as_of, currency)
            );
            CREATE TABLE IF NOT EXISTS deals (
                deal_key TEXT PRIMARY KEY,
                observed_at TEXT NOT NULL,
                deal_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS listings_observed_at ON listings(observed_at);
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def start_scan(self) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = self.connection.execute(
            "INSERT INTO scans(started_at, status) VALUES (?, ?)", (now, "running")
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_scan(self, scan_id: int, status: str, stats: dict[str, Any]) -> None:
        self.connection.execute(
            "UPDATE scans SET finished_at=?, status=?, stats_json=? WHERE id=?",
            (datetime.now(UTC).isoformat(), status, json.dumps(stats), scan_id),
        )
        self.connection.commit()

    def save_listing(self, listing: Listing) -> None:
        observed = datetime.now(UTC).isoformat()
        self.connection.execute(
            """
            INSERT INTO listings(item_id, observed_at, listing_json) VALUES (?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET observed_at=excluded.observed_at,
            listing_json=excluded.listing_json
            """,
            (
                listing.item_id,
                observed,
                json.dumps(listing.as_dict(), ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def save_deal(self, deal: dict[str, Any]) -> None:
        listing = deal.get("listing", {})
        deal_key = f"{listing.get('item_id', 'unknown')}:{deal.get('profile_id', 'unknown')}:{deal.get('tier_id', 'unknown')}"
        self.connection.execute(
            """
            INSERT INTO deals(deal_key, observed_at, deal_json) VALUES (?, ?, ?)
            ON CONFLICT(deal_key) DO UPDATE SET observed_at=excluded.observed_at,
            deal_json=excluded.deal_json
            """,
            (
                deal_key,
                datetime.now(UTC).isoformat(),
                json.dumps(deal, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def get_cached(self, cache_key: str, max_age_seconds: int) -> bytes | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT fetched_at, body FROM http_cache WHERE cache_key=?",
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        fetched = datetime.fromisoformat(row["fetched_at"])
        age = (datetime.now(UTC) - fetched).total_seconds()
        if age > max_age_seconds:
            return None
        return bytes(row["body"])

    def put_cached(
        self,
        cache_key: str,
        body: bytes,
        status: int = 200,
        content_type: str = "text/html",
    ) -> None:
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO http_cache(cache_key, fetched_at, status, content_type, body)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET fetched_at=excluded.fetched_at,
                status=excluded.status, content_type=excluded.content_type, body=excluded.body
                """,
                (
                    cache_key,
                    datetime.now(UTC).isoformat(),
                    status,
                    content_type,
                    body,
                ),
            )
            self.connection.commit()

    def recent_listings(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT listing_json FROM listings ORDER BY observed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [json.loads(row["listing_json"]) for row in rows]

    def recent_deals(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT deal_json FROM deals ORDER BY observed_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [json.loads(row["deal_json"]) for row in rows]
