from donkeytype.analysis import Lexicon, describe, nlp, tokenize


def lexicon():
    words = "i do not know what you be talk about give up smoke smoking last year a".split()
    return Lexicon(
        lemmas={w: (i + 1, i + 1) for i, w in enumerate(words)},
        propn={"tom": 0},
        lemma_freqs=[1.0 / (i + 1) for i in range(len(words))],
        phrases={("give", "up"): 100, ("gave", "up"): 100},
    )


def test_tokens_map_to_typed_words():
    tokens = tokenize(nlp()("I don't know what you're talking about."))
    assert [(t[0], t[2]) for t in tokens if t[0] >= 0][:4] == [(0, "i"), (1, "do"), (1, "not"), (2, "know")]


def test_describe_items_phrases_and_free_names():
    lex = lexicon()
    max_rank, items = describe(tokenize(nlp()("Tom gave up smoking last year.")), lex)
    ids = {i for i, _, _ in items}
    assert 100 in ids  # "gave up" matched as the phrase "give up"
    assert (100, 1, 2) in items  # spans typed words 1..2
    assert max_rank == lex.lemmas["year"][1]


def test_unknown_word_blocks_sentence():
    max_rank, items = describe(tokenize(nlp()("I know a hippopotamus.")), lexicon())
    assert items == [] and max_rank == lexicon().unknown_rank
