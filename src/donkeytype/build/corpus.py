"""Build data/corpus.db from Tatoeba sentences, wordfreq and Wiktionary phrases.

Stages (each cached on disk, rerun with --force to redo):
  1. download   Tatoeba English sentences + user language levels, Wiktionary phrase titles
  2. tokenize   filter sentences (native speakers, length, US spelling) and run spaCy
  3. lexicon    rank lemmas by frequency, collect phrases (Wiktionary + corpus n-grams)
  4. write      map every sentence to its items and store everything in SQLite
"""

import argparse
import bz2
import csv
import json
import math
import random
import re
import sqlite3
import tarfile
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from wordfreq import word_frequency

from .. import config
from ..analysis import (
    CONTRACTIONS,
    Lexicon,
    describe,
    item_lemma,
    nlp,
    segments,
    tokenize,
)
from ..text import clean, is_british, word_spans
from ..setup import download as fetch
from .wiktionary import fetch_all as fetch_wiktionary

BUILD = config.DATA / "build"
TOKENS = BUILD / "tokens.jsonl"
LEXICON = BUILD / "lexicon.json"
WIKTIONARY = config.RAW / "wiktionary_mwe.json"
TATOEBA_URL = "https://downloads.tatoeba.org/exports/per_language/eng/eng_sentences_detailed.tsv.bz2"
USERS_URL = "https://downloads.tatoeba.org/exports/user_languages.tar.bz2"

MIN_WORDS, MAX_WORDS = 3, 14
ALLOWED_CHARS = re.compile(r"[A-Za-z ,.'?!\-\";:]+")
FREE_NAMES = 40  # most common corpus names never block a sentence
MIN_LEMMA_COUNT = 3
MAX_SENTENCE_RANK = 25000
MIN_WIKT_PHRASE_COUNT = 8
MIN_NGRAM_COUNT = 60
MIN_NGRAM_PMI = 1.5
COMMON_RANK = 300  # phrases made only of words this common need MIN_NGRAM_PMI
MAX_NGRAM = 5
# Template-heavy contributions (mostly about Algeria with invented names).
EXCLUDED_OWNERS = {"Amastan"}
EXCLUDED_WORDS = {"ziri", "yanni", "rima", "skura", "algeria", "algerian", "algerians", "algiers",
                  "kabyle", "kabylie", "berber", "berbers", "tamazight", "mennad", "baya"}
PLACEHOLDERS = {"one's", "someone", "someone's", "something", "somebody", "oneself", "sb", "sth"}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --- 1. download -----------------------------------------------------------------


def download() -> None:
    config.RAW.mkdir(parents=True, exist_ok=True)
    sentences = config.RAW / "eng_sentences_detailed.tsv.bz2"
    if not sentences.exists():
        log("downloading Tatoeba sentences")
        fetch(TATOEBA_URL, sentences)
    if not (config.RAW / "user_languages.csv").exists():
        log("downloading Tatoeba user languages")
        archive = config.RAW / "user_languages.tar.bz2"
        fetch(USERS_URL, archive)
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extract("user_languages.csv", config.RAW, filter="data")
    if not WIKTIONARY.exists():
        log("fetching Wiktionary phrase titles (takes a few minutes)")
        fetch_wiktionary(WIKTIONARY)


# --- 2. tokenize -----------------------------------------------------------------


def native_speakers() -> set[str]:
    with open(config.RAW / "user_languages.csv", newline="", encoding="utf-8") as f:
        return {
            row[2]
            for row in csv.reader(f, delimiter="\t")
            if len(row) >= 3 and row[0] == "eng" and row[1] == "5"
        }


