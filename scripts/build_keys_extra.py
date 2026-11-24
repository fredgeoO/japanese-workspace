# -*- coding: utf-8 -*-
"""离线扩键：给语法条目补「接续字段已经写明、但 keys 里没有」的同功能变体键。

为什么需要：
    现有 keys 只从 pattern 抽（build_grammar.py: extract_keys），**从不读接续字段**。
    于是「も〜ば、〜も」的接续写着「ナＡなら／Ｎなら＋Ｎも」，匹配层却没有 なら 这一支，
    整句只能命中无关的条件 なら —— 这不是分词错，是键覆盖缺口。

本脚本只做三件事（全部确定性，不调 LLM）：
    1. 抽取：从 formation(接续) 里找出已写明的变体形式，在现有 key 上做对应替换；
    2. 过滤：ASCII/括号键、独占首段的裸形式、长度不足、已存在的键一律剔除（跳过原因写进报告）；
    3. 验证：拿新键去 sentences 表（Tatoeba 真实语料）里检索，统计能命中几句——
       只考察语料，不自己造句（自己造的句子验证不了自己）。

产物：data/grammar_keys_extra.json（仅 --commit 时写；ja_lookup.py 加载时并入索引）。

用法：
    python scripts/build_keys_extra.py            # 演练：只打印候选表
    python scripts/build_keys_extra.py --json     # 机器可读
    python scripts/build_keys_extra.py --commit   # 落盘（写前备份旧文件）
"""
import argparse
import io
import json
import pathlib
import re
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ja_lookup import (DICT_DB, EXTRA_BY_ID, EXTRA_KEYS_PATH, GRAMMAR_JSON,
                       GrammarDB, get_tokenizer, norm, _hira)

# ---- 变体族：只收「接续字段明确规定」的活用交替，且交替两侧都是真实接续形态 ----
# (接续里必须出现的字面, 现有 key 里的字面, 替换成, 运行时接续闸门前接词性)
FAMILIES = [
    # ば ⇄ なら：ナ形容词/名词用 なら（イＡ 用 ければ，动词用 ば），闸门必须卡住，
    #   否则「私も行くなら」这类条件句会被当成并列表。
    ("なら", "ば", "なら", ("形状詞", "名詞")),
    # ては ⇄ では：**不设闸门**。では 有两个来源——ナA/N + では，和动词て形 で + は
    #   （読んではいけない）。卡 ナA/N 会把后者全挡掉，是错的。
    ("では", "ては", "では", None),
]

# 抽取器的自动规则拦不住、必须人工判定的候选：键 → 跳过原因（评审结论，别删）
REVIEW_SKIP = {
    "なら〜ほど": "「〜ば〜ほど」是重复构造（ナＡなら＋ナＡな＋ほど），单个 なら〜ほど 键表达不了重复约束，会误配",
    "ならほど": "同上（无槽位字面版，价值低）",
    "ならのに": "「ば〜（のに）」的裸字面键，实际句子几乎不出现，价值低",
    "もならも": "无逗号字面版，已被 も〜なら、〜も / も〜なら〜も 覆盖",
    "なら": "「〜ば」本体的裸形式键，会让任何含 なら 的句子命中条件型「〜ば」，过松",
    "ならいい": "「〜ばいい」与「〜ならいい」语义不完全等价（条件＋いい 可另读），待人工判断",
}


def load_entries():
    data = json.load(io.open(GRAMMAR_JSON, encoding="utf-8"))
    entries = data["entries"] if isinstance(data, dict) else data
    return [e for e in entries
            if (e.get("nuance") or e.get("examples") or e.get("formation"))]


def skeleton(pattern):
    """句型的骨架键：去掉 〜/、/。 后比较。两个条目若骨架相同，视为同一句型的多源条目，
    接续字段的授权可以互认（例：openjlpt 的「〜も〜ば〜も」没写 なら，但 anki_besshiki 的
    「も〜ば、〜も」写了「ナＡなら／Ｎなら＋Ｎも」——同一句型，授权共享）。"""
    return re.sub(r"[〜、。\s]", "", norm(pattern or ""))


def sanctioned_forms(entries):
    """骨架 → 该句型任何一条目接续字段里出现过的变体形式。"""
    out = {}
    for e in entries:
        sk = skeleton(e.get("pattern"))
        out.setdefault(sk, set()).update(
            w for w, _s, _d, _p in FAMILIES if w in (e.get("formation") or ""))
    return out


