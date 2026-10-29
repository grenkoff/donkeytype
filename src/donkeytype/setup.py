"""Download everything the trainer needs: Kokoro voice model and the prebuilt corpus.

Usage: uv run donkeytype-setup [--build]
  --build  build the corpus from sources instead of downloading it (~30-60 min)
"""

import argparse
import gzip
import shutil
import sys
from pathlib import Path

import httpx

from . import config

KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
CORPUS_URL = "https://github.com/grenkoff/donkeytype/releases/download/data-v1/corpus.db.gz"


def download(url: str, dest: Path) -> None:
    """Stream a URL to a file with a progress line; gunzips when the URL ends in .gz."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with open(tmp, "wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {dest.name}: {done >> 20} / {total >> 20} MB", end="", flush=True)
    print()
    if url.endswith(".gz"):
        unpacked = dest.with_name(dest.name + ".unpacked")
        with gzip.open(tmp, "rb") as src, open(unpacked, "wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
        tmp.unlink()
        tmp = unpacked
    tmp.replace(dest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true", help="build the corpus locally instead of downloading it")
    args = parser.parse_args()

    for path in (config.KOKORO_MODEL, config.KOKORO_VOICES):
        if path.exists():
            print(f"ok: {path.name}")
        else:
            print(f"downloading {path.name}")
            download(f"{KOKORO_BASE}/{path.name}", path)

    if config.CORPUS_DB.exists():
        print(f"ok: {config.CORPUS_DB.name}")
    elif args.build:
        from .build.corpus import main as build

        sys.argv = sys.argv[:1]
        build()
    else:
        print("downloading prebuilt corpus")
        download(CORPUS_URL, config.CORPUS_DB)

    print("\nall set: run `uv run donkeytype` and open http://127.0.0.1:8765")


if __name__ == "__main__":
    main()
