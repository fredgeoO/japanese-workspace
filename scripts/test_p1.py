# -*- coding: utf-8 -*-
"""P0/P1 数据层冒烟测试：汉字 / 活用 / WordNet / Tatoeba / 音调 / 频度 / 语域

跑法：.venv\\Scripts\\python.exe scripts/test_p1.py
退出码 0 = 全过，1 = 有失败项。
"""
import sqlite3
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from ja_lookup import DictDB, ConjDB, KanjiDB, WordNetDB  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(name)


con = sqlite3.connect(ROOT / "data" / "ja_dict.sqlite")
q = lambda sql: con.execute(sql).fetchone()[0]

# ---- 表规模 ----
check("words 30.8 万", q("SELECT count(*) FROM words") > 300000, f"{q('SELECT count(*) FROM words')} 条")
check("kanji 1.3 万字", q("SELECT count(*) FROM kanji") > 12000, f"{q('SELECT count(*) FROM kanji')} 字")
check("WordNet 义项 6.7 万", q("SELECT count(*) FROM wn_synset") > 60000, f"{q('SELECT count(*) FROM wn_synset')}")
check("WordNet 词义 14 万", q("SELECT count(*) FROM wn_word") > 100000, f"{q('SELECT count(*) FROM wn_word')}")
check("Tatoeba 日中句对", q("SELECT count(*) FROM sentences") > 10000, f"{q('SELECT count(*) FROM sentences')} 对")
check("tags 标签表", q("SELECT count(*) FROM tags") > 50, f"{q('SELECT count(*) FROM tags')} 条")

# ---- 新列覆盖率 ----
pitch = q("SELECT count(*) FROM words WHERE pitch IS NOT NULL AND pitch<>''")
zipf = q("SELECT count(*) FROM words WHERE zipf IS NOT NULL")
misc = q("SELECT count(*) FROM words WHERE misc<>''")
check("音调覆盖 >5 万", pitch > 50000, f"{pitch} 条")
check("Zipf 覆盖 >9 万", zipf > 90000, f"{zipf} 条")
check("语域标记 >5 千", misc > 5000, f"{misc} 条")

# ---- 具体词 ----
d = DictDB()
rows = d.lookup("言葉")
check("言葉 有条目", len(rows) >= 1, f"{len(rows)} 条")
main_row = next((r for r in rows if r["jlpt"]), rows[0] if rows else None)
if main_row:
    check("言葉 有音调", bool(main_row["pitch"]), main_row["pitch"] or "-")
    check("言葉 Zipf 在 3~7 之间", 3 <= (main_row["zipf"] or 0) <= 7, str(main_row["zipf"]))
    check("言葉 有释义", bool(main_row["gloss_zh"]), (main_row["gloss_zh"] or "")[:30])

col_rows = d.lookup("あたし")
check("あたし 有语域标记", any(r["misc"] for r in col_rows),
      ",".join(sorted({m for r in col_rows for m in (r["misc"] or "").split(",") if m})))
lab = d.tag_text("col")
check("标签中文说明", "口语" in lab, lab)

# ---- 活用 ----
c = ConjDB()
cr = c.lookup("聞く")
check("活用表 聞く 命中", len(cr) == 1, f"{len(cr)} 条")
if cr:
    pairs = dict(c.pairs(cr[0]))
    check("活用 9~10 形态", 9 <= len(pairs) <= 10, f"{len(pairs)} 个")
    check("活用 て形=聞いて", pairs.get("て形") == "聞いて", str(pairs.get("て形")))
    check("活用 ない形=聞かない", pairs.get("ない形") == "聞かない", str(pairs.get("ない形")))

# ---- 汉字 ----
k = KanjiDB().get("検")
check("汉字 検 命中", k is not None)
if k:
    check("検 音读=ケン", "ケン" in (k["on_read"] or ""), k["on_read"])
    check("検 笔画", (k["stroke"] or 0) > 0, str(k["stroke"]))
    check("検 有中文义", bool(k["meaning_zh"]), str(k["meaning_zh"]))

# ---- 中文讲解转写（P2） ----
import json as _json
_g = _json.loads((ROOT / "data" / "grammar.json").read_text("utf-8"))
zh_n = sum(1 for e in _g if e.get("nuance_zh"))
check("nuance_zh 覆盖 >900 条", zh_n > 900, f"{zh_n} 条")
kanji_zh = q("SELECT count(*) FROM kanji WHERE meaning_zh IS NOT NULL AND meaning_zh<>''")
check("汉字中文义 >1800 字", kanji_zh > 1800, f"{kanji_zh} 字")

# 全局关系覆盖（2.1 自身 relation 稀疏，靠 1.1 补齐）
hyp = q("SELECT count(*) FROM wn_rel WHERE rel='hypernym'")
ant = q("SELECT count(*) FROM wn_rel WHERE rel='antonym'")
check("上位关系补齐 >3 万", hyp > 30000, f"{hyp} 条")
check("对义关系 >5 千", ant > 5000, f"{ant} 条")

# ---- WordNet 辨析 ----
w = WordNetDB()
syn = w.explain("検討")
check("WordNet 検討 有义项", len(syn) > 0, f"{len(syn)} 个义项")
if syn:
    s0 = syn[0]
    check("検討 有日语释义", bool(s0["definition"]), (s0["definition"] or "")[:40])
    check("検討 有同类义项词", len(s0["members"]) > 1, " / ".join(s0["members"][:5]))
    with_rel = [s for s in syn if s["hypernym"] or s["antonym"]]
    check("検討 有上位/对义", bool(with_rel),
          " / ".join((with_rel[0]["hypernym"] or with_rel[0]["antonym"])[:3]) if with_rel else "-")

# ---- Tatoeba ----
sents = d.sentences("言葉", 3)
check("Tatoeba 言葉 命中例句", len(sents) > 0, f"{len(sents)} 句")
if sents:
    check("Tatoeba 例句有中文", all(x["zh"] for x in sents), sents[0]["zh"][:20])

# ---- 回归：原有语法检索没坏 ----
from ja_lookup import GrammarDB, get_tokenizer
g = GrammarDB()
hits = g.match("協力するかしないかは別として、まずは試してみよう", tokenizer=get_tokenizer())
check("语法回归 hits 非空", len(hits) > 0, f"{len(hits)} 个")

con.close()
print()
print(f"结果：{'全部通过' if not fails else '失败 ' + str(len(fails)) + ' 项 → ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
