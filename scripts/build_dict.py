# -*- coding: utf-8 -*-
"""构建本地词典库 data/ja_dict.sqlite（P0+P1 多源合并版）

数据源：
  1. jitendex-yomitan-zh  日→中词典（中文释义+词性+例句+标签中文说明）
  2. jmdict-simplified    日→英全量（词性/常用标记/英文释义）
                          + P0：sense 级 misc / field / dialect / info / related / antonym / languageSource
  3. OpenJLPT vocab       JLPT N5-N1 分级 + Tatoeba 例句（带振假名）
  4. Kanjium              P1：音调（pitch accent）
  5. wordfreq             P1：Zipf 词频（构建期预计算，运行时不依赖该包）
  6. KANJIDIC2            P1：汉字层（音训/部首/笔画/学年/JLPT/英义）
  7. 日本語WordNet 2.1    P1：类义/上位/对义 + 日语释义（辨析用）
  8. Tatoeba jpn-cmn      P1：日中句对（真实语料例句）

表结构：
  words(...)         每个 surface+reading 组合一行
  tags(code, category, label)         语域/标签代码 → 中文说明
  kanji(...)                          汉字层
  wn_synset / wn_word / wn_rel         WordNet 义项/词/关系（辨析）
  sentences(id, ja, zh)                Tatoeba 日中句对
"""
import bz2
import gzip
import json
import math
import pathlib
import re
import sqlite3
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT_DB = ROOT / "data" / "ja_dict.sqlite"

# ---------------------------------------------------------------- 标签词典
# EDRDG 标签代码 → 中文说明（标准标签定义的保守译法；CLI 输出时带 (code) 以便回溯）
TAB_LABELS = {
    # ---- misc（语域/用法）----
    "arch": "古语", "obs": "废语", "col": "口语", "fam": "亲昵语",
    "hon": "尊敬语", "hum": "谦让语", "pol": "礼貌语", "derog": "贬义",
    "fem": "女性用语", "euph": "委婉语", "company": "公司名", "product": "商品名",
    "char": "单字",
    "joc": "戏谑", "vulg": "粗俗", "sl": "俚语", "net-sl": "网络用语",
    "m-sl": "漫画用语", "on-mim": "拟声拟态", "proverb": "谚语", "poet": "诗歌语",
    "rare": "罕用", "sens": "需避讳", "uk": "通常写假名", "sk": "通常写汉字",
    "ik": "不规则假名", "ok": "不规则汉字", "ateji": "借字", "gikun": "义训",
    "yoji": "四字熟语", "abbr": "略语", "chn": "幼儿语", "dated": "旧式说法",
    "male": "男性用语", "female": "女性用语", "child": "儿童用语",
    "form": "书面语", "ex": "感叹", "exp": "惯用表达", "id": "惯用句",
    "quote": "引用", "person": "人名", "place": "地名", "work": "作品名",
    "ev": "事件名", "obj": "物名", "group": "组织名", "relig": "宗教",
    "myth": "神话", "leg": "传说", "fict": "虚构", "given": "名", "surname": "姓",
    "organization": "组织", "unclass": "未分类",
    # ---- field（领域）----
    "buddh": "佛教", "comp": "计算机", "ling": "语言学", "math": "数学",
    "mil": "军事", "music": "音乐", "physics": "物理", "chem": "化学",
    "sports": "体育", "sumo": "相扑", "law": "法律", "med": "医学",
    "biol": "生物", "econ": "经济", "geol": "地质", "astron": "天文",
    "baseb": "棒球", "food": "料理", "archit": "建筑", "shogi": "将棋",
    "MA": "武道", "go": "围棋", "mahj": "麻将", "print": "印刷",
    "photo": "摄影", "elec": "电气", "telec": "通信", "civeng": "土木",
    "engr": "工程", "geometry": "几何", "gramm": "语法", "logic": "逻辑",
    "philos": "哲学", "psych": "心理", "Shinto": "神道", "Christn": "基督教",
    "bus": "商务", "fin": "金融", "stock": "股票", "agric": "农业",
    "anat": "解剖", "zool": "动物", "bot": "植物", "min": "矿物",
    "oceanog": "海洋学", "meteor": "气象", "naut": "航海", "aviat": "航空",
    "rail": "铁路", "tradem": "商标", "paleo": "古生物", "video": "影像",
    "politics": "政治", "hist": "历史", "geogr": "地理", "ornith": "鸟类",
    "fish": "鱼类", "insect": "昆虫", "foodst": "食材", "cloth": "服装",
    "hanaf": "花道", "kabuki": "歌舞伎", "noh": "能乐", "thea": "戏剧",
}

