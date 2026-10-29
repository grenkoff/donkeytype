"""Kokoro-82M speech synthesis with an on-disk cache and a background worker."""

import hashlib
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from . import config


class TTS:
    def __init__(self, cache: Path = config.AUDIO_CACHE):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        # One worker: onnxruntime already uses every core for a single sentence.
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")
        self.jobs: dict[str, Future] = {}
        self.lock = threading.Lock()
        self._kokoro = None

    @property
    def kokoro(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro

            self._kokoro = Kokoro(str(config.KOKORO_MODEL), str(config.KOKORO_VOICES))
        return self._kokoro

    def voices(self) -> list[str]:
        return [v for v in self.kokoro.get_voices() if v[:3] in ("af_", "am_")]

    @staticmethod
    def name(text: str, voice: str, speed: float) -> str:
        digest = hashlib.sha1(f"{voice}|{speed:.2f}|{text}".encode()).hexdigest()[:20]
        return f"{digest}.ogg"

    def path(self, name: str) -> Path:
        return self.cache / name

    def request(self, text: str, voice: str, speed: float) -> str:
        """Start synthesis in the background (if not cached) and return the file name."""
        name = self.name(text, voice, speed)
        with self.lock:
            if not self.path(name).exists() and name not in self.jobs:
                self.jobs[name] = self.pool.submit(self._synth, text, voice, speed, name)
        return name

    def wait(self, name: str) -> Path | None:
        with self.lock:
            job = self.jobs.get(name)
        if job is not None:
            job.result()
            with self.lock:
                self.jobs.pop(name, None)
        path = self.path(name)
        return path if path.exists() else None

    def _synth(self, text: str, voice: str, speed: float, name: str) -> None:
        import soundfile as sf

        samples, rate = self.kokoro.create(text, voice=voice, speed=speed, lang="en-us")
        tmp = self.path(name).with_suffix(".tmp")
        sf.write(tmp, samples, rate, format="OGG", subtype="OPUS")
        tmp.replace(self.path(name))
