"""Compare what the user typed with the reference sentence, word by word."""

from dataclasses import dataclass

from .text import words


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance where swapping two adjacent letters is one edit."""
    d = [[i + j if i * j == 0 else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def is_typo(ref: str, typed: str) -> bool:
    """Close enough to count as a spelling slip rather than a different word."""
    allowed = 1 if len(ref) <= 6 else 2
    return len(ref) >= 3 and edit_distance(ref, typed) <= allowed


@dataclass
class WordResult:
    ref: str | None  # None for an extra typed word
    typed: str | None  # None for a missing word
    status: str  # correct | corrected | typo | wrong | missing | extra
    score: float


SCORES = {"correct": 1.0, "corrected": 0.7, "typo": 0.6, "wrong": 0.0, "missing": 0.0, "extra": 0.0}


def align(ref: list[str], typed: list[str]) -> list[tuple[int | None, int | None]]:
    """Word alignment minimizing edits; substitution cost grows with spelling distance."""

    def sub(i: int, j: int) -> float:
        if ref[i] == typed[j]:
            return 0.0
        return 0.5 + 1.5 * edit_distance(ref[i], typed[j]) / max(len(ref[i]), len(typed[j]))

    n, m = len(ref), len(typed)
    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = float(i)
    for j in range(1, m + 1):
        cost[0][j] = float(j)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(cost[i - 1][j] + 1, cost[i][j - 1] + 1, cost[i - 1][j - 1] + sub(i - 1, j - 1))
    pairs: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i or j:
        if i and j and cost[i][j] == cost[i - 1][j - 1] + sub(i - 1, j - 1):
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i and cost[i][j] == cost[i - 1][j] + 1:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    return pairs[::-1]


def grade(ref: list[str], typed_text: str, corrected: list[bool] | None = None) -> list[WordResult]:
    """`corrected[i]` marks reference words the user got wrong at first and fixed (live mode)."""
    typed = words(typed_text.replace("-", " "))
    corrected = corrected or []
    results = []
    for i, j in align(ref, typed):
        if i is None:
            results.append(WordResult(None, typed[j], "extra", 0.0))
            continue
        if j is None:
            status = "missing"
        elif ref[i] == typed[j]:
            status = "corrected" if i < len(corrected) and corrected[i] else "correct"
        else:
            status = "typo" if is_typo(ref[i], typed[j]) else "wrong"
        results.append(WordResult(ref[i], None if j is None else typed[j], status, SCORES[status]))
    return results


def word_scores(results: list[WordResult]) -> list[float]:
    """Score per reference word, in reference order."""
    return [r.score for r in results if r.ref is not None]


def accuracy(results: list[WordResult]) -> float:
    ref_count = sum(r.ref is not None for r in results)
    extra = sum(r.status == "extra" for r in results)
    return max(0.0, sum(r.score for r in results) - 0.5 * extra) / max(1, ref_count)
