"""Fetch titles of English multiword entries from Wiktionary categories."""

import json
import time
from pathlib import Path

import httpx

API = "https://en.wiktionary.org/w/api.php"
CATEGORIES = {
    "multiword": "Category:English multiword terms",
    "idiom": "Category:English idioms",
    "phrasal_verb": "Category:English phrasal verbs",
    "phrase": "Category:English phrases",
    "prep_phrase": "Category:English prepositional phrases",
}
HEADERS = {"User-Agent": "donkeytype/0.1 (https://localhost/donkeytype; dictation typing trainer)"}


def fetch_category(client: httpx.Client, category: str) -> list[str]:
    titles: list[str] = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category,
        "cmlimit": "500",
        "cmnamespace": "0",
        "format": "json",
    }
    while True:
        for attempt in range(5):
            try:
                resp = client.get(API, params=params, timeout=30)
                resp.raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"failed to fetch {category}")
        data = resp.json()
        titles += [m["title"] for m in data["query"]["categorymembers"]]
        if "continue" not in data:
            return titles
        params.update(data["continue"])
        time.sleep(0.2)


def fetch_all(out: Path) -> None:
    result: dict[str, list[str]] = {}
    with httpx.Client(headers=HEADERS) as client:
        for kind, category in CATEGORIES.items():
            result[kind] = fetch_category(client, category)
            print(f"{kind}: {len(result[kind])}")
    out.write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    import sys

    fetch_all(Path(sys.argv[1]))
