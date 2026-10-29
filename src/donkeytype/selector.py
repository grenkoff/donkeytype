"""Choose the next sentence: target a weighted item, then the best sentence for it."""

import random
import time

from .learner import ItemState, selection_weights, target_length
from .store import Store

CANDIDATES = 150
TARGET_TRIES = 6


def pick_sentence(store: Store, level: int, exclude: set[int]) -> dict | None:
    items = store.unlocked(level)
    if not items:
        return None
    now = time.time()
    states = store.states([i["key"] for i in items])
    weights = selection_weights([states[i["key"]] for i in items], now)
    weight_by_id = {i["id"]: w for i, w in zip(items, weights)}
    preferred = target_length(level)

    for _ in range(TARGET_TRIES):
        target = random.choices(items, weights)[0]
        ids = store.sentence_ids_with(target["id"], level, random.getrandbits(31), CANDIDATES)
        candidates = store.sentences([i for i in ids if i not in exclude])
        if not candidates:
            continue

        def score(s: dict) -> float:
            unique = {item_id for item_id, _, _ in s["items"]}
            total = sum(weight_by_id.get(i, 0.0) for i in unique)
            length_fit = 1 / (1 + 0.15 * abs(s["n_words"] - preferred))
            return total / s["n_words"] ** 0.5 * length_fit * random.uniform(0.8, 1.2)

        best = max(candidates, key=score)
        best["target"] = target["key"]
        return best
    return None


def describe_items(store: Store, sentence: dict, states: dict[str, ItemState]) -> list[dict]:
    """Items of a sentence with their status, for the result screen."""
    seen = set()
    out = []
    for item_id, ws, we in sentence["items"]:
        item = store.items.get(item_id)
        if not item or item_id in seen:
            continue
        seen.add(item_id)
        state = states.get(item["key"])
        out.append({"id": item_id, "key": item["key"], "label": item["label"], "kind": item["kind"],
                    "words": [ws, we], "status": state.status if state else "new"})
    return out
