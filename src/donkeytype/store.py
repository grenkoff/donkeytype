"""SQLite access: the read-mostly corpus and the user's progress."""

import json
import sqlite3
import threading
import time
from pathlib import Path

from . import config
from .learner import ItemState

PROGRESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS item_state (
    item_key TEXT PRIMARY KEY,
    strength REAL NOT NULL, seen INTEGER NOT NULL, correct INTEGER NOT NULL,
    streak INTEGER NOT NULL, lapses INTEGER NOT NULL,
    first_seen REAL, last_seen REAL, due REAL
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL, sentence_id INTEGER NOT NULL, text TEXT NOT NULL, typed TEXT NOT NULL,
    accuracy REAL NOT NULL, wpm REAL, replays INTEGER NOT NULL, level INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- LLM-made sentences live in corpus.db too; kept here so a corpus rebuild can re-import them.
CREATE TABLE IF NOT EXISTS llm_sentences (text TEXT PRIMARY KEY, target TEXT, created REAL);
"""

DEFAULT_SETTINGS = {
    "level": 100,
    "voices": ["af_heart", "af_bella", "am_michael", "am_fenrir", "am_puck"],
    "speed": 1.0,
    "live_feedback": True,
    "show_mask": True,
}
STATE_FIELDS = ["strength", "seen", "correct", "streak", "lapses", "first_seen", "last_seen", "due"]


class Store:
    def __init__(self, corpus: Path = config.CORPUS_DB, progress: Path = config.PROGRESS_DB):
        if not corpus.exists():
            raise SystemExit(f"{corpus} not found: build it first with `uv run donkeytype-build`")
        self.lock = threading.RLock()
        self.corpus = sqlite3.connect(corpus, check_same_thread=False)
        self.progress = sqlite3.connect(progress, check_same_thread=False)
        self.progress.executescript(PROGRESS_SCHEMA)
        self._items: dict[int, dict] | None = None

    # --- corpus -----------------------------------------------------------------

    @property
    def items(self) -> dict[int, dict]:
        if self._items is None:
            with self.lock:
                rows = self.corpus.execute("SELECT id, key, kind, label, rank, forms, avail_rank FROM items")
                self._items = {
                    r[0]: {"id": r[0], "key": r[1], "kind": r[2], "label": r[3], "rank": r[4],
                           "forms": json.loads(r[5]) if r[5] else [], "avail_rank": r[6]}
                    for r in rows
                }
        return self._items

    def reload_items(self) -> None:
        self._items = None

    def unlocked(self, level: int) -> list[dict]:
        """Unlocked items that have enough sentences at this level to be practiced."""
        return [i for i in self.items.values() if i["rank"] <= level and (i["avail_rank"] or 10**9) <= level]

    def starved(self, level: int) -> list[dict]:
        """Unlocked items without enough sentences at this level (LLM candidates)."""
        return sorted(
            (i for i in self.items.values() if i["rank"] <= level and (i["avail_rank"] or 10**9) > level),
            key=lambda i: i["rank"],
        )

    def sentence_ids_with(self, item_id: int, level: int, rand_from: int, limit: int) -> list[int]:
        q = (
            "SELECT sentence_id FROM sentence_items WHERE item_id = ? AND rand_key {} ?"
            " AND max_rank <= ? ORDER BY rand_key LIMIT ?"
        )
        with self.lock:
            ids = [r[0] for r in self.corpus.execute(q.format(">="), (item_id, rand_from, level, limit))]
            if len(ids) < limit:
                ids += [r[0] for r in self.corpus.execute(q.format("<"), (item_id, rand_from, level, limit - len(ids)))]
        return ids

    def sentences(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        with self.lock:
            rows = self.corpus.execute(
                f"SELECT id, text, words, items, n_words, source, source_id FROM sentences"
                f" WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            ).fetchall()
        return [
            {"id": r[0], "text": r[1], "words": json.loads(r[2]), "items": json.loads(r[3]),
             "n_words": r[4], "source": r[5], "source_id": r[6]}
            for r in rows
        ]

    # --- progress ---------------------------------------------------------------

    def settings(self) -> dict:
        with self.lock:
            stored = {k: json.loads(v) for k, v in self.progress.execute("SELECT key, value FROM settings")}
        return {**DEFAULT_SETTINGS, **stored}

    def save_settings(self, values: dict) -> None:
        with self.lock:
            self.progress.executemany(
                "INSERT OR REPLACE INTO settings VALUES (?, ?)",
                [(k, json.dumps(v)) for k, v in values.items() if k in DEFAULT_SETTINGS],
            )
            self.progress.commit()

    def states(self, keys: list[str] | None = None) -> dict[str, ItemState]:
        with self.lock:
            rows = self.progress.execute(f"SELECT item_key, {', '.join(STATE_FIELDS)} FROM item_state")
            states = {r[0]: ItemState(r[0], *r[1:]) for r in rows}
        if keys is not None:
            return {k: states.get(k) or ItemState(k) for k in keys}
        return states

    def save_states(self, states: list[ItemState]) -> None:
        with self.lock:
            self.progress.executemany(
                f"INSERT OR REPLACE INTO item_state VALUES (?, {', '.join('?' * len(STATE_FIELDS))})",
                [(s.key, *(getattr(s, f) for f in STATE_FIELDS)) for s in states],
            )
            self.progress.commit()

    def add_attempt(self, sentence: dict, typed: str, accuracy: float, wpm: float | None,
                    replays: int, level: int) -> None:
        with self.lock:
            self.progress.execute(
                "INSERT INTO attempts (ts, sentence_id, text, typed, accuracy, wpm, replays, level)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), sentence["id"], sentence["text"], typed, accuracy, wpm, replays, level),
            )
            self.progress.commit()

    def recent_attempts(self, limit: int) -> list[dict]:
        with self.lock:
            rows = self.progress.execute(
                "SELECT ts, sentence_id, accuracy, wpm, replays, level FROM attempts ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(zip(["ts", "sentence_id", "accuracy", "wpm", "replays", "level"], r)) for r in rows]
