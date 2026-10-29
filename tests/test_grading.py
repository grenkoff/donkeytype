from donkeytype.grading import accuracy, grade


def statuses(ref, typed, corrected=None):
    return [(r.ref, r.typed, r.status) for r in grade(ref, typed, corrected)]


def test_exact_match():
    ref = ["i", "don't", "know"]
    assert [s for *_, s in statuses(ref, "I don't know")] == ["correct"] * 3
    assert accuracy(grade(ref, "i don't know")) == 1.0


def test_typo_vs_wrong():
    ref = ["she", "was", "beautiful"]
    result = statuses(ref, "she wsa beautifull")
    assert result[1][2] == "typo"  # adjacent letters swapped
    assert result[2][2] == "typo"


def test_missing_word_keeps_alignment():
    ref = ["i", "want", "to", "go", "home"]
    result = statuses(ref, "i want go home")
    assert [s for *_, s in result] == ["correct", "correct", "missing", "correct", "correct"]


def test_extra_word():
    result = statuses(["thank", "you"], "thank you very")
    assert [s for *_, s in result] == ["correct", "correct", "extra"]


def test_corrected_flag_and_hyphen():
    result = statuses(["well", "known", "fact"], "well-known fact", [False, True, False])
    assert [s for *_, s in result] == ["correct", "corrected", "correct"]


def test_completely_wrong():
    result = statuses(["cat"], "dog")
    assert result == [("cat", "dog", "wrong")]
    assert accuracy(grade(["cat"], "")) == 0.0