def candidate_sentences():
    natives = native_speakers()
    seen: set[str] = set()
    with bz2.open(config.RAW / "eng_sentences_detailed.tsv.bz2", "rt", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4 or parts[3] not in natives or parts[3] in EXCLUDED_OWNERS:
                continue
            text = clean(parts[2])
            if not ALLOWED_CHARS.fullmatch(text):
                continue
            words = [w for _, _, w in word_spans(text)]
            if (
                not MIN_WORDS <= len(words) <= MAX_WORDS
                or any(map(is_british, words))
                or EXCLUDED_WORDS.intersection(words)
            ):
                continue
            key = " ".join(words)
            if key in seen:
                continue
            seen.add(key)
            yield int(parts[0]), text


def tokenize_corpus() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    sentences = list(candidate_sentences())
    log(f"tokenizing {len(sentences)} sentences")
    tmp = TOKENS.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as out:
        texts = (text for _, text in sentences)
        for i, doc in enumerate(nlp().pipe(texts, batch_size=1000, n_process=1 if sys.platform == "win32" else 3)):
            tid, text = sentences[i]
            out.write(json.dumps([tid, text, tokenize(doc)], separators=(",", ":")) + "\n")
            if i % 200_000 == 0:
                log(f"  {i}")
    tmp.replace(TOKENS)


_excluded: set[int] | None = None


def excluded_ids() -> set[int]:
    """Sentences dropped by filters added after tokens.jsonl was made."""
    global _excluded
    if _excluded is None:
        _excluded = set()
        with bz2.open(config.RAW / "eng_sentences_detailed.tsv.bz2", "rt", encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 4 and (
                    parts[3] in EXCLUDED_OWNERS or EXCLUDED_WORDS.intersection(word_spans_lower(parts[2]))
                ):
                    _excluded.add(int(parts[0]))
    return _excluded


def word_spans_lower(text: str) -> list[str]:
    return [w for _, _, w in word_spans(clean(text))]


def read_tokens():
    excluded = excluded_ids()
    with open(TOKENS, encoding="utf-8") as f:
        for line in f:
            tid, text, tokens = json.loads(line)
            if tid not in excluded:
                yield tid, text, [tuple(t) for t in tokens]


# --- 3. lexicon ------------------------------------------------------------------


def rank_lemmas() -> tuple[dict, dict, list[float], Counter, dict]:
    """Rank lemmas by summed wordfreq frequency of the forms that realize them."""
    form_lemma: Counter = Counter()
    propn: Counter = Counter()
    for _, _, tokens in read_tokens():
        for tok in tokens:
            if tok[3] == "PROPN":
                propn[tok[1]] += 1
            elif (lemma := item_lemma(tok)) is not None:
                form_lemma[tok[1], lemma] += 1
    form_total: Counter = Counter()
    lemma_count: Counter = Counter()
    for (form, lemma), c in form_lemma.items():
        form_total[form] += c
        lemma_count[lemma] += c
    # Clitics like "n't" or "'re" have no wordfreq entry: scale their corpus counts.
    wf = {form: word_frequency(form, "en") for form in form_total}
    known = [f for f in form_total if wf[f] > 0]
    scale = sum(wf[f] for f in known) / sum(form_total[f] for f in known)
    general: Counter = Counter()
    for (form, lemma), c in form_lemma.items():
        freq = wf[form] or form_total[form] * scale
        general[lemma] += freq * c / form_total[form]
    # wordfreq leans on written text (news, Wikipedia); Tatoeba is conversational.
    # The geometric mean favors words common in both, i.e. in everyday speech.
    corpus_total = sum(lemma_count.values())
    lemma_freq = Counter({l: math.sqrt(general[l] * lemma_count[l] / corpus_total) for l in general})
    ranked = [l for l, _ in lemma_freq.most_common() if lemma_count[l] >= MIN_LEMMA_COUNT]
    ranks = {lemma: i + 1 for i, lemma in enumerate(ranked)}
    freqs = [lemma_freq[l] for l in ranked]
    forms: dict[str, Counter] = defaultdict(Counter)
    for (form, lemma), c in form_lemma.items():
        if lemma in ranks:
            forms[lemma][form] += c
    free = {name: 0 for name, _ in propn.most_common(FREE_NAMES)}
    log(f"ranked {len(ranks)} lemmas; free names: {', '.join(list(free)[:15])}...")
    return ranks, forms, freqs, Counter({l: lemma_count[l] for l in ranks}), free


def wiktionary_phrases() -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    """(category, lemma key, surface key) of Wiktionary multiword entries."""
    data = json.loads(WIKTIONARY.read_text(encoding="utf-8"))
    titles: dict[str, str] = {}
    for kind in ("multiword", "phrase", "prep_phrase", "idiom", "phrasal_verb"):
        for title in data[kind]:
            title = clean(title)
            words = [w for _, _, w in word_spans(title)]
            if (
                len(words) < 2
                or not ALLOWED_CHARS.fullmatch(title)
                or any(w[0].isupper() and w not in ("I", "I'm", "I'd", "I'll", "I've") for w in title.split())
                or any(w in PLACEHOLDERS for w in words)
            ):
                continue
            titles[title] = kind  # later categories override the generic "multiword"
    result = []
    for doc in nlp().pipe(titles, batch_size=2000):
        tokens = [t for t in tokenize(doc) if t[0] != -1]
        result.append((titles[doc.text], tuple(t[2] for t in tokens), tuple(t[1] for t in tokens)))
    log(f"wiktionary: {len(result)} usable titles")
    return result


def ngram_counts() -> tuple[dict[int, Counter], int]:
    """Counts of frequent lemma n-grams (all their sub-n-grams are frequent too)."""
    counts: dict[int, Counter] = {}
    total = 0
    for n in range(1, MAX_NGRAM + 1):
        c: Counter = Counter()
        prev = counts.get(n - 1)
        for _, _, tokens in read_tokens():
            for seg in segments(tokens):
                lemmas = [t[2] for t in seg]
                if n == 1:
                    total += len(lemmas)
                for i in range(len(lemmas) - n + 1):
                    g = tuple(lemmas[i : i + n])
                    if prev is None or (g[:-1] in prev and g[1:] in prev):
                        c[g] += 1
        counts[n] = Counter({g: v for g, v in c.items() if v >= MIN_NGRAM_COUNT})
        log(f"  {n}-grams with count >= {MIN_NGRAM_COUNT}: {len(counts[n])}")
    return counts, total


def min_split_pmi(g: tuple[str, ...], counts: dict[int, Counter], total: int) -> float | None:
    """How much better a lemma sequence holds together than its weakest split; None if unknown."""
    n = len(g)
    if n > MAX_NGRAM or g not in counts[n]:
        return None
    return min(
        math.log(counts[n][g] * total / (counts[k][g[:k]] * counts[n - k][g[k:]]))
        for k in range(1, n)
    )


FUNCTION_WORDS = set("""
    a an the this that these those some any no every each all both either neither another such what which who
    whom whose i you he she it we they me him her us them my your his its our their mine yours myself
    be have do will would shall should can could may might must not to of in on at by for with from about
    as into onto than then and or but if because so though although while when where how why there here
    up out off over very too also just still really even only well yes oh
""".split())
BAD_ENDINGS = set("""
    the a an this that i he she we they my your his her its our their very so too really still also just
    no and or but because
""".split())


def is_fragment(g: tuple[str, ...], kind: str = "chunk") -> bool:
    """Spans like "to the", "said he", "is very" or "to get" are not phrases worth learning."""
    if kind in ("idiom", "phrasal_verb"):
        return False
    if "do that" in " ".join(g):  # Tatoeba's countless "... to do that" templates
        return True
    if g[-1] in BAD_ENDINGS or g[0] in {"and", "or", "but", "that"}:
        return True
    if kind == "chunk":
        return (
            g[-1] in {"be", "have", "do"}
            or (len(g) == 2 and g[0] in {"to", "a", "the"})
            or all(w in FUNCTION_WORDS for w in g)
        )
    return False

def build_lexicon() -> None:
    ranks, forms, freqs, lemma_count, free = rank_lemmas()
    # Phrase groups: every key (lemma or surface sequence) of a group matches it.
    groups: list[dict] = [{}]  # group ids start at 1
    keys: dict[tuple[str, ...], int] = {}
    for kind, lemma_key, surface_key in wiktionary_phrases():
        if lemma_key in keys or len(lemma_key) < 2:
            continue
        keys[lemma_key] = keys.setdefault(surface_key, len(groups))
        groups.append({"kind": kind, "lemmas": lemma_key, "keys": {lemma_key, surface_key}})
    counts, total = ngram_counts()
    for n in range(2, MAX_NGRAM + 1):
        for key in counts[n]:
            if key not in keys and not is_fragment(key) and min_split_pmi(key, counts, total) >= MIN_NGRAM_PMI:
                keys[key] = len(groups)
                groups.append({"kind": "chunk", "lemmas": key, "keys": {key}})

    # Count phrase occurrences (sentences) and their surface forms.
    lex = Lexicon(lemmas={l: (0, r) for l, r in ranks.items()}, propn=free, lemma_freqs=freqs)
    lex.phrases = keys
    lex.max_phrase_len = max(map(len, keys))
    sentence_count: Counter = Counter()
    surfaces: dict[int, Counter] = defaultdict(Counter)
    for _, text, tokens in read_tokens():
        spans = word_spans(text)
        _, items = describe(tokens, lex)
        seen = set()
        for gid, ws, we in items:
            if gid and gid not in seen:  # lemma items all have id 0 here
                seen.add(gid)
                sentence_count[gid] += 1
                surfaces[gid][text[spans[ws][0] : spans[we][1]].lower()] += 1

    # Phrases unlock when their words are unlocked and their own frequency
    # matches the frequency of lemmas at that rank (by corpus counts).
    counts_desc = sorted(lemma_count.values(), reverse=True)

    def rank_of_count(c: int) -> int:
        lo, hi = 0, len(counts_desc)
        while lo < hi:
            mid = (lo + hi) // 2
            if counts_desc[mid] >= c:
                lo = mid + 1
            else:
                hi = mid
        return lo + 1

    def lemma_rank(lemma: str) -> int:
        return ranks.get(CONTRACTIONS.get(lemma, lemma), lex.unknown_rank)

    by_label: dict[str, dict] = {}
    for gid, c in sentence_count.most_common():
        group = groups[gid]
        if c < (MIN_NGRAM_COUNT if group["kind"] == "chunk" else MIN_WIKT_PHRASE_COUNT):
            continue
        words_rank = max(map(lemma_rank, group["lemmas"]))
        if words_rank >= lex.unknown_rank:
            continue
        label = surfaces[gid].most_common(1)[0][0]
        if len(word_spans(label)) < 2:
            continue  # "don't", "I'm": one typed word, already covered by its lemmas
        lemmas = group["lemmas"]
        if group["kind"] != "chunk":
            # Wiktionary entries made of very common words ("be in", "to go") match
            # literal uses everywhere: keep them only if they really hold together.
            pmi = min_split_pmi(lemmas, counts, total)
            if is_fragment(lemmas, group["kind"]):
                continue
            if all(w in FUNCTION_WORDS for w in lemmas) and group["kind"] == "multiword":
                continue
            if words_rank <= COMMON_RANK and pmi is not None and pmi < MIN_NGRAM_PMI:
                continue
        if label in by_label:  # e.g. "look up" and "look-up": merge into the more frequent
            by_label[label]["keys"] += [list(k) for k in group["keys"]]
            continue
        by_label[label] = {
            "keys": [list(k) for k in group["keys"]], "label": label, "kind": group["kind"],
            "count": c, "rank": max(words_rank, rank_of_count(c)),
        }
    # Drop chunks that almost only occur inside a longer phrase ("lot of" in "a lot of").
    kept = list(by_label.values())
    best_super: Counter = Counter()  # sub-phrase label -> count of its most frequent super-phrase
    for p in kept:
        w = p["label"].split()
        for i in range(len(w)):
            for j in range(i + 2, len(w) + 1):
                if j - i < len(w):
                    sub = " ".join(w[i:j])
                    best_super[sub] = max(best_super[sub], p["count"])
    phrases = [p for p in kept if p["kind"] != "chunk" or best_super[p["label"]] < 0.8 * p["count"]]
    log(f"phrases kept: {len(phrases)} ({Counter(p['kind'] for p in phrases)})")

    LEXICON.write_text(json.dumps({
        "lemmas": [
            {"lemma": l, "rank": r, "count": lemma_count[l],
             "forms": [f for f, _ in forms[l].most_common(8)]}
            for l, r in sorted(ranks.items(), key=lambda x: x[1])
        ],
        "freqs": freqs,
        "free_names": list(free),
        "phrases": phrases,
    }), encoding="utf-8")


# --- 4. write --------------------------------------------------------------------

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE items (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,      -- "w:give" or "p:give up"
    kind TEXT NOT NULL,            -- word | idiom | phrasal_verb | phrase | ... | chunk
    label TEXT NOT NULL,           -- display form
    rank INTEGER NOT NULL,         -- unlock order, in lemma-rank units
    forms TEXT,                    -- JSON list of frequent surface forms
    avail_rank INTEGER             -- level at which >= 3 sentences contain the item
);
CREATE TABLE phrase_keys (key TEXT PRIMARY KEY, item_id INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE propn (name TEXT PRIMARY KEY, rank INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE sentences (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    words TEXT NOT NULL,           -- JSON list of normalized words to type
    items TEXT NOT NULL,           -- JSON list of [item id, first word, last word]
    n_words INTEGER NOT NULL,
    max_rank INTEGER NOT NULL,
    source TEXT NOT NULL,          -- tatoeba | llm
    source_id INTEGER
);
CREATE TABLE sentence_items (
    item_id INTEGER NOT NULL,
    rand_key INTEGER NOT NULL,
    max_rank INTEGER NOT NULL,
    sentence_id INTEGER NOT NULL,
    PRIMARY KEY (item_id, rand_key, sentence_id)
) WITHOUT ROWID;
"""


def load_lexicon(db: sqlite3.Connection) -> Lexicon:
    lemmas = {}
    for item_id, key, rank in db.execute("SELECT id, key, rank FROM items WHERE kind = 'word'"):
        lemmas[key[2:]] = (item_id, rank)
    meta = dict(db.execute("SELECT key, value FROM meta"))
    lex = Lexicon(
        lemmas=lemmas,
        propn=dict(db.execute("SELECT name, rank FROM propn")),
        lemma_freqs=json.loads(meta["lemma_freqs"]),
        max_phrase_len=int(meta["max_phrase_len"]),
    )
    lex.phrases = {tuple(k.split(" ")): i for k, i in db.execute("SELECT key, item_id FROM phrase_keys")}
    return lex


def insert_sentence(db, lex: Lexicon, text: str, tokens, source: str, source_id=None) -> int | None:
    max_rank, items = describe(tokens, lex)
    if not items or max_rank > MAX_SENTENCE_RANK:
        return None
    words = [w for _, _, w in word_spans(text)]
    cur = db.execute(
        "INSERT INTO sentences (text, words, items, n_words, max_rank, source, source_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (text, json.dumps(words), json.dumps(items), len(words), max_rank, source, source_id),
    )
    sid = cur.lastrowid
    db.executemany(
        "INSERT OR IGNORE INTO sentence_items VALUES (?, ?, ?, ?)",
        [(i, random.getrandbits(31), max_rank, sid) for i in {i for i, _, _ in items}],
    )
    return sid


def update_avail_ranks(db: sqlite3.Connection) -> None:
    """avail_rank = the level at which the item appears in at least 3 sentences."""
    db.execute("""
        UPDATE items SET avail_rank = (
            SELECT max_rank FROM sentence_items si WHERE si.item_id = items.id
            ORDER BY max_rank LIMIT 1 OFFSET 2)
    """)
    db.execute("""
        UPDATE items SET avail_rank = (SELECT max(max_rank) FROM sentence_items si WHERE si.item_id = items.id)
        WHERE avail_rank IS NULL
    """)


def write_db() -> None:
    data = json.loads(LEXICON.read_text(encoding="utf-8"))
    tmp = config.CORPUS_DB.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    rows = [(f"w:{l['lemma']}", "word", l["lemma"], l["rank"], json.dumps(l["forms"])) for l in data["lemmas"]]
    rows += [(f"p:{p['label']}", p["kind"], p["label"], p["rank"], None) for p in data["phrases"]]
    db.executemany("INSERT INTO items (key, kind, label, rank, forms) VALUES (?, ?, ?, ?, ?)", rows)
    ids = dict(db.execute("SELECT key, id FROM items"))
    db.executemany(
        "INSERT OR IGNORE INTO phrase_keys VALUES (?, ?)",
        [(" ".join(k), ids[f"p:{p['label']}"]) for p in data["phrases"] for k in p["keys"]],
    )
    db.executemany("INSERT INTO propn VALUES (?, 0)", [(n,) for n in data["free_names"]])
    db.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("lemma_freqs", json.dumps(data["freqs"])),
        ("max_phrase_len", str(max((len(k) for p in data["phrases"] for k in p["keys"]), default=2))),
    ])
    lex = load_lexicon(db)

    log("writing sentences")
    seen: set[tuple] = set()
    kept = 0
    for tid, text, tokens in read_tokens():
        # Tatoeba has many "Tom/Mary ..." twins: keep one per name-less word sequence.
        sig = tuple("_" if t[3] == "PROPN" else t[1] for t in tokens if t[0] != -1)
        if sig in seen:
            continue
        seen.add(sig)
        if insert_sentence(db, lex, text, tokens, "tatoeba", tid):
            kept += 1
    log(f"sentences kept: {kept}")
    update_avail_ranks(db)
    db.execute("CREATE INDEX sentences_rank ON sentences (max_rank)")
    db.commit()
    db.execute("VACUUM")
    db.close()
    tmp.replace(config.CORPUS_DB)
    log(f"wrote {config.CORPUS_DB}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", choices=["tokenize", "lexicon", "write"], help="redo from this stage")
    args = parser.parse_args()
    stages = ["tokenize", "lexicon", "write"]
    redo = set(stages[stages.index(args.force):]) if args.force else set()
    download()
    if "tokenize" in redo or not TOKENS.exists():
        tokenize_corpus()
    if "lexicon" in redo or not LEXICON.exists():
        build_lexicon()
    if "write" in redo or not config.CORPUS_DB.exists():
        write_db()


if __name__ == "__main__":
    main()
