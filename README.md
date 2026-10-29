# donkeytype

A monkeytype-style typing trainer for **listening**: you don't see the text,
you hear an American English sentence and type what you heard.

- Sentences come from [Tatoeba](https://tatoeba.org) (only sentences by native
  English speakers, American spelling), optionally topped up by an LLM.
- Speech is synthesized locally by [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M)
  with American voices (free, offline).
- Vocabulary grows by frequency. You start with the ~100 most frequent words
  (a word = a lemma with all its forms: *go, goes, went, gone, going*). When
  nearly everything unlocked is known and your accuracy is high, more words unlock.
- Besides single words it tracks **phrases**: idioms, phrasal verbs and set
  phrases from Wiktionary, plus frequent chunks found in the corpus
  (*I don't know*, *a lot of*, *give up*, *at the same time*...).
- New and difficult items appear more often; learned ones follow a
  spaced-repetition schedule and show up rarely, but never disappear.

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
# Kokoro model files (~340 MB)
mkdir -p data/models && cd data/models
curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
cd ../..
# Build the corpus: downloads Tatoeba and Wiktionary phrase lists, runs spaCy (~30 min)
uv run donkeytype-build
```

## Run

```bash
uv run donkeytype            # http://127.0.0.1:8765
```

Keys: type what you hear, <kbd>space</kbd> between words, <kbd>tab</kbd>
replays, <kbd>shift</kbd>+<kbd>tab</kbd> replays slowly, <kbd>enter</kbd>
checks the sentence and moves on. Case and punctuation are ignored.

Settings let you set the starting vocabulary level (e.g. 1000 if top-100 is
too easy), speech speed, voices, live letter feedback and word slots.

## Optional: LLM top-up

Some unlocked words or phrases have too few corpus sentences at your level.
With an API key for any OpenAI-compatible endpoint, a background thread asks
the model for simple sentences and keeps only those whose every word is
within your level:

```bash
# Groq (free tier) is the default endpoint
DONKEY_LLM_API_KEY=... uv run donkeytype
# Gemini (free tier)
DONKEY_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai \
DONKEY_LLM_MODEL=gemini-2.5-flash DONKEY_LLM_API_KEY=... uv run donkeytype
```

## How it works

| module | role |
| --- | --- |
| `build/corpus.py` | filters Tatoeba, lemmatizes with spaCy, ranks lemmas by wordfreq, extracts phrases, writes `data/corpus.db` |
| `analysis.py` | maps spaCy tokens onto typed words and finds word/phrase items in a sentence |
| `learner.py` | item memory model (strength, spaced repetition) and level-up rule |
| `selector.py` | picks a target item by weight, then the best-fitting sentence for it |
| `grading.py` | aligns typed words with the reference, tolerating typos |
| `tts.py` | Kokoro synthesis with disk cache; the next sentences are synthesized ahead |
| `app.py` | FastAPI server and API; `web/` is the UI |

Progress is stored in `data/progress.db`.

## Credits

Sentences: Tatoeba, CC BY 2.0 FR. Phrase lists: Wiktionary (titles of
multiword entries). Word frequencies: [wordfreq](https://github.com/rspeer/wordfreq).
