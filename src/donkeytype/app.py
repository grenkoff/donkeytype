"""Web server: serves the trainer UI and its JSON API (single local user)."""

import logging
import random
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, llm
from .grading import accuracy, grade, word_scores
from .learner import level_step, ready_to_level_up
from .selector import describe_items, pick_sentence
from .store import DEFAULT_SETTINGS, Store
from .text import word_spans
from .tts import TTS

log = logging.getLogger("donkeytype")
QUEUE_AHEAD = 2  # sentences selected and synthesized in advance
RECENT_EXCLUDE = 400  # don't repeat a sentence within this many attempts
LEVEL_WINDOW = 10  # attempts considered for leveling up


def display_words(text: str) -> list[str]:
    """The original text cut into one chunk per typed word, punctuation included."""
    spans = word_spans(text)
    cuts = [0] + [start for start, _, _ in spans[1:]] + [len(text)]
    return [text[a:b].strip() for a, b in zip(cuts, cuts[1:])]


class Trainer:
    def __init__(self, store: Store, tts: TTS, filler: llm.Filler):
        self.store, self.tts, self.filler = store, tts, filler
        self.queue: deque[dict] = deque()
        self.active: dict[int, dict] = {}
        self.recent: deque[int] = deque(
            (a["sentence_id"] for a in reversed(store.recent_attempts(RECENT_EXCLUDE))), maxlen=RECENT_EXCLUDE
        )
        self.lock = threading.Lock()
        self.planner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="planner")

    def _plan(self) -> dict | None:
        settings = self.store.settings()
        with self.lock:
            exclude = set(self.recent) | {s["id"] for s in self.queue} | set(self.active)
        s = pick_sentence(self.store, settings["level"], exclude)
        if s is None:
            return None
        s["level"] = settings["level"]
        s["voice"] = random.choice(settings["voices"] or DEFAULT_SETTINGS["voices"])
        s["speed"] = settings["speed"]
        s["audio"] = self.tts.request(s["text"], s["voice"], s["speed"])
        return s

    def _top_up(self) -> None:
        with self.lock:
            missing = QUEUE_AHEAD - len(self.queue)
        for _ in range(missing):
            s = self._plan()
            if s is None:
                break
            with self.lock:
                self.queue.append(s)

    def top_up(self) -> None:
        self.planner.submit(self._top_up)

    def reset_queue(self) -> None:
        with self.lock:
            self.queue.clear()
        self.top_up()

    def next(self) -> dict:
        settings = self.store.settings()
        with self.lock:
            s = self.queue.popleft() if self.queue else None
        if s is None or s["speed"] != settings["speed"] or s["voice"] not in settings["voices"]:
            s = self._plan()
        if s is None:
            raise HTTPException(503, "no sentences available at this level")
        with self.lock:
            self.active[s["id"]] = s
        self.top_up()
        return s

    def submit(self, sentence_id: int, typed: str, corrected: list[bool], replays: int, elapsed_ms: int) -> dict:
        with self.lock:
            s = self.active.pop(sentence_id, None)
            self.recent.append(sentence_id)
        if s is None:
            found = self.store.sentences([sentence_id])
            if not found:
                raise HTTPException(404, "unknown sentence")
            s = found[0]
        settings = self.store.settings()
        level = settings["level"]
        now = time.time()

        results = grade(s["words"], typed, corrected)
        scores = word_scores(results)
        replay_factor = 1.0 if replays <= 1 else max(0.7, 1 - 0.1 * (replays - 1))
        perf: dict[int, float] = {}
        for item_id, ws, we in s["items"]:
            p = min(scores[ws : we + 1]) * replay_factor
            perf[item_id] = min(perf.get(item_id, 1.0), p)
        items = {i: self.store.items[i] for i in perf if i in self.store.items}
        states = self.store.states([it["key"] for it in items.values()])
        before = {k: st.status for k, st in states.items()}
        for item_id, item in items.items():
            states[item["key"]].update(perf[item_id], now)
        self.store.save_states(list(states.values()))

        acc = accuracy(results)
        minutes = elapsed_ms / 60000
        wpm = len(typed) / 5 / minutes if minutes > 0 and typed else None
        self.store.add_attempt(s, typed, acc, wpm, replays, level)

        leveled_up = self._maybe_level_up(level)
        return {
            "text": s["text"],
            "display": display_words(s["text"]),
            "words": [vars(r) for r in results],
            "accuracy": acc,
            "wpm": wpm,
            "items": [
                {**d, "before": before.get(d["key"], "new"), "performance": perf.get(d["id"], 0.0)}
                for d in describe_items(self.store, s, states)
            ],
            "level": self.store.settings()["level"],
            "leveled_up": leveled_up,
            "source": s["source"],
            "source_id": s["source_id"],
        }

    def _maybe_level_up(self, level: int) -> bool:
        attempts = [a for a in self.store.recent_attempts(LEVEL_WINDOW) if a["level"] == level]
        unlocked = self.store.unlocked(level)
        states = self.store.states([i["key"] for i in unlocked])
        if not ready_to_level_up([st.status for st in states.values()], [a["accuracy"] for a in attempts]):
            return False
        self.store.save_settings({"level": level + level_step(level)})
        self.filler.poke()
        return True

    def overview(self) -> dict:
        settings = self.store.settings()
        level = settings["level"]
        unlocked = self.store.unlocked(level)
        states = self.store.states([i["key"] for i in unlocked])
        counts = {"new": 0, "learning": 0, "known": 0}
        for st in states.values():
            counts[st.status] += 1
        all_states = self.store.states()
        return {
            "level": level,
            "counts": counts,
            "known_total": sum(st.status == "known" for st in all_states.values()),
            "starved": len(self.store.starved(level)),
            "settings": settings,
            "llm": llm.enabled(),
        }


