# -*- coding: utf-8 -*-
"""清洗合并语法数据 → data/grammar.json + data/ja_grammar.sqlite

合并四个语法源：
  1. OpenJLPT  data/raw/openjlpt/grammar_n*.json   526 条，JLPT N5-N1，含接续/例句
  2. jkindrix  data/raw/jkindrix/grammar.json      595 条，含详细语感说明 meaning_detailed
  3. jkindrix  data/raw/jkindrix/expressions.json   13220 条 JMdict exp 惯用表达（兜底词典层）
  4. Anki 语法卡组（どんなときどう使う日本語表現文型，教材级）
     data/anki_grammar.json  632 条，由 scripts/export_anki_grammar.py 离线只读导出；
     自带中文讲解（CNote1）+ 中文例句译文（CN1-11），例句带 <b> 高亮

输出扁平结构（对本地 LLM / RAG 友好）：
{
  "id": "te-mo-ii",
  "pattern": "〜てもいい",
  "reading": "〜てもいい",          # 全假名形态（若可得）
  "level": "N5",
  "formation": "Verb-te + もいい",
  "meaning_en": "may; it's okay to",
  "nuance": "...",                  # 语感/使用限制（meaning_detailed / notes）
  "examples": [{"ja": "...", "en": "..."}],
  "tags": [...],
  "keys": ["てもいい"],             # 检索特征尾串（去〜、去空白）
  "sources": ["openjlpt"]
}
"""
import json, re, sqlite3, pathlib, unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT_JSON = ROOT / "data" / "grammar.json"
OUT_DB = ROOT / "data" / "ja_grammar.sqlite"

LEVEL_ORDER = {"N5": 5, "N4": 4, "N3": 3, "N2": 2, "N1": 1}

