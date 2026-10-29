from donkeytype.text import is_british, words


def test_words():
    assert words("“Don’t go,” said Tom's well-known dog.") == ["don't", "go", "said", "tom's", "well", "known", "dog"]
    assert words("'Hello', she said.") == ["hello", "she", "said"]


def test_british():
    for w in ["colour", "realise", "centre", "travelled", "organisation"]:
        assert is_british(w), w
    for w in ["color", "realize", "our", "four", "advise", "surprise", "exercise", "tour"]:
        assert not is_british(w), w