# jitendex tag_bank 只有 8 条，逐个映射（category 决定归属列）
TAGBANK_CAT = {
    "popular": "常用", "frequent": "高频", "archaism": "古语",
    "expression": "惯用表达", "partOfSpeech": "词性", "field": "领域",
    "form": "形态", "misc": "语域", "dialect": "方言",
}


# ---------------------------------------------------------------- 1. jitendex-zh
def walk(node, out):
    """递归提取 structured-content：义项(glossary 的 li)、词性 tag、例句。"""
    if isinstance(node, list):
        for n in node:
            walk(n, out)
        return
    if not isinstance(node, dict):
        return
    tag = node.get("tag")
    data = node.get("data") or {}
    content = data.get("content")
    if content == "glossary":
        def gl(n):
            if isinstance(n, list):
                for x in n:
                    gl(x)
            elif isinstance(n, dict):
                if isinstance(n.get("content"), str):
                    out["gloss"].append(n["content"])
                else:
                    gl(n.get("content"))
        gl(node.get("content"))
        return  # glossary 内部不再深入
    if data.get("class") == "tag" and data.get("code"):
        out["tags"].add(data["code"])
    if content == "example-sentence":
        ja, zh = [], []
        def collect(n, lang):
            if isinstance(n, list):
                for x in n:
                    collect(x, lang)
            elif isinstance(n, dict):
                d = n.get("data") or {}
                if d.get("content") == "example-sentence-a":
                    collect(n.get("content"), "ja")
                elif d.get("content") == "example-sentence-b":
                    collect(n.get("content"), "zh")
                elif n.get("tag") == "rt":
                    return  # 跳过振假名注音
                elif isinstance(n.get("content"), str):
                    (ja if lang == "ja" else zh).append(n["content"])
                else:
                    collect(n.get("content"), lang)
        collect(node.get("content"), None)
        if ja:
            out["examples"].append({"ja": "".join(ja), "zh": "".join(zh)})
    walk(node.get("content"), out)


def flatten_sc(node):
    out = {"gloss": [], "tags": set(), "examples": []}
    walk(node, out)
    return out


def read_jitendex():
    zh, tag_rows = {}, []
    zj = zipfile.ZipFile(RAW / "jitendex_zh" / "jitendex-zh.zip")
    for i in range(1, 217):
        name = f"term_bank_{i}.json"
        try:
            bank = json.loads(zj.read(name))
        except KeyError:
            break
        for row in bank:
            surface, reading, freq, defs = row[0], row[1], row[4], row[5]
            gloss_zh, tags, examples = [], set(), []
            for d in defs:
                if isinstance(d, dict) and d.get("type") == "structured-content":
                    f = flatten_sc(d["content"])
                    gloss_zh += f["gloss"]
                    tags |= f["tags"]
                    examples += f["examples"]
                elif isinstance(d, str):
                    gloss_zh.append(d)
            key = (surface, reading)
            e = zh.setdefault(key, {"gloss_zh": [], "tags": set(), "examples": [], "freq": 0})
            e["gloss_zh"] += gloss_zh
            e["tags"] |= tags
            e["examples"] += examples[:2]
            e["freq"] = max(e["freq"], freq or 0)
    # tag_bank：仅 8 条，官方中文说明
    try:
        for code, category, order, label, score in json.loads(zj.read("tag_bank_1.json")):
            tag_rows.append((code, TAGBANK_CAT.get(category, category), label))
    except (KeyError, ValueError):
        pass
    return zh, tag_rows


