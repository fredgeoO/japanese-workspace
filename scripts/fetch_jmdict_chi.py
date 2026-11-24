# -*- coding: utf-8 -*-
"""下载 jmdict-simplified 中文词典（jmdict-chi）到 data/raw/jmdict/。"""
import urllib.request, pathlib, sys, json

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEST = ROOT / "data" / "raw" / "jmdict"
DEST.mkdir(parents=True, exist_ok=True)

api = urllib.request.Request(
    "https://api.github.com/repos/scriptin/jmdict-simplified/releases/latest",
    headers={"User-Agent": "japanese-workspace data fetch"})
rel = json.load(urllib.request.urlopen(api, timeout=60))
url = next(a["browser_download_url"] for a in rel["assets"]
           if a["name"].startswith("jmdict-chi") and a["name"].endswith(".json.zip"))
OUT = DEST / "jmdict-chi.zip"
print("asset:", url)

if OUT.exists() and OUT.stat().st_size > 100_000:
    print(f"SKIP {OUT} ({OUT.stat().st_size:,} bytes)")
    sys.exit(0)

req = urllib.request.Request(url, headers={"User-Agent": "japanese-workspace data fetch"})
with urllib.request.urlopen(req, timeout=600) as r, open(OUT, "wb") as f:
    total = 0
    while True:
        chunk = r.read(1 << 20)
        if not chunk:
            break
        f.write(chunk)
        total += len(chunk)
print(f"OK {OUT} ({OUT.stat().st_size:,} bytes)")
