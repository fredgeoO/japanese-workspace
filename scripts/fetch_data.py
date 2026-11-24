# -*- coding: utf-8 -*-
"""下载日语学习数据源到 data/raw/。幂等：已存在且非空的文件跳过。"""
import sys, urllib.request, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

BASE = "https://raw.githubusercontent.com"

FILES = {
    # OpenJLPT: 526 个 JLPT 语法点（含接续/例句/笔记）
    "openjlpt/grammar_n5.json": f"{BASE}/evanclan/OpenJLPT/main/data/json/grammar/n5.json",
    "openjlpt/grammar_n4.json": f"{BASE}/evanclan/OpenJLPT/main/data/json/grammar/n4.json",
    "openjlpt/grammar_n3.json": f"{BASE}/evanclan/OpenJLPT/main/data/json/grammar/n3.json",
    "openjlpt/grammar_n2.json": f"{BASE}/evanclan/OpenJLPT/main/data/json/grammar/n2.json",
    "openjlpt/grammar_n1.json": f"{BASE}/evanclan/OpenJLPT/main/data/json/grammar/n1.json",
    # OpenJLPT: 7811 个 JLPT 词汇（带例句+振假名）
    "openjlpt/vocab_n5.json": f"{BASE}/evanclan/OpenJLPT/main/data/json/vocab/n5.json",
    "openjlpt/vocab_n4.json": f"{BASE}/evanclan/OpenJLPT/main/data/json/vocab/n4.json",
    "openjlpt/vocab_n3.json": f"{BASE}/evanclan/OpenJLPT/main/data/json/vocab/n3.json",
    "openjlpt/vocab_n2.json": f"{BASE}/evanclan/OpenJLPT/main/data/json/vocab/n2.json",
    "openjlpt/vocab_n1.json": f"{BASE}/evanclan/OpenJLPT/main/data/json/vocab/n1.json",
    # jkindrix: 595 个手工整理语法点 + 13220 条 JMdict 惯用表达 + 活用表
    "jkindrix/grammar.json": f"{BASE}/jkindrix/japanese-language-data/main/data/grammar/grammar.json",
    "jkindrix/expressions.json": f"{BASE}/jkindrix/japanese-language-data/main/data/grammar/expressions.json",
    "jkindrix/conjugations.json": f"{BASE}/jkindrix/japanese-language-data/main/data/grammar/conjugations.json",
}

def fetch(url, dest):
    if dest.exists() and dest.stat().st_size > 100:
        print(f"SKIP  {dest.relative_to(RAW.parent)}  ({dest.stat().st_size:,} bytes)")
        return
    req = urllib.request.Request(url, headers={"User-Agent": "japanese-workspace data fetch"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    dest.write_bytes(data)
    print(f"OK    {dest.relative_to(RAW.parent)}  ({len(data):,} bytes)")

fail = 0
for rel, url in FILES.items():
    dest = RAW / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        fetch(url, dest)
    except Exception as e:
        fail += 1
        print(f"FAIL  {rel}: {e}", file=sys.stderr)
print(f"\ndone, {len(FILES)-fail}/{len(FILES)} ok")
sys.exit(1 if fail else 0)