# ---------------------------------------------------------------- 2. jmdict
def read_jmdict():
    je = zipfile.ZipFile(RAW / "jmdict" / "jmdict-eng.zip")
    name = next(n for n in je.namelist() if n.startswith("jmdict-eng") and n.endswith(".json"))
    jmd = json.loads(je.read(name))
    en = {}
    for w in jmd["words"]:
        kanji = [k["text"] for k in w.get("kanji", [])]
        kana = [k["text"] for k in w.get("kana", [])]
        common = any(k.get("common") for k in w.get("kanji", []) + w.get("kana", []))
        gloss_en, pos = [], []
        misc, field, dialect, info = [], [], [], []
        related, antonym, lang_src = [], [], []
        for s in w.get("sense", []):
            pos += s.get("partOfSpeech", [])
            misc += s.get("misc", []) or []
            field += s.get("field", []) or []
            dialect += s.get("dialect", []) or []
            info += s.get("info", []) or []
            for r in s.get("related", []) or []:
                # jmdict-simplified 的 related 形如 ["一見"] 或 ["見る", 2]（第二项是义项序号）
                text = r[0] if isinstance(r, (list, tuple)) and r else (
                    r.get("text") if isinstance(r, dict) else None)
                if text and text not in [x["word"] for x in related]:
                    related.append({"word": text, "rel": ""})
            for a in s.get("antonym", []) or []:
                text = a[0] if isinstance(a, (list, tuple)) and a else (
                    a.get("text") if isinstance(a, dict) else None)
                if text and text not in antonym:
                    antonym.append(text)
            for ls in s.get("languageSource", []) or []:
                if ls.get("text"):
                    lang_src.append({"lang": ls.get("lang") or "", "text": ls["text"],
                                     "wasei": bool(ls.get("wasei"))})
            for g in s.get("gloss", []):
                if g.get("lang") == "eng":
                    gloss_en.append(g["text"])
        surfaces = kanji or kana
        readings = kana or kanji
        for sf in surfaces:
            for rd in readings:
                e = en.setdefault((sf, rd), {"gloss_en": [], "pos": set(), "common": False,
                                             "misc": [], "field": [], "dialect": [],
                                             "info": [], "related": [], "antonym": [],
                                             "lang_src": []})
                e["gloss_en"] += gloss_en[:4]
                e["pos"] |= set(pos)
                e["common"] = e["common"] or common
                e["misc"] += misc
                e["field"] += field
                e["dialect"] += dialect
                e["info"] += info
                e["related"] += related
                e["antonym"] += antonym
                e["lang_src"] += lang_src
    return en


# ---------------------------------------------------------------- 3. OpenJLPT vocab
def read_openjlpt():
    jlpt = {}
    for lv in ("n5", "n4", "n3", "n2", "n1"):
        for v in json.loads((RAW / f"openjlpt/vocab_{lv}.json").read_text("utf-8")):
            jlpt[(v["word"], v["reading"])] = (lv.upper(), [
                {"ja": e["ja"], "en": e.get("en", ""), "furigana": e.get("furigana", "")}
                for e in v.get("examples", [])])
    return jlpt


# ---------------------------------------------------------------- 4. Kanjium 音调
def read_kanjium():
    """accents.txt: 词形 \\t 假名 \\t 音调位(可能逗号分隔) \\t 标签"""
    p = RAW / "kanjium" / "accents.txt"
    pitch = {}
    if not p.exists():
        return pitch
    for line in p.read_text("utf-8").splitlines():
        cols = line.split("\t")
        if len(cols) < 3 or not cols[0]:
            continue
        word, reading, pose = cols[0], cols[1], cols[2].strip()
        if not pose:
            continue
        for key in ((word, reading), (word, None)):
            pitch.setdefault(key, pose)
    return pitch