def norm(s):
    """NFKC + 去空白，用于匹配键。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))

def strip_tilde(s):
    return norm(s).lstrip("〜~～").strip()

def kana_form(pattern, reading=None):
    """生成全假名检索形态：〜にたいして → にたいして"""
    out = []
    for ch in norm(pattern):
        if ch in "〜~～+*()（）/;；,，":
            out.append(" ")
        elif "぀" <= ch <= "ゟ" or "゠" <= ch <= "ヿ":
            out.append(ch)
        else:
            out.append(" ")
    return "".join("".join(out).split())

def extract_keys(entry):
    """从 pattern 提取检索特征尾串：去〜、按 + / ; 分段，取含假名的尾段。"""
    keys = set()
    p = norm(entry["pattern"])
    for seg in re.split(r"[+;；/]", p):
        seg = strip_tilde(seg)
        if not seg:
            continue
        # 尾段：从最后一个汉字块之后开始（接续前缀去掉，留句型本体）
        m = re.findall(r"[\u3041-\u30ff\u4e00-\u9fff][\u3041-\u30ff\u4e00-\u9fff々ー]*", seg)
        for tok in m:
            if len(tok) >= 2:
                keys.add(tok)
        if re.search(r"[\u3041-\u30ff]", seg) and len(seg) <= 12:
            keys.add(seg)
    # 全假名形态兜底
    kf = kana_form(entry["pattern"])
    if kf and len(kf) >= 2:
        keys.add(kf)
    return sorted(keys)

entries = {}

def add(entry):
    pid = entry["id"]
    if pid in entries:
        old = entries[pid]
        old["sources"] = sorted(set(old["sources"] + entry["sources"]))
        for f in ("nuance", "meaning_en", "formation", "reading"):
            if not old.get(f) and entry.get(f):
                old[f] = entry[f]
        have = {norm(e["ja"]) for e in old["examples"]}
        old["examples"] += [e for e in entry["examples"] if norm(e["ja"]) not in have]
        old["keys"] = sorted(set(old["keys"]) | set(entry["keys"]))
    else:
        entries[pid] = entry

# ---------- 1. OpenJLPT ----------
for lv in ("n5", "n4", "n3", "n2", "n1"):
    data = json.loads((RAW / f"openjlpt/grammar_{lv}.json").read_text("utf-8"))
    for g in data:
        add({
            "id": f"oj:{g['id']}",
            "pattern": g["pattern"],
            "reading": g.get("reading"),
            "level": g["level"],
            "formation": g.get("formation", ""),
            "meaning_en": g.get("meaning", ""),
            "nuance": g.get("notes", ""),
            "examples": [{"ja": e["ja"], "en": e.get("en", "")} for e in g.get("examples", [])],
            "tags": g.get("tags", []),
            "keys": [],
            "sources": ["openjlpt"],
        })

# ---------- 2. jkindrix grammar ----------
g2 = json.loads((RAW / "jkindrix/grammar.json").read_text("utf-8"))["grammar_points"]
for g in g2:
    ex = []
    for e in g.get("examples", [])[:3]:
        if isinstance(e, dict):
            ex.append({"ja": e.get("ja") or e.get("japanese") or e.get("sentence", ""),
                       "en": e.get("en") or e.get("english", "")})
        elif isinstance(e, str):
            ex.append({"ja": e, "en": ""})
    add({
        "id": f"jk:{g['id']}",
        "pattern": g["pattern"],
        "reading": g.get("reading"),
        "level": g.get("jlpt_level") or g.get("level"),
        "formation": g.get("formation", ""),
        "meaning_en": g.get("meaning_en", ""),
        "nuance": g.get("meaning_detailed", ""),
        "examples": [e for e in ex if e["ja"]],
        "tags": ([g.get("formality")] if g.get("formality") else []) + (g.get("tags") or []),
        "keys": [],
        "sources": ["jkindrix"],
    })

# ---------- 3. jkindrix expressions（惯用表达兜底层）----------
exprs = json.loads((RAW / "jkindrix/expressions.json").read_text("utf-8"))["expressions"]
for e in exprs:
    if not e.get("text"):
        continue
    # 只留有假名的表达（纯汉字成语走词典层即可）
    if not re.search(r"[\u3041-\u30ff]", e["text"]):
        continue
    add({
        "id": f"ex:{e['id']}",
        "pattern": e["text"],
        "reading": e.get("reading"),
        "level": e.get("jlpt_waller"),
        "formation": "",
        "meaning_en": "; ".join(e.get("meanings", [])[:3]),
        "nuance": "",
        "examples": [],
        "tags": ["expression"] + (e.get("misc") or []),
        "keys": [],
        "sources": ["jmdict_exp"],
    })

# ---------- 4. Anki 语法卡组（どんなときどう使う日本語表現文型，教材级）----------
# 由 scripts/export_anki_grammar.py 离线只读导出；schema 已对齐，直接合并。
# 例句带中文译文（zh），nuance 是中文讲解——三个源里唯一自带中文的。
AG = ROOT / "data" / "anki_grammar.json"
if AG.exists():
    for e in json.loads(AG.read_text("utf-8")):
        add({
            "id": e["id"],
            "pattern": e["pattern"],
            "reading": e.get("reading"),
            "level": e.get("level"),
            "formation": e.get("formation", ""),
            "meaning_en": e.get("meaning_en", ""),
            "nuance": e.get("nuance", ""),
            "examples": e.get("examples", []),
            "tags": e.get("tags", []),
            "keys": [],
            "sources": ["anki_besshiki"],
        })

# 计算检索键
for e in entries.values():
    e["keys"] = extract_keys(e)

# 去重：同 pattern 同 level 的 ex: 条目若已被语法源覆盖则丢弃
pats = {}
for e in entries.values():
    if not e["id"].startswith("ex:"):
        pats.setdefault(norm(e["pattern"]), set()).add(norm(e["reading"] or ""))
drop = []
for pid, e in entries.items():
    if e["id"].startswith("ex:") and norm(e["pattern"]) in pats:
        drop.append(pid)
for pid in drop:
    del entries[pid]

result = sorted(entries.values(),
                key=lambda e: (LEVEL_ORDER.get(e.get("level") or "", 0), e["pattern"]))

# ---------- 6. 中文讲解转写（data/zh_nuance.json；本地模型产出，非原始数据源）----------
# gen_zh.py 把英文 nuance 转写成中文，落成独立文件，构建时合并进来；
# 带 nuance_zh 的条目 CLI 会同时给中文转写和英文原文，来源标注为「库内·中译」。
ZH_NUANCE = ROOT / "data" / "zh_nuance.json"
zh_merged = 0
if ZH_NUANCE.exists():
    zh_map = json.loads(ZH_NUANCE.read_text("utf-8"))
    for e in result:
        rec = zh_map.get(e["id"])
        if rec and rec.get("zh"):
            e["nuance_zh"] = rec["zh"]
            zh_merged += 1
print(f"nuance_zh merged: {zh_merged}")
OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), "utf-8")

# ---------- 5. 活用表（jkindrix conjugations，P0）：动词/形容词全形态 ----------
CONJ = RAW / "jkindrix" / "conjugations.json"
conj_rows = []
if CONJ.exists():
    for e in json.loads(CONJ.read_text("utf-8"))["entries"]:
        conj_rows.append((e.get("id"), e.get("dictionary_form"), e.get("reading"), e.get("class"),
                          json.dumps(e.get("forms") or {}, ensure_ascii=False),
                          json.dumps(e.get("display_forms") or {}, ensure_ascii=False)))
print(f"conjugations: {len(conj_rows)}")

# ---------- SQLite ----------
OUT_DB.unlink(missing_ok=True)
con = sqlite3.connect(OUT_DB)
con.executescript("""
CREATE TABLE grammar(
  id TEXT PRIMARY KEY, pattern TEXT, reading TEXT, level TEXT,
  formation TEXT, meaning_en TEXT, nuance TEXT, tags TEXT, sources TEXT,
  nuance_zh TEXT
);
CREATE TABLE examples(id TEXT, ja TEXT, en TEXT);
CREATE TABLE keys(key TEXT, id TEXT);
CREATE VIRTUAL TABLE grammar_fts USING fts5(pattern, meaning_en, nuance, tags,
  content='grammar', content_rowid='rowid');
