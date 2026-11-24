# -*- coding: utf-8 -*-
"""export_anki_grammar —— 从 Anki 语法卡组导出「どんなときどう使う日本語表現文型」→ data/anki_grammar.json

数据源：Anki collection（**离线只读**：复制 collection.anki2 到临时文件再读，
不依赖 Anki 是否开着，绝不写原库）。

输出与 data/grammar.json 同 schema（build_grammar.py 第 4 源直接合并）：
  id            "ag:<noteId>"
  pattern       Card 字段（句型）
  reading       从例句 <b>…[ふりがな]</b> 标注反推的句型假名（可得时）
  level         标签 日本語表現文型::N1..N5
  formation     ConnectiveType1（接续）
  meaning_en    MeaningEN
  nuance        CNote1（中文讲解）+ AIConclusion（AI 核心语境，剥 HTML）
  examples      Sentence1-11 与 CN1-11 配对（**例句自带中文译文**）
  tags          ["textbook", star, jnote 语感补充...]
  keys          由 build_grammar.extract_keys 统一生成
  sources       ["anki_besshiki"]

教材排版清洗（_clean_ja）：
  「夏[なつ] 休[やす]みの<b>間[あいだ]</b>」→「夏の<b>間</b>」
  —— 剥掉 [ふりがな] 与教材为排版假名插入的空格，保留 <b> 强调。
"""
import json, re, sqlite3, shutil, sys, tempfile, unicodedata, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "anki_grammar.json"
NOTETYPE = "どんなときどう使う日本語表現文型Note"
TAG_LEVEL = re.compile(r"日本語表現文型::(N[1-5])")

def find_collection():
    """定位 collection.anki2：env ANKI_COLLECTION > %APPDATA%/Anki2/*/。"""
    env = __import__("os").environ.get("ANKI_COLLECTION")
    if env and pathlib.Path(env).exists():
        return pathlib.Path(env)
    base = pathlib.Path(os.environ["APPDATA"]) / "Anki2"
    cands = sorted(base.glob("*/collection.anki2"))
    if not cands:
        sys.exit(f"[Error] 未找到 collection.anki2（查过 {base}），可设 ANKI_COLLECTION 指定")
    if len(cands) > 1:
        sys.stderr.write("[Warn] 多个 Anki 配置，取第一个: %s\n" % cands[0])
    return cands[0]

import os

# ---------------- HTML / 教材排版清洗 ----------------
_BR = re.compile(r"<br\s*/?>", re.I)
_BLOCK_END = re.compile(r"</(p|div|li|h[1-6]|ul|ol|tr)>", re.I)
_FURI = re.compile(r"\s*[\[（(]([ぁ-ゖァ-ヺー]+)[\]）)]")  # 汉字后的 [假名] 注音
# 教材为排假名在假名/汉字间插入的空格（两侧至少一侧是假名，汉字间空格不误伤）
_KANA = "[ぁ-ゖァ-ヺ々〇ー]"
_SP = re.compile(rf"(?={_KANA})[ \u3000]+|(?<={_KANA})[ \u3000]+")
# 教材排版残留：「北[ほっ] 海[かい] 道[どう]」剥注音后成「北 海 道」，
# 两侧都是汉字、_SP 够不着，这里补一刀（汉字+空格+汉字 → 直接相连）。
_SP_KK = re.compile(rf"(?<=[\u4e00-\u9fff])[ \u3000]+(?=[\u4e00-\u9fff])")
_TAG = re.compile(r"<[^>]+>")

def _unescape(s):
    return (s.replace("&nbsp;", " ").replace("&amp;", "&")
             .replace("&lt;", "<").replace("&gt;", ">"))

def _ja_core(html):
    """例句/日语讲解通用清洗：块级转空格、剥注音、剥排版空格；保留 <b>。"""
    if not html:
        return ""
    s = _BR.sub(" ", html)
    s = _BLOCK_END.sub(" ", s)
    s = _unescape(s)
    s = _FURI.sub("", s)                      # 汉字[假名] -> 汉字
    s = re.sub(r"<(?!/?b>)[^>]+>", "", s)     # 只留 <b></b>
    s = _SP.sub("", s)
    s = _SP_KK.sub("", s)
    return re.sub(r"[ \u3000]{2,}", " ", s).strip()

def clean_ja(html):
    return _ja_core(html).replace("<b>", "").replace("</b>", "")

def clean_ja_keep_bold(html):
    """例句：保留 <b> 强调（卡片渲染时高亮句型）。"""
    return _ja_core(html)