# ---------------------------------------------------------------- 5. KANJIDIC2
def read_kanjidic():
    p = RAW / "kanjidic" / "kanjidic2.xml.gz"
    rows = []
    if not p.exists():
        return rows
    with gzip.open(p, "rb") as f:
        for event, elem in ET.iterparse(f, events=("end",)):
            if elem.tag != "character":
                continue
            lit = elem.findtext("literal")
            misc = elem.find("misc") or ET.Element("misc")
            # KANJIDIC2 结构：reading/meaning 都挂在 reading_meaning/rmgroup 下
            rm = elem.find("reading_meaning") or ET.Element("reading_meaning")
            on, kun, nanori, meaning = [], [], [], []
            for r in rm.iter("reading"):
                # 本版 KANJIDIC2 把读音直接写在 <reading> 的文本里，老版是 <r_value>
                v = (r.findtext("r_value") or r.text or "").strip()
                rt = r.get("r_type") or r.findtext("r_type")
                if rt == "ja_on":
                    on.append(v)
                elif rt == "ja_kun":
                    kun.append(v.lstrip("."))
            for n in rm.findall("nanori"):
                if n.text:
                    nanori.append(n.text)
            for m in rm.iter("meaning"):
                if m.get("m_lang") in (None, "en") and m.text:
                    meaning.append(m.text)
            radical = ""
            for rv in elem.findall("radical/rad_value"):
                if rv.get("rad_type") == "classical":
                    radical = rv.text or ""
            rows.append((
                lit,
                int(misc.findtext("grade") or 0) or None,
                int(misc.findtext("stroke_count") or 0) or None,
                int(misc.findtext("jlpt") or 0) or None,
                int(misc.findtext("freq") or 0) or None,
                radical,
                ",".join(on), ",".join(kun), ",".join(nanori),
                "; ".join(meaning[:6]),
            ))
            elem.clear()
    return rows


# ---------------------------------------------------------------- 6. 日本語WordNet
def read_wordnet():
    p = RAW / "wordnet" / "wnja-2.1.xml"
    if not p.exists():
        return [], [], []
    words, synsets, rels = [], [], []
    seen_word = set()
    syn = None
    for event, elem in ET.iterparse(p, events=("start", "end")):
        if event == "start":
            if elem.tag == "Synset":
                syn = {"id": elem.get("id"), "pos": elem.get("partOfSpeech"),
                       "def": "", "ex": [], "rel": []}
            continue
        if elem.tag == "LexicalEntry":
            lemma = elem.find("Lemma")
            if lemma is None:
                continue
            surface = lemma.get("writtenForm") or ""
            pos = lemma.get("partOfSpeech") or ""
            reading = ""
            for form in elem.findall("Form"):
                if form.get("script") == "hira":
                    reading = form.get("writtenForm") or ""
                    break
            for sense in elem.findall("Sense"):
                sid = sense.get("synset")
                if sid and (surface, sid) not in seen_word:
                    seen_word.add((surface, sid))
                    words.append((surface, reading, pos, sid))
            elem.clear()
        elif elem.tag == "Synset":
            if syn is None:
                continue
            for d in elem.findall("Definition"):
                if d.text:
                    syn["def"] = (syn["def"] + ("；" if syn["def"] else "") + d.text)[:400]
            for ex in elem.findall("Example"):
                if ex.text and len(syn["ex"]) < 2:
                    syn["ex"].append(ex.text)
            for r in elem.findall("SynsetRelation"):
                tgt, rt = r.get("target"), r.get("relType")
                if tgt and rt:
                    syn["rel"].append((tgt, rt))
            synsets.append((syn["id"], syn["pos"], syn["def"], json.dumps(syn["ex"], ensure_ascii=False)))
            for tgt, rt in syn["rel"]:
                rels.append((syn["id"], tgt, rt))
            syn = None
            elem.clear()
    return words, synsets, rels


# ---------------------------------------------------------------- 6b. WordNet 1.1 关系补齐
# 2.1 的 SynsetRelation 很稀疏（hypernym 只有 723 条），1.1 的 synset id 是
# 「offset-pos」，与 2.1 的「wnja-offset-pos」一一对应（抽样成员重合率 95%）。
# 只对「1.1 与 2.1 成员有交集」的 synset 合并关系：映射漂移的义项宁可缺失，也不挂错。
WN_LINK_MAP = {
    "hype": "hypernym", "hypo": "hyponym", "inst": "instance_hypernym",
    "hasi": "instance_hyponym", "sim": "similar", "also": "also",
    "attr": "attribute", "ant": "antonym",
}


