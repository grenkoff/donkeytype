from donkeytype.learner import INTERVALS, ItemState, ready_to_level_up, selection_weights


def test_becomes_known_after_three_good_answers():
    s = ItemState("w:house")
    assert s.status == "new"
    for t in range(3):
        s.update(1.0, now=t * 10_000)
    assert s.status == "known"


def test_streak_only_grows_when_due():
    s = ItemState("w:the")
    s.update(1.0, now=0)
    s.update(1.0, now=1)  # seen again right away: not a review
    assert s.streak == 1
    s.update(1.0, now=INTERVALS[1] + 1)
    assert s.streak == 2


def test_failure_resets_and_counts_lapse():
    s = ItemState("w:through")
    for t in range(3):
        s.update(1.0, now=t * 10_000)
    s.update(0.0, now=50_000)
    assert s.streak == 0 and s.lapses == 1 and s.due == 50_000
    assert s.status == "learning"


def test_resting_items_share_is_capped_but_never_zero():
    known = [ItemState(f"w:{i}", strength=1.0, seen=5, correct=5, streak=3, due=10**9) for i in range(1000)]
    weights = selection_weights(known + [ItemState("w:new")], now=0)
    assert sum(weights[:-1]) / sum(weights) <= 0.2 + 1e-9
    assert all(w > 0 for w in weights)


def test_level_up_needs_known_majority_and_accuracy():
    assert not ready_to_level_up(["known"] * 90 + ["new"] * 10, [1.0] * 10)
    assert ready_to_level_up(["known"] * 95 + ["learning"] * 5, [0.9] * 10)
    assert not ready_to_level_up(["known"] * 100, [0.6] * 10)
    assert not ready_to_level_up(["known"] * 100, [1.0] * 3)
