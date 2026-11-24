# -*- coding: utf-8 -*-
"""下载 P1 阶段外部资料（幂等，已存在则跳过）

  1. KANJIDIC2           汉字信息（音训/部首/笔画/学年/JLPT）
  2. Kanjium             音调（pitch accent）
  3. Tatoeba 日中句对     真实语料例句
  4. 日本語WordNet 2.1    类义/上位/对义 + 日语释义（解压出 LMF XML）

注意：本机 PowerShell 的 schannel TLS 不可用，所以全部走 Python urllib。
"""
import pathlib
import tarfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

ITEMS = [
    ("kanjidic/kanjidic2.xml.gz", "https://www.edrdg.org/kanjidic/kanjidic2.xml.gz"),
    ("kanjium/accents.txt",
     "https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt"),
    ("tatoeba/jpn_sentences_detailed.tsv.bz2",
     "https://downloads.tatoeba.org/exports/per_language/jpn/jpn_sentences_detailed.tsv.bz2"),
    ("tatoeba/cmn_sentences.tsv.bz2",
     "https://downloads.tatoeba.org/exports/per_language/cmn/cmn_sentences.tsv.bz2"),
    ("tatoeba/jpn-cmn_links.tsv.bz2",
     "https://downloads.tatoeba.org/exports/per_language/jpn/jpn-cmn_links.tsv.bz2"),
    ("wordnet/wnja-2.1.tar.xz",
     "https://github.com/bond-lab/wnja/releases/download/v2.1/wnja-2.1.tar.xz"),
    # 1.1 的 sqlite 只用来补齐 2.1 稀疏的语义关系（hypernym 等），映射信任度已在构建期抽样验证
    ("wordnet/wnjpn-1.1.db.gz",
     "https://github.com/bond-lab/wnja/releases/download/v1.1/wnjpn.db.gz"),
]

def fetch(rel, url):
    p = RAW / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and p.stat().st_size > 0:
        print(f"[skip] {rel} ({p.stat().st_size:,} bytes)")
        return p
    tmp = p.with_suffix(p.suffix + ".part")
    print(f"[get ] {rel} <- {url}")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(p)
    print(f"[ok  ] {rel} ({p.stat().st_size:,} bytes)")
    return p

for rel, url in ITEMS:
    fetch(rel, url)

# WordNet：解压出 LMF XML 与 LICENSE
wn_dir = RAW / "wordnet"
xml = wn_dir / "wnja-2.1.xml"
if not xml.exists():
    with tarfile.open(wn_dir / "wnja-2.1.tar.xz", "r:xz") as t:
        for name in ("wnja/wnja-2.1.xml", "wnja/LICENSE"):
            member = t.extractfile(name)
            if member is None:
                continue
            out = wn_dir / pathlib.Path(name).name
            out.write_bytes(member.read())
            print(f"[ok  ] {out.name} ({out.stat().st_size:,} bytes)")
print("done")