def extract(entries, existing_extra):
    """返回 (accepts, skips)。accepts 每项自带依据与闸门，skips 带原因。
    existing_extra: {entry_id: [已落盘的键]}，已存在的候选跳过，保证重复跑幂等。"""
    accepts, skips, seen = [], [], set()
    done = {(rid, norm(k)) for rid, ks in (existing_extra or {}).items() for k in ks}
    by_skel = sanctioned_forms(entries)
    for e in entries:
        form = e.get("formation") or ""
        ok_forms = by_skel.get(skeleton(e.get("pattern")), set())
        keys = [norm(k).lstrip("〜~～") for k in (e.get("keys") or [])]
        for word, src, dst, prev_pos in FAMILIES:
            if word not in form and word not in ok_forms:
                continue
            for base in [k for k in keys if src in k and dst not in k]:
                new = base.replace(src, dst, 1)
                ident = (e["id"], new)
                if ident in seen:
                    continue
                seen.add(ident)

                reason = None
                if (e["id"], norm(new)) in done:
                    reason = "已在 data/grammar_keys_extra.json 中（重复跑幂等跳过）"
                elif any(c in new for c in "()（）[]［］/／") or any(
                        "a" <= c.lower() <= "z" for c in new):
                    reason = "键含 ASCII/括号，_key_valid 会拒"
                elif len(new) < 2:
                    reason = "裸形式，过短"
                elif new in keys:
                    reason = "键已存在"
                else:
                    segs = [s for s in new.split("〜") if s]
                    if segs and segs[0] == dst and len(segs) <= 2:
                        reason = "变体形式独占首段且无前锚点，过松"
                if reason is None:
                    reason = REVIEW_SKIP.get(new)
                if reason:
                    skips.append({"id": e["id"], "pattern": e["pattern"],
                                  "new": new, "why": reason})
                    continue
                slot = _guard_slot(new, dst)
                accepts.append({
                    "id": e["id"],
                    "pattern": e["pattern"],
                    "base_key": base,
                    "new_key": new,
                    "basis": (form or "(同骨架另一条目的接续)")[:160],
                    "guard": ({"slot": slot, "prev_pos": list(prev_pos)}
                              if (prev_pos and slot is not None) else None),
                })
    return accepts, skips


def _guard_slot(key, form):
    """变体形式所在段的下标（_find 里 spans[si][0] 的前一个 token 参与闸门）。"""
    for i, seg in enumerate(s for s in key.split("〜") if s):
        if form in seg:
            return i
    return None


def verify(accepts, limit_per_key=800):
    """拿新键去 Tatoeba 语料里检索：命中句数 + 例句。只用真实语料，不自造句子。"""
    g = GrammarDB()
    tok = get_tokenizer()
    db = sqlite3.connect(DICT_DB)
    for c in accepts:
        # 探针取「变体形式」这个功能性关键词（なら/では），别用整键——整键做 LIKE 几乎必然 0；
        # 精确性由随后的 _find（含接续闸门）保证，探针只负责缩小候选语料。
        probe = "では" if "では" in c["new_key"] else "なら"
        rows = db.execute(
            "SELECT ja FROM sentences WHERE ja LIKE ? LIMIT ?",
            (f"%{probe}%", limit_per_key)).fetchall()
        hit, sample = 0, []
        for (jp,) in rows:
            m = tok.tokenize(jp)
            tokens = [norm(t.surface()) for t in m]
            readings = [norm(_hira(t.reading_form() or t.surface())) for t in m]
            dicts = [norm(t.dictionary_form() or t.surface()) for t in m]
            pos = [t.part_of_speech() for t in m]
            if g._find(c["new_key"], any("\u4e00" <= ch <= "\u9fff" for ch in c["new_key"]),
                       norm(jp), tokens, readings, dicts, pos):
                hit += 1
                if len(sample) < 2:
                    sample.append(jp)
        c["corpus"] = {"scanned": len(rows), "hits": hit, "examples": sample}
    db.close()
    return accepts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="写 data/grammar_keys_extra.json")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    entries = load_entries()
    accepts, skips = extract(entries, EXTRA_BY_ID)
    verify(accepts)

    if args.json:
        print(json.dumps({"accepts": accepts, "skips": skips}, ensure_ascii=False, indent=1))
        return 0

    print(f"有讲解条目 {len(entries)} 条 → 候选 {len(accepts) + len(skips)} 个"
          f"（采纳 {len(accepts)} / 跳过 {len(skips)}）\n")
    if accepts:
        print("【采纳】")
        for c in accepts:
            corpus = c["corpus"]
            tag = f"语料命中 {corpus['hits']}/{corpus['scanned']}" if corpus["hits"] else \
                  f"语料扫描 {corpus['scanned']} 句未命中（依据仅接续字段）"
            guard = c["guard"]
            g_txt = f"接续闸门：第{guard['slot']}段前接 {'/'.join(guard['prev_pos'])}" if guard else "无闸门"
            print(f"  ✓ {c['pattern']}")
            print(f"      键 {c['base_key']} → {c['new_key']}")
            print(f"      依据：{c['basis']}")
            print(f"      {g_txt}；{tag}")
            for ex in c["corpus"]["examples"]:
                print(f"      语料：{ex}")
    if skips:
        print("\n【跳过】")
        for s in skips:
            print(f"  ✗ {s['pattern']} → {s['new']}：{s['why']}")

    if not args.commit:
        print("\n（演练模式，未写文件；加 --commit 落盘）")
        return 0

    payload = {
        "version": 1,
        "generator": "scripts/build_keys_extra.py",
        "note": "离线扩键产物：只增不改，依据必须来自条目自带的 formation(接续) 字段",
        "entries": [{
            "id": c["id"],
            "pattern": c["pattern"],
            "add_keys": [c["new_key"]],
            "basis": c["basis"],
            "guard": c["guard"],
        } for c in accepts],
    }
    if EXTRA_KEYS_PATH.exists():
        bak = EXTRA_KEYS_PATH.with_suffix(".json.bak")
        bak.write_bytes(EXTRA_KEYS_PATH.read_bytes())
        print(f"\n旧文件已备份 → {bak.name}")
    EXTRA_KEYS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"已写入 {EXTRA_KEYS_PATH.relative_to(EXTRA_KEYS_PATH.parent.parent)}"
          f"（{len(payload['entries'])} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
