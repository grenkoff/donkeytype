# donkeytype

A monkeytype-style typing trainer for **listening comprehension**. You don't
see the text: you hear an American English sentence and type what you heard.

- **Sentences**: ~1.15M sentences from [Tatoeba](https://tatoeba.org), only
  those written by native English speakers, American spelling only.
  Optionally topped up by an LLM.
- **Voice**: [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), a free
  open model that runs locally on the CPU with 20 American voices.
- **Vocabulary grows by frequency.** You start with the ~100 most frequent
  words. A word means a lemma with all its forms (*go, goes, went, gone,
  going*). When almost everything unlocked is known and your recent accuracy
  is high, the next batch of words unlocks.
- **Phrases count too**: idioms, phrasal verbs and set phrases from Wiktionary,
  plus frequent chunks found in the corpus (*I don't know*, *a lot of*,
  *give up*, *figure out*, *looking forward to*...).
- **Spaced repetition**: new and difficult items come up more often. Learned
  ones come back on a growing schedule: rarely, but they never disappear.

---

## Quick start

You need **git** and **[uv](https://docs.astral.sh/uv/)**. uv installs the right
Python (3.12) by itself. About 1.5 GB of free disk space is needed.

```bash
git clone https://github.com/grenkoff/donkeytype.git
cd donkeytype
uv sync                   # creates .venv and installs dependencies (~1 GB incl. spaCy, onnxruntime)
uv run donkeytype-setup   # downloads the voice model (~340 MB) and the prebuilt corpus (~190 MB -> 460 MB)
uv run donkeytype         # starts the server
```

Open **http://127.0.0.1:8765** in a browser and press any key.

### Windows (PowerShell)

```powershell
# 1. Tools (skip what is already installed)
winget install --id Git.Git -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# open a NEW terminal so git and uv are on PATH, then:

# 2. Project
git clone https://github.com/grenkoff/donkeytype.git
cd donkeytype
uv sync
uv run donkeytype-setup
uv run donkeytype
```

Then open http://127.0.0.1:8765. Stop the server with `Ctrl+C`. Everything is
pure Python (no compilers, no system packages). The `espeak-ng` phonemizer that
Kokoro needs ships inside the `espeakng-loader` wheel.

---

## How to use

| key | action |
| --- | --- |
| letters, `'` | type what you hear (case and punctuation are ignored) |
| `space` | next word |
| `backspace` / `ctrl+backspace` | delete a letter / the whole word |
| `tab` | replay the sentence |
| `shift+tab` | replay slowly |
| `enter` | check the sentence; on the result screen: next sentence |
| `esc` | give up and show the answer |

On the result screen each word is colored: correct, typo (small spelling
slip, partial credit), wrong or missing. Chips below show items that were
**new**, just **learned**, or **missed**. There is also a link to the sentence on
Tatoeba.

**Settings** (top right):
- **vocabulary level**: how many of the most frequent words are in play. If the
  first 100 words are too easy, set 1000 or more. It grows automatically.
- **speech speed**: Kokoro synthesis speed (0.6-1.3).
- **live feedback**: color letters while typing, monkeytype style. If off, you
  only see the result after `enter`.
- **show word slots**: dots show how many words and letters to type. Turn
  this off for real dictation.
- **voices**: a random one is picked for each sentence, so you hear different speakers.

**Stats** shows accuracy and WPM over time, the items you struggle with, and
recently learned ones.

Progress is stored in `data/progress.db`. Delete it to start over.

---

## Optional: LLM top-up

At high levels, some unlocked words or phrases may have too few corpus
sentences. With an API key for any OpenAI-compatible endpoint, a background
thread asks the model for simple sentences. It keeps only sentences where the
target occurs and every word is within your level. The Settings page shows how
many items lack sentences. Up to level ~1000 none do, so this is not needed
to start.

```bash
# Groq (free tier, default endpoint)
DONKEY_LLM_API_KEY=gsk_... uv run donkeytype

# Gemini (free tier)
DONKEY_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai \
DONKEY_LLM_MODEL=gemini-2.5-flash DONKEY_LLM_API_KEY=... uv run donkeytype
```

PowerShell: `$env:DONKEY_LLM_API_KEY="gsk_..."; uv run donkeytype`

---

## Commands

| command | what it does |
| --- | --- |
| `uv run donkeytype [--host H] [--port P]` | run the web app (default 127.0.0.1:8765) |
| `uv run donkeytype-setup` | download the Kokoro model and the prebuilt corpus (skips files that exist) |
| `uv run donkeytype-setup --build` | same, but build the corpus from sources instead of downloading it |
| `uv run donkeytype-build [--force tokenize\|lexicon\|write]` | (re)build `data/corpus.db` from Tatoeba + Wiktionary; ~30-60 min |
| `uv run pytest` | run tests |

## Files

```
data/                       (not in git, created by setup)
  models/kokoro-v1.0.onnx   voice model
  models/voices-v1.0.bin    voice styles
  corpus.db                 sentences, words, phrases (read-mostly)
  progress.db               your progress and settings
  audio/                    cache of synthesized sentences (safe to delete)
  raw/, build/              only when building the corpus yourself
src/donkeytype/
  app.py          FastAPI server: API + static UI, queue of pre-synthesized sentences
  selector.py     picks a target item by weight, then the best-fitting sentence
  learner.py      item memory model (strength, spaced repetition), level-up rule
  grading.py      aligns typed words with the reference, tolerates typos
  tts.py          Kokoro synthesis with disk cache, background worker
  llm.py          optional LLM top-up of sentences
  analysis.py     maps spaCy tokens to typed words, finds word/phrase items
  text.py         normalization, British spelling filter
  store.py        SQLite access
  setup.py        downloads models and corpus
  build/corpus.py corpus build pipeline
  build/wiktionary.py  fetches Wiktionary multiword entry titles
web/              index.html, app.js, style.css (no build step)
tests/
```

### How the corpus is built

1. Tatoeba English sentences are filtered: authors with native English, 3-14
   words, no digits, no British spellings, no template-heavy contributions.
2. spaCy (`en_core_web_sm`) lemmatizes them. Every token is mapped to the word
   the user types (*don't* = one typed word carrying *do* + *not*).
3. Lemmas are ranked by the geometric mean of their wordfreq frequency
   (general English) and their Tatoeba frequency (conversational). This
   favors everyday spoken words.
4. Phrases come from Wiktionary categories (multiword terms, idioms, phrasal
   verbs, phrases, prepositional phrases) matched by lemma sequence, plus
   corpus n-grams with high association (PMI). Fragments like *to the* or
   *is very* are filtered out. A phrase unlocks when all its words are
   unlocked and its own frequency matches that level.
5. Every sentence stores its items with their word positions and its
   "difficulty" (the rank of its rarest word). At level N only sentences with
   difficulty <= N are used.

### How learning works

- After each sentence, each item gets a score from how its words were typed:
  1.0 correct, 0.7 corrected while typing, 0.6 typo, 0 wrong or missing. Many
  replays reduce the score.
- Strength is an exponential average of these scores. An item is **known**
  after 3 correct answers with strength >= 0.75.
- Correct answers given when an item is due advance its review interval:
  10 min, 1 h, 6 h, 1 d, 3 d, 7 d, 16 d, 35 d, 80 d. A failure resets it.
- Selection weight: new items 6, learning 2-8 (weaker = higher), due 1-3,
  known and not due 0.1. Known, not-due items get at most 20% of the total weight.
- Level up when >= 85% of unlocked items are known, at most ~8% are
  unfinished, and accuracy over the last attempts is >= 85%. The step is 20
  words, or 5% of the level when that is larger.

---

## Notes for AI assistants setting this up

Follow these steps in order and verify each one.

1. **Prerequisites**: `git --version` and `uv --version` must work. If uv is
   missing on Windows, install it with
   `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.
   Then open a new shell, or add `%USERPROFILE%\.local\bin` to PATH for the
   current session. Do not install Python separately: uv reads
   `.python-version` (3.12) and downloads it.
2. **`uv sync`** must finish without errors. It installs the spaCy model
   `en_core_web_sm` from a GitHub wheel URL pinned in `pyproject.toml`.
3. **`uv run donkeytype-setup`** downloads into `data/`. Afterwards these files
   must exist: `data/models/kokoro-v1.0.onnx` (~310 MB),
   `data/models/voices-v1.0.bin` (~27 MB), `data/corpus.db` (~460 MB). If a
   download was interrupted, delete the leftover `*.part` file and rerun. Do
   **not** run `donkeytype-build` unless the corpus download is impossible: it
   takes a long time and is not needed.
4. **Tests**: `uv run pytest` should report all tests passing.
5. **Smoke test** before handing over to the user. Start the server in the
   background (`uv run donkeytype`), wait for `donkeytype: http://127.0.0.1:8765`,
   then:
   - `curl http://127.0.0.1:8765/api/state` returns JSON with `"level": 100`.
     On PowerShell use `curl.exe` or `Invoke-RestMethod`.
   - `curl http://127.0.0.1:8765/api/next` returns `{"id": ..., "words": [...], "audio": "/api/audio/<name>.ogg"}`.
   - Fetching that audio URL returns an `audio/ogg` file of a few KB. The
     first synthesis loads the model and can take ~10 s.

   These calls do not record progress (only submitting an answer does), so
   the server can be left running for the user.
6. Tell the user to open http://127.0.0.1:8765 and press any key. The browser
   blocks audio until the first key press. If audio does not start, press `tab`.

**Troubleshooting**

- *Port in use*: `uv run donkeytype --port 8766`.
- *Kokoro/espeak errors on first synthesis*: run
  `uv run python -c "from kokoro_onnx import Kokoro; k=Kokoro('data/models/kokoro-v1.0.onnx','data/models/voices-v1.0.bin'); print(len(k.create('Hello there.', voice='af_heart', lang='en-us')[0]))"`.
  It must print a number. If it fails with an espeak-related error, run
  `uv sync --reinstall-package espeakng-loader`.
- *No sound*: check system audio and the browser tab's audio permissions.
  Chrome and Firefox both play OGG/Opus. The server log shows
  `/api/audio/...` requests.
- *Slow synthesis*: Kokoro runs on the CPU and needs ~1-3 s per sentence on an
  older laptop. The app synthesizes the next two sentences in advance, so a
  pause only happens if you answer very fast.
- *`data/corpus.db` not found*: run `uv run donkeytype-setup`.

## Credits and licenses

- Sentences: [Tatoeba](https://tatoeba.org), CC BY 2.0 FR. Each result screen
  links to the source sentence.
- Phrase lists: titles of English multiword entries on
  [Wiktionary](https://en.wiktionary.org) (CC BY-SA).
- Word frequencies: [wordfreq](https://github.com/rspeer/wordfreq).
- Voice: [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (Apache 2.0)
  via [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx).
- Lemmatization: [spaCy](https://spacy.io).