def clean_text(html):
    """中文/通用字段：全剥 HTML 成纯文本，块级转空格。"""
    if not html:
        return ""
    s = _BR.sub(" ", html)
    s = _BLOCK_END.sub(" ", s)
    s = _TAG.sub("", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", s).strip()

def pattern_reading(card, sentences):
    """从例句 <b>…[ふりがな]</b> 标注反推句型假名：取第一个含 card 汉字的 <b> 段。"""
    kanji = [c for c in card if "\u4e00" <= c <= "\u9fff"]
    if not kanji:
        return None
    for s in sentences:
        for m in re.finditer(r"<b>(.*?)</b>", s or ""):
            seg = m.group(1)
            if all(k in seg for k in kanji):
                rd = "".join(_FURI.findall(seg))
                if rd:
                    return unicodedata.normalize("NFKC", rd)
    return None

# ---------------- 读取 ----------------
def read_notes():
    src = find_collection()
    tmp = pathlib.Path(tempfile.gettempdir()) / "anki_grammar_ro.anki2"
    shutil.copy2(src, tmp)
    con = sqlite3.connect("file:%s?mode=ro" % tmp.as_posix(), uri=True)
    con.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
    mid = con.execute("select id from notetypes where name=?", (NOTETYPE,)).fetchone()[0]
    names = [r[0] for r in con.execute("select name from fields where ntid=? order by ord", (mid,))]
    out = []
    for nid, flds, tags in con.execute("select id, flds, tags from notes where mid=? order by id", (mid,)):
        f = dict(zip(names, flds.split("\x1f")))
        f = {k: (v or "").strip() for k, v in f.items()}
        out.append((nid, f, [t.strip() for t in tags.split() if t.strip()]))
    con.close()
    tmp.unlink(missing_ok=True)
    return out

def build(nid, f, tags):
    card = clean_text(f.get("Card"))
    if not card:
        return None
    level = next((m.group(1) for t in tags if (m := TAG_LEVEL.search(t))), None)
    sents, exs = [], []
    for i in range(1, 12):
        jp = clean_ja_keep_bold(f.get(f"Sentence{i}"))
        if not jp:
            continue
        sents.append(f.get(f"Sentence{i}"))
        cn = clean_text(f.get(f"CN{i}")) or clean_text(f.get(f"CHT{i}"))
        exs.append({"ja": jp, "zh": cn, "en": ""})
    notes_cn = [clean_text(f.get(f"CNote{i}")) for i in range(1, 8)]
    notes_ja = [clean_ja(f.get(f"JNote{i}")) for i in range(1, 8)]
    ai = clean_text(f.get("AIConclusion"))
    nuance_parts = [x for x in notes_cn if x]
    if ai:
        nuance_parts.append("〔核心语境〕" + ai)
    nuance = "\n".join(nuance_parts)
    star = clean_text(f.get("Star"))
    tags_out = ["textbook"] + ([star] if star else [])
    # 日语讲解里的语感关键词（如「婉曲」「丁寧」）作为补充 tag
    for nj in notes_ja:
        for kw in ("婉曲", "丁寧", "カジュアル", "硬い", "柔らかい", "書き言葉", "話し言葉"):
            if kw in nj and kw not in tags_out:
                tags_out.append(kw)
    return {
        "id": f"ag:{nid}",
        "pattern": card,
        "reading": pattern_reading(card, sents),
        "level": level,
        "formation": clean_text(f.get("ConnectiveType1")),
        "meaning_en": clean_text(f.get("MeaningEN")),
        "nuance": nuance,
        "examples": exs,
        "tags": tags_out,
        "keys": [],
        "sources": ["anki_besshiki"],
        "_jnote_ja": [x for x in notes_ja if x],   # 导出层保留，build 合并时可取用
        "_meaning_cn": clean_text(f.get("MeaningCN")),
        "_book_order": clean_text(f.get("BookOrder")),
    }

def main():
    notes = read_notes()
    entries = [e for nid, f, t in notes if (e := build(nid, f, t))]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(entries, ensure_ascii=False, indent=1), "utf-8")
    n_ex = sum(len(e["examples"]) for e in entries)
    n_zh = sum(1 for e in entries for x in e["examples"] if x["zh"])
    print(f"导出 {len(entries)} 条例句卡 -> {OUT.relative_to(ROOT)}")
    print(f"例句 {n_ex} 条（带中文 {n_zh}）| 带中文讲解 {sum(1 for e in entries if e['nuance'])} | 带接续 {sum(1 for e in entries if e['formation'])}")

if __name__ == "__main__":
    main()