store = Store()
tts = TTS()
filler = llm.Filler(store)
trainer = Trainer(store, tts, filler)
app = FastAPI(title="donkeytype")


@app.on_event("startup")
def startup() -> None:
    filler.start()
    trainer.top_up()


class Submission(BaseModel):
    sentence_id: int
    typed: str
    corrected: list[bool] = []
    replays: int = 0
    elapsed_ms: int = 0


class SettingsUpdate(BaseModel):
    level: int | None = None
    voices: list[str] | None = None
    speed: float | None = None
    live_feedback: bool | None = None
    show_mask: bool | None = None


@app.get("/api/state")
def get_state() -> dict:
    return trainer.overview()


@app.get("/api/next")
def get_next() -> dict:
    s = trainer.next()
    return {"id": s["id"], "words": s["words"], "voice": s["voice"], "audio": f"/api/audio/{s['audio']}"}


@app.post("/api/submit")
def post_submit(sub: Submission) -> dict:
    return trainer.submit(sub.sentence_id, sub.typed, sub.corrected, sub.replays, sub.elapsed_ms)


@app.get("/api/audio/{name}")
def get_audio(name: str):
    if not name.endswith(".ogg") or "/" in name:
        raise HTTPException(404)
    path = tts.wait(name)
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/ogg", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/voices")
def get_voices() -> list[str]:
    return tts.voices()


@app.post("/api/settings")
def post_settings(update: SettingsUpdate) -> dict:
    values = update.model_dump(exclude_none=True)
    if "level" in values:
        values["level"] = max(50, min(values["level"], 25000))
    if "speed" in values:
        values["speed"] = max(0.5, min(values["speed"], 1.5))
    if "voices" in values:
        values["voices"] = [v for v in values["voices"] if v in tts.voices()] or DEFAULT_SETTINGS["voices"]
    store.save_settings(values)
    if {"level", "voices", "speed"} & values.keys():
        trainer.reset_queue()
        filler.poke()
    return trainer.overview()


@app.get("/api/stats")
def get_stats() -> dict:
    level = store.settings()["level"]
    states = store.states()
    by_key = {i["key"]: i for i in store.items.values()}
    rows = []
    for key, st in states.items():
        item = by_key.get(key)
        if item:
            rows.append({"key": key, "label": item["label"], "kind": item["kind"], "rank": item["rank"],
                         "status": st.status, "strength": st.strength, "seen": st.seen, "lapses": st.lapses,
                         "unlocked": item["rank"] <= level})
    learning = sorted((r for r in rows if r["status"] == "learning" and r["unlocked"]), key=lambda r: r["strength"])
    return {
        "level": level,
        "struggling": learning[:40],
        "recent_known": sorted(
            (r for r in rows if r["status"] == "known"), key=lambda r: -states[r["key"]].first_seen
        )[:40],
        "attempts": list(reversed(store.recent_attempts(300))),
    }


app.mount("/", StaticFiles(directory=config.WEB, html=True), name="web")


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Listen-and-type English trainer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(f"donkeytype: http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