def _extract_wn11_tsv():
    """把 1.1 sqlite 里需要的两张表导成 TSV（一次，之后复用）。"""
    gz = RAW / "wordnet" / "wnjpn-1.1.db.gz"
    links_tsv = RAW / "wordnet" / "wn1.1-links.tsv"
    words_tsv = RAW / "wordnet" / "wn1.1-jpnwords.tsv"
    if links_tsv.exists() and words_tsv.exists():
        return links_tsv, words_tsv
    if not gz.exists():
        return None, None
    import gzip
    import shutil
    import tempfile
    tmp = pathlib.Path(tempfile.gettempdir()) / "wnjpn-1.1.db"
    with gzip.open(gz, "rb") as src, open(tmp, "wb") as dst:
        shutil.copyfileobj(src, dst)
    con = sqlite3.connect(tmp)
    with open(links_tsv, "w", encoding="utf-8") as f:
        for a, b, l in con.execute("SELECT synset1, synset2, link FROM synlink"):
            f.write(f"{a}\t{b}\t{l}\n")
    with open(words_tsv, "w", encoding="utf-8") as f:
        for sid, w in con.execute(
                "SELECT s.synset, w.lemma FROM sense s JOIN word w ON w.wordid=s.wordid "
                "WHERE w.lang='jpn'"):
            f.write(f"{sid}\t{w}\n")
    con.close()
    tmp.unlink(missing_ok=True)
    return links_tsv, words_tsv


def read_wordnet_extra(members21, existing):
    """members21: synset -> set(词)；existing: 已有的 (src,dst,rel)。"""
    links_tsv, words_tsv = _extract_wn11_tsv()
    if not links_tsv:
        return []
    words11 = {}
    with open(words_tsv, encoding="utf-8") as f:
        for line in f:
            sid, w = line.rstrip("\n").split("\t", 1)
            words11.setdefault(sid, set()).add(w)
    valid = {sid for sid, ws in words11.items()
             if members21.get("wnja-" + sid, set()) & ws}
    extra = []
    with open(links_tsv, encoding="utf-8") as f:
        for line in f:
            a, b, l = line.rstrip("\n").split("\t")
            rel = WN_LINK_MAP.get(l)
            if not rel or a not in valid:
                continue
            src, dst = "wnja-" + a, "wnja-" + b
            if dst not in members21 or (src, dst, rel) in existing:
                continue
            existing.add((src, dst, rel))
            extra.append((src, dst, rel))
    return extra



def read_tatoeba():
    p = RAW / "tatoeba"
    jpn_p, cmn_p, link_p = (p / "jpn_sentences_detailed.tsv.bz2",
                            p / "cmn_sentences.tsv.bz2",
                            p / "jpn-cmn_links.tsv.bz2")
    if not (jpn_p.exists() and cmn_p.exists() and link_p.exists()):
        return []
    jpn, cmn = {}, {}
    with bz2.open(jpn_p, "rt", encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) >= 3:
                jpn[c[0]] = c[2]
    with bz2.open(cmn_p, "rt", encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) >= 3:
                cmn[c[0]] = c[2]
    out, seen = [], set()
    with bz2.open(link_p, "rt", encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) < 2:
                continue
            a, b = c[0].strip(), c[1].strip()
            ja, zh = jpn.get(a), cmn.get(b)
            if not ja or not zh:
                # 两侧语言可能颠倒
                ja, zh = jpn.get(b), cmn.get(a)
            if not ja or not zh or (ja, zh) in seen:
                continue
            seen.add((ja, zh))
            out.append((f"tatoeba-{a}-{b}", ja, zh))
    return out


