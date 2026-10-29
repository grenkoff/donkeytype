"""Sentence analysis: spaCy tokens mapped onto typed words, lexicon lookups.

A sentence is reduced to a list of tokens (word index, lowercased text, lemma,
POS). Word indexes refer to `text.word_spans`, i.e. to the words the user
types, so "don't" is one word carrying two lemma items (do, not).
"""

import re
from dataclasses import dataclass, field
from functools import cache

from wordfreq import word_frequency

from .text import word_spans

BOUNDARY_RE = re.compile(r"[.,;:!?\"()\[\]…]")
LEMMA_RE = re.compile(r"[a-z]+(?:-[a-z]+)*")
CONTRACTIONS = {"'m": "be", "'re": "be", "'ve": "have", "'ll": "will", "'d": "would"}
NON_ITEM_POS = {"PROPN", "PUNCT", "SYM", "X", "SPACE", "NUM"}
NUMBER_WORDS = set(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty "
    "sixty seventy eighty ninety hundred thousand million billion".split()
)

# (word index, lower text, lemma, pos); word index -1 marks a phrase boundary.
Token = tuple[int, str, str, str]
BOUNDARY: Token = (-1, "", "", "PUNCT")


@cache
def nlp():
    import spacy

    return spacy.load("en_core_web_sm", disable=["parser", "ner"])


def tokenize(doc) -> list[Token]:
    spans = word_spans(doc.text)
    out: list[Token] = []
    j = 0
    for t in doc:
        while j < len(spans) and spans[j][1] <= t.idx:
            j += 1
        if j < len(spans) and spans[j][0] <= t.idx:
            out.append((j, t.text.lower(), t.lemma_.lower(), t.pos_))
        elif BOUNDARY_RE.search(t.text) and out and out[-1][0] != -1:
            out.append(BOUNDARY)
    if out and out[-1][0] == -1:
        out.pop()
    return out


def item_lemma(tok: Token) -> str | None:
    """The lemma a token contributes as a learnable word, if any."""
    _, lower, lemma, pos = tok
    if lemma in CONTRACTIONS:
        lemma = CONTRACTIONS[lemma]
    elif lemma == "'s":
        lemma = "be" if pos == "AUX" else ""
    if pos == "NUM" and lower in NUMBER_WORDS:
        return lower
    if pos in NON_ITEM_POS or not LEMMA_RE.fullmatch(lemma):
        return None
    return lemma


def segments(tokens: list[Token]) -> list[list[Token]]:
    """Runs of tokens between punctuation and proper nouns (phrases stay inside)."""
    out: list[list[Token]] = [[]]
    for tok in tokens:
        if tok[0] == -1 or tok[3] == "PROPN":
            if out[-1]:
                out.append([])
        else:
            out[-1].append(tok)
    return [s for s in out if s]


@dataclass
class Lexicon:
    lemmas: dict[str, tuple[int, int]]  # lemma -> (item id, rank)
    propn: dict[str, int]  # proper noun (lower) -> rank; 0 = always allowed
    lemma_freqs: list[float]  # wordfreq-based frequency per rank, descending
    phrases: dict[tuple[str, ...], int] = field(default_factory=dict)  # lemma/surface key -> item id
    max_phrase_len: int = 6
    unknown_rank: int = 10**9

    def propn_rank(self, lower: str) -> int:
        if lower in self.propn:
            return self.propn[lower]
        return self.rank_of_freq(word_frequency(lower, "en"))

    def rank_of_freq(self, freq: float) -> int:
        lo, hi = 0, len(self.lemma_freqs)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.lemma_freqs[mid] >= freq:
                lo = mid + 1
            else:
                hi = mid
        return lo + 1 if lo < len(self.lemma_freqs) else self.unknown_rank


def describe(tokens: list[Token], lex: Lexicon) -> tuple[int, list[tuple[int, int, int]]]:
    """Max lemma rank of a sentence and its item occurrences (item id, first word, last word)."""
    max_rank = 0
    items: list[tuple[int, int, int]] = []
    for tok in tokens:
        if tok[0] == -1:
            continue
        if tok[3] == "PROPN":
            max_rank = max(max_rank, lex.propn_rank(tok[1]))
            continue
        lemma = item_lemma(tok)
        if lemma is None:
            continue
        if lemma not in lex.lemmas:
            return lex.unknown_rank, []
        item_id, rank = lex.lemmas[lemma]
        max_rank = max(max_rank, rank)
        items.append((item_id, tok[0], tok[0]))
    for seg in segments(tokens):
        lemmas = [t[2] for t in seg]
        lowers = [t[1] for t in seg]
        for n in range(2, min(lex.max_phrase_len, len(seg)) + 1):
            for i in range(len(seg) - n + 1):
                item_id = lex.phrases.get(tuple(lemmas[i : i + n])) or lex.phrases.get(
                    tuple(lowers[i : i + n])
                )
                if item_id:
                    items.append((item_id, seg[i][0], seg[i + n - 1][0]))
    return max_rank, items
