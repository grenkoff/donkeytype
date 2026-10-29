"""Per-item memory model and level (vocabulary size) progression.

Every word (lemma, i.e. all its forms) and phrase is an item. An item is
"known" once it was typed correctly a few times with high recent accuracy;
after that a spaced-repetition schedule decides when it is due again. Known
items are never excluded, they just get a small weight until they are due.
"""

import math
from dataclasses import dataclass

# Review intervals (seconds) indexed by the streak of on-schedule successes.
INTERVALS = [0, 600, 3600, 6 * 3600, 86400, 3 * 86400, 7 * 86400, 16 * 86400, 35 * 86400, 80 * 86400]
ALPHA = 0.4  # how fast strength follows recent performance
KNOWN_STRENGTH = 0.75
KNOWN_CORRECT = 3
SUCCESS, PARTIAL = 0.85, 0.5

W_NEW = 6.0
W_LEARNING_MIN, W_LEARNING_MAX = 2.0, 8.0
W_DUE_MIN, W_DUE_MAX = 1.0, 3.0
W_RESTING = 0.1
RESTING_SHARE = 0.2  # max share of selection weight spent on known, not-due items


@dataclass
class ItemState:
    key: str
    strength: float = 0.0
    seen: int = 0
    correct: int = 0
    streak: int = 0
    lapses: int = 0
    first_seen: float | None = None
    last_seen: float | None = None
    due: float | None = None

    @property
    def status(self) -> str:
        if self.seen == 0:
            return "new"
        if self.correct >= KNOWN_CORRECT and self.strength >= KNOWN_STRENGTH:
            return "known"
        return "learning"

    def is_due(self, now: float) -> bool:
        return self.due is None or now >= self.due

    def weight(self, now: float) -> float:
        status = self.status
        if status == "new":
            return W_NEW
        if status == "learning":
            return W_LEARNING_MIN + (W_LEARNING_MAX - W_LEARNING_MIN) * (1 - self.strength)
        if self.is_due(now):
            interval = INTERVALS[min(self.streak, len(INTERVALS) - 1)] or INTERVALS[1]
            overdue = (now - (self.due or now)) / interval
            return min(W_DUE_MAX, W_DUE_MIN + overdue)
        return W_RESTING

    def update(self, p: float, now: float) -> None:
        """Record one exposure with performance p in [0, 1]."""
        self.seen += 1
        self.strength += ALPHA * (p - self.strength)
        if self.first_seen is None:
            self.first_seen = now
        if p >= SUCCESS:
            self.correct += 1
            # Only on-schedule reviews advance the interval: seeing "the" ten
            # times in a minute is not ten reviews.
            if self.is_due(now):
                self.streak += 1
                self.due = now + INTERVALS[min(self.streak, len(INTERVALS) - 1)]
        elif p >= PARTIAL:
            self.due = min(self.due or now, now + INTERVALS[1])
        else:
            if self.correct:
                self.lapses += 1
            self.streak = 0
            self.due = now
        self.last_seen = now


def selection_weights(states: list[ItemState], now: float) -> list[float]:
    """Weights for picking target items; caps the share of resting (known, not due) items."""
    weights = [s.weight(now) for s in states]
    resting = [i for i, s in enumerate(states) if s.status == "known" and not s.is_due(now)]
    rest_sum = sum(weights[i] for i in resting)
    active_sum = sum(weights) - rest_sum
    if active_sum > 0 and rest_sum > RESTING_SHARE / (1 - RESTING_SHARE) * active_sum:
        scale = RESTING_SHARE / (1 - RESTING_SHARE) * active_sum / rest_sum
        for i in resting:
            weights[i] *= scale
    return weights


def level_step(level: int) -> int:
    return max(20, round(level * 0.05))


def target_length(level: int) -> float:
    """Preferred sentence length in words: short at first, longer with a bigger vocabulary."""
    return min(11.0, 5.0 + 2.0 * math.log2(max(level, 100) / 100))


def ready_to_level_up(statuses: list[str], recent_accuracy: list[float]) -> bool:
    """Unlock more vocabulary when nearly everything unlocked is known and recent work is accurate."""
    if not statuses or len(recent_accuracy) < 5:
        return False
    known = statuses.count("known")
    unfinished = len(statuses) - known
    return (
        known / len(statuses) >= 0.85
        and unfinished <= max(6, 0.08 * len(statuses))
        and sum(recent_accuracy) / len(recent_accuracy) >= 0.85
    )