# ---------------------------------------------------------------- 8. wordfreq
def compute_zipf(keys):
    """构建期预计算 Zipf 频度（运行时不依赖 wordfreq）。

    用 get_frequency_dict('ja') 拿预训练词频表，而不是 zipf_frequency()：
    后者对日语输入要调 MeCab，本机没装；前者是现成表，纯 dict 查询。
    wordfreq 的 ja 表值是**占比**（全表加起来 ≈ 1），不是每百万频度；
    Zipf 定义是「每十亿词出现 10**x 次」→ zipf = log10(p) + 9
    """
    try:
        from wordfreq import get_frequency_dict
        freq = get_frequency_dict("ja")
    except Exception as exc:  # 依赖装不上不影响主流程
        print(f"[warn] wordfreq 不可用，跳过 zipf 列: {exc}")
        return {}
    print(f"wordfreq ja table: {len(freq)} entries")
    out = {}
    for sf, rd in keys:
        v = freq.get(sf) or (freq.get(rd) if rd and rd != sf else 0)
        if v and v > 0:
            out[(sf, rd)] = round(math.log10(v) + 9, 2)
    return out


# ---------------------------------------------------------------- 主流程
def main():
    zh, tag_rows = read_jitendex()
    print(f"jitendex-zh entries: {len(zh)}")
    en = read_jmdict()
    print(f"jmdict keys: {len(en)}")
    jlpt = read_openjlpt()
    print(f"openjlpt vocab keys: {len(jlpt)}")
    pitch = read_kanjium()
    print(f"kanjium pitch keys: {len(pitch)}")

    keys = set(zh) | set(en)
    keep = {k for k in keys if (k in zh and zh[k]["gloss_zh"]) or (k in en and en[k]["common"]) or k in jlpt}
    print(f"total keys: {len(keys)} -> keep: {len(keep)}")
    zipf = compute_zipf(sorted(keep))

    kanji_rows = read_kanjidic()
    # 常用汉字的中文义（data/zh_kanji.json，本地模型译自英文义，构建期合并）
    ZH_KANJI = ROOT / "data" / "zh_kanji.json"
    zh_kanji = {}
    if ZH_KANJI.exists():
        zh_kanji = {k: v["zh"] for k, v in json.loads(ZH_KANJI.read_text("utf-8")).items()
                    if isinstance(v, dict) and v.get("zh")}
    kanji_rows = [tuple(row) + (zh_kanji.get(row[0]),) for row in kanji_rows]
    print(f"kanjidic entries: {len(kanji_rows)}（中文义 {sum(1 for r in kanji_rows if r[-1])} 字）")
    wn_words, wn_synsets, wn_rels = read_wordnet()
    print(f"wordnet 2.1: {len(wn_words)} word-senses / {len(wn_synsets)} synsets / {len(wn_rels)} rels")
    members21 = {}
    for surface, reading, pos, sid in wn_words:
        members21.setdefault(sid, set()).add(surface)
    extra = read_wordnet_extra(members21, set(wn_rels))
    wn_rels += extra
    print(f"wordnet 1.1 补关系: +{len(extra)} → {len(wn_rels)} rels")
    sentences = read_tatoeba()
    print(f"tatoeba jpn-cmn pairs: {len(sentences)}")

    OUT_DB.unlink(missing_ok=True)
    con = sqlite3.connect(OUT_DB)
    con.executescript("""
    CREATE TABLE words(
      id INTEGER PRIMARY KEY, surface TEXT, reading TEXT,
      common INTEGER, jlpt TEXT, pos TEXT, freq INTEGER,
      gloss_zh TEXT, gloss_en TEXT, examples TEXT,
      pitch TEXT, zipf REAL, misc TEXT, field TEXT, dialect TEXT,
      info TEXT, related TEXT, antonym TEXT, lang_src TEXT
    );
    CREATE INDEX idx_surface ON words(surface);
    CREATE INDEX idx_reading ON words(reading);
    CREATE VIRTUAL TABLE words_fts USING fts5(surface, reading, gloss_zh, gloss_en);

    CREATE TABLE tags(code TEXT PRIMARY KEY, category TEXT, label TEXT);

    CREATE TABLE kanji(literal TEXT PRIMARY KEY, grade INTEGER, stroke INTEGER, jlpt INTEGER,
      freq INTEGER, radical TEXT, on_read TEXT, kun_read TEXT, nanori TEXT, meaning_en TEXT,
      meaning_zh TEXT);

    CREATE TABLE wn_synset(id TEXT PRIMARY KEY, pos TEXT, definition TEXT, examples TEXT);
    CREATE TABLE wn_word(surface TEXT, reading TEXT, pos TEXT, synset TEXT);
    CREATE TABLE wn_rel(src TEXT, dst TEXT, rel TEXT);
    CREATE INDEX idx_wn_word_surface ON wn_word(surface);
    CREATE INDEX idx_wn_word_synset ON wn_word(synset);
    CREATE INDEX idx_wn_rel_src ON wn_rel(src);

    CREATE TABLE sentences(id TEXT PRIMARY KEY, ja TEXT, zh TEXT);
    CREATE VIRTUAL TABLE sentences_fts USING fts5(ja, zh, content='sentences', content_rowid='rowid');
    """)

    n = 0
    for k in keep:
        sf, rd = k
        z = zh.get(k, {})
        e = en.get(k, {})
        jl = jlpt.get(k)
        gloss_zh = "；".join(dict.fromkeys(z.get("gloss_zh", [])))[:600]
        gloss_en = "; ".join(dict.fromkeys(e.get("gloss_en", [])))[:400]
        pos = sorted(set(e.get("pos", set())) | (z.get("tags", set()) & {"exp", "ctr", "ateji", "id", "myn", "gikun"}))
        ex = (jl[1] if jl else []) + z.get("examples", [])[:2]
        con.execute(
            "INSERT INTO words(surface,reading,common,jlpt,pos,freq,gloss_zh,gloss_en,examples,"
            "pitch,zipf,misc,field,dialect,info,related,antonym,lang_src) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sf, rd, int(e.get("common") or bool(jl)), jl[0] if jl else None,
             ",".join(pos), z.get("freq", 0), gloss_zh, gloss_en,
             json.dumps(ex[:3], ensure_ascii=False),
             pitch.get((sf, rd)) or pitch.get((sf, None)),
             zipf.get(k),
             ",".join(dict.fromkeys(e.get("misc", []))),
             ",".join(dict.fromkeys(e.get("field", []))),
             ",".join(dict.fromkeys(e.get("dialect", []))),
             " / ".join(dict.fromkeys(e.get("info", [])))[:300],
             json.dumps(e.get("related", [])[:6], ensure_ascii=False),
             json.dumps(e.get("antonym", [])[:6], ensure_ascii=False),
             json.dumps(e.get("lang_src", [])[:3], ensure_ascii=False)))
        n += 1
    con.execute("INSERT INTO words_fts SELECT surface, reading, gloss_zh, gloss_en FROM words")

    # 标签表：jitendex 官方中文说明 + 标签词典兜底
    tag_map = {code: (cat, label) for code, cat, label in tag_rows}
    for code, label in TAB_LABELS.items():
        tag_map.setdefault(code, ("语域", label))
    con.executemany("INSERT OR REPLACE INTO tags VALUES(?,?,?)",
                    [(c, v[0], v[1]) for c, v in tag_map.items()])

    con.executemany("INSERT OR REPLACE INTO kanji VALUES(?,?,?,?,?,?,?,?,?,?,?)", kanji_rows)
    con.executemany("INSERT OR REPLACE INTO wn_synset VALUES(?,?,?,?)", wn_synsets)
    con.executemany("INSERT INTO wn_word VALUES(?,?,?,?)", wn_words)
    con.executemany("INSERT INTO wn_rel VALUES(?,?,?)", wn_rels)
    con.executemany("INSERT INTO sentences VALUES(?,?,?)", sentences)
    con.execute("INSERT INTO sentences_fts(sentences_fts) VALUES('rebuild')")
    con.commit()
    con.close()
    print(f"words rows: {n}, db: {OUT_DB.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
