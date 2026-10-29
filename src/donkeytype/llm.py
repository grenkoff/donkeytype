"""Optional sentence generation for items the corpus has too few sentences for.

Works with any OpenAI-compatible chat API (Groq and Gemini have free tiers).
Generated sentences are validated exactly like corpus sentences: every word
must be within the current level and the target item must really occur.
"""

import logging
import threading
import time

import httpx

from . import config
from .analysis import nlp, tokenize
from .build.corpus import insert_sentence, load_lexicon
from .store import Store
from .text import clean

log = logging.getLogger("donkeytype.llm")

PROMPT = """Write {n} different short, natural sentences in everyday American English.
Every sentence must contain "{target}" (any grammatical form is fine).
Use only very simple, common words: imagine a learner who knows only the {level} most frequent English words.
Sentences should be 4 to 10 words long. Use no names other than Tom and Mary. Write numbers as words.
Output one sentence per line, with no numbering or quotes."""


def enabled() -> bool:
    return bool(config.LLM_API_KEY)


def ask(target: str, level: int, n: int = 8) -> list[str]:
    resp = httpx.post(
        f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
        json={
            "model": config.LLM_MODEL,
            "messages": [{"role": "user", "content": PROMPT.format(n=n, target=target, level=level)}],
            "temperature": 0.9,
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return [clean(line.strip(" -*\"")) for line in text.splitlines() if line.strip()]


class Filler:
    """Background thread that tops up starved items at the current level."""

    def __init__(self, store: Store, per_round: int = 10, pause: float = 3.0):
        self.store = store
        self.per_round = per_round
        self.pause = pause
        self.wakeup = threading.Event()
        self.tried: set[tuple[int, int]] = set()  # (item id, level)
        self._lex = None

    def start(self) -> None:
        if enabled():
            threading.Thread(target=self._run, daemon=True, name="llm-filler").start()

    def poke(self) -> None:
        self.wakeup.set()

    def _run(self) -> None:
        while True:
            level = self.store.settings()["level"]
            todo = [i for i in self.store.starved(level) if (i["id"], level) not in self.tried]
            for item in todo[: self.per_round]:
                self.tried.add((item["id"], level))
                try:
                    added = self.fill(item, level)
                    log.info("llm: %s -> %d sentences", item["label"], added)
                except Exception as e:  # network/quota errors must not kill the app
                    log.warning("llm: %s failed: %s", item["label"], e)
                time.sleep(self.pause)
            self.wakeup.wait(timeout=600)
            self.wakeup.clear()

    def fill(self, item: dict, level: int) -> int:
        candidates = ask(item["label"], level)
        if self._lex is None:
            with self.store.lock:
                self._lex = load_lexicon(self.store.corpus)
        added = 0
        for doc in nlp().pipe(candidates):
            tokens = tokenize(doc)
            with self.store.lock:
                db = self.store.corpus
                if db.execute("SELECT 1 FROM sentences WHERE text = ?", (doc.text,)).fetchone():
                    continue
                sid = insert_sentence(db, self._lex, doc.text, tokens, "llm")
                if sid is None:
                    continue
                row = db.execute("SELECT max_rank FROM sentences WHERE id = ?", (sid,)).fetchone()
                has_target = db.execute(
                    "SELECT 1 FROM sentence_items WHERE sentence_id = ? AND item_id = ?", (sid, item["id"])
                ).fetchone()
                if row[0] > level or not has_target:
                    db.execute("DELETE FROM sentence_items WHERE sentence_id = ?", (sid,))
                    db.execute("DELETE FROM sentences WHERE id = ?", (sid,))
                    continue
                self.store.progress.execute(
                    "INSERT OR IGNORE INTO llm_sentences VALUES (?, ?, ?)", (doc.text, item["key"], time.time())
                )
                added += 1
            with self.store.lock:
                self.store.corpus.commit()
                self.store.progress.commit()
        if added:
            self.refresh_avail(item["id"], level)
        return added

    def refresh_avail(self, item_id: int, level: int) -> None:
        with self.store.lock:
            db = self.store.corpus
            n = db.execute(
                "SELECT count(*) FROM sentence_items WHERE item_id = ? AND max_rank <= ?", (item_id, level)
            ).fetchone()[0]
            if n:  # few sentences beat none: make the item practicable at this level
                db.execute("UPDATE items SET avail_rank = min(avail_rank, ?) WHERE id = ?", (level, item_id))
                db.commit()
        self.store.reload_items()
