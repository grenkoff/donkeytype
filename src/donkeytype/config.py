import os
from pathlib import Path

ROOT = Path(os.environ.get("DONKEY_ROOT", Path(__file__).resolve().parents[2]))
DATA = ROOT / "data"
RAW = DATA / "raw"
MODELS = DATA / "models"
AUDIO_CACHE = DATA / "audio"
CORPUS_DB = DATA / "corpus.db"
PROGRESS_DB = DATA / "progress.db"
WEB = ROOT / "web"

KOKORO_MODEL = MODELS / "kokoro-v1.0.onnx"
KOKORO_VOICES = MODELS / "voices-v1.0.bin"

# Optional LLM for sentences the corpus lacks; any OpenAI-compatible endpoint
# (Groq, Gemini, OpenRouter, a local server...). Disabled without an API key.
LLM_BASE_URL = os.environ.get("DONKEY_LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_API_KEY = os.environ.get("DONKEY_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("DONKEY_LLM_MODEL", "llama-3.3-70b-versatile")
