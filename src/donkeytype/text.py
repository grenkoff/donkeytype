"""Text normalization shared by the corpus build, grading and the web client."""

import re

from wordfreq import word_frequency

# A word is a run of letters/digits with optional internal apostrophes:
# "don't", "Tom's" and "rock'n'roll" are single words, "well-known" is two.
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)*")


def clean(text: str) -> str:
    return (
        text.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("—", " - ")
        .strip()
    )


def word_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, lowercased word) for every word of an already cleaned text."""
    return [(m.start(), m.end(), m.group().lower()) for m in WORD_RE.finditer(text)]


def words(text: str) -> list[str]:
    return [w for _, _, w in word_spans(clean(text))]


# British spellings that rules can't catch safely. Rules below handle
# -ise/-isation/-yse and -tre when the American variant is more frequent.
BRITISH = set(
    """
    colour colours coloured colouring colourful favour favours favoured favourite favourites
    favourable honour honours honoured honourable labour labours laboured flavour flavours
    flavoured humour humoured neighbour neighbours neighbourhood neighbouring
    behaviour behaviours harbour harbours rumour rumours vapour armour odour endeavour
    endeavours parlour savour saviour splendour vigour rigour clamour glamour tumour
    travelled travelling traveller travellers cancelled cancelling labelled labelling
    modelled modelling fuelled fuelling signalled signalling quarrelled quarrelling
    marvelled marvellous jewellery jeweller counsellor levelled levelling totalled
    dialled dialling equalled panelled channelled tunnelled grovelled snorkelling
    programme programmes grey greyer greyish mum mums tyre tyres pyjamas cheque cheques
    aluminium kerb plough ploughed sceptical sceptic defence offence licence pretence
    catalogue analogue enrol enrolment fulfil fulfilment instalment skilful wilful
    mould mouldy moult smoulder storey storeys draught draughts manoeuvre aeroplane
    aeroplanes arse whilst amongst learnt spelt dreamt leapt spoilt dwelt
    lorry lorries flat flats holidaymaker fortnight petrol trousers biscuit biscuits
    queue queues queued queueing rubbish dustbin postcode motorway pavement chemist
    maths carpark pram nappy nappies
    gaol practise practised practising licenced cosy cosier moustache
    """.split()
)
# Words above that are legit American English too when used differently.
BRITISH -= {"flat", "flats", "chemist", "queue", "queues", "queued", "practise"}


def _american_variant(word: str) -> str | None:
    for suffix, repl in (
        ("isation", "ization"),
        ("isations", "izations"),
        ("ise", "ize"),
        ("ised", "ized"),
        ("ises", "izes"),
        ("ising", "izing"),
        ("yse", "yze"),
        ("ysed", "yzed"),
        ("tre", "ter"),
        ("tres", "ters"),
    ):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[: -len(suffix)] + repl
    return None


_cache: dict[str, bool] = {}


def is_british(word: str) -> bool:
    word = word.lower()
    if word in BRITISH:
        return True
    if word not in _cache:
        variant = _american_variant(word)
        _cache[word] = bool(
            variant
            and word_frequency(variant, "en") > word_frequency(word, "en")
        )
    return _cache[word]