CREATE TABLE conj(id TEXT PRIMARY KEY, dict_form TEXT, reading TEXT, cls TEXT,
  forms TEXT, display TEXT);
CREATE INDEX idx_conj_dict ON conj(dict_form);
CREATE INDEX idx_conj_reading ON conj(reading);
"""
)
for e in result:
    con.execute("INSERT INTO grammar VALUES(?,?,?,?,?,?,?,?,?,?)",
                (e["id"], e["pattern"], e.get("reading"), e.get("level"),
                 e.get("formation"), e.get("meaning_en"), e.get("nuance"),
                 json.dumps(e["tags"], ensure_ascii=False), json.dumps(e["sources"], ensure_ascii=False),
                 e.get("nuance_zh")))
    for ex in e["examples"]:
        con.execute("INSERT INTO examples VALUES(?,?,?)", (e["id"], ex["ja"], ex["en"]))
    for k in e["keys"]:
        con.execute("INSERT INTO keys VALUES(?,?)", (k, e["id"]))
con.execute("INSERT INTO grammar_fts(grammar_fts) VALUES('rebuild')")
if conj_rows:
    con.executemany("INSERT OR REPLACE INTO conj VALUES(?,?,?,?,?,?)", conj_rows)
con.commit()
con.close()

n = len(result)
by_src = {}
for e in result:
    for s in e["sources"]:
        by_src[s] = by_src.get(s, 0) + 1
print(f"grammar entries: {n}")
print("by source:", by_src)
print("with examples:", sum(1 for e in result if e["examples"]))
print("with nuance:", sum(1 for e in result if e.get("nuance")))
print(f"json: {OUT_JSON.stat().st_size:,} bytes | db: {OUT_DB.stat().st_size:,} bytes")
