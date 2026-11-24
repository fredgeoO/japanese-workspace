# -*- coding: utf-8 -*-
"""下载 jmdict-simplified 精简词典（带中文释义）到 data/raw/jmdict/。"""
import urllib.request, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEST = ROOT / "data" / "raw" / "jmdict"
DEST.mkdir(parents=True, exist_ok=True)

URL = ("https://github.com/scriptin/jmdict-simplified/releases/download/"
       "3.6.2%2B20261123231529/jmdict-eng-3.6.2%2B20261123231529.json.zip")
OUT = DEST / "jmdict-eng.zip"

if OUT.exists() and OUT.stat().st_size > 1_000_000:
    print(f"SKIP {OUT} ({OUT.stat().st_size:,} bytes)")
    sys.exit(0)

req = urllib.request.Request(URL, headers={"User-Agent": "japanese-workspace data fetch"})
print("downloading", URL)
with urllib.request.urlopen(req, timeout=600) as r, open(OUT, "wb") as f:
    total = 0
    while True:
        chunk = r.read(1 << 20)
        if not chunk:
            break
        f.write(chunk)
        total += len(chunk)
        if total % (10 << 20) < (1 << 20):
            print(f"  {total/1e6:.0f} MB")
print(f"OK {OUT} ({OUT.stat().st_size:,} bytes)")
