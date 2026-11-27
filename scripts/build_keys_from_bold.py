# -*- coding: utf-8 -*-
"""离线扩键 v2：用条目自带例句里的 <b> 标注，补「教材亲手标出来的目标字面」。

⚠️ 已实测：零收益，默认不落盘（2026-11-27）
    本脚本按下面的规则跑出过两版候选：
      - 严规则（与现有键共享 ≥3 字含汉字或 ≥4 字）：采纳 35 个键，只救回 7 条例句，
        其中 12 个因「被同点多源条目去重覆盖」注定无效；
      - 宽规则（只用语料频率上限当安全阀）：采纳 504 个键、覆盖 275 条条目，
        但「前 4 条命中位置对上课本标注」——衡量用户实际看到什么的指标——
        **80.5% → 80.5%，一点没动**，test_match 负例也没破。
    结论：剩余未命中主要不是「缺键」，而是
      ① 同一语法点在库里有多个来源条目，兄弟条目已经覆盖（学习者看得到）；
      ② 键本身形态不对（如 くする 这种「イＡく+する」被抽成片段，`短くして` 对不上）。
    所以 ② 要从 extract_keys 的键生成侧修，而不是在例句上打补丁。
    本文件保留为可复现的实验工具：跑一遍能重新验证上述数字；改动键生成逻辑后可用它回归。

为什么走这条路（而不是让模型在线判定命中）：
    <b> 是教材/资料作者标出的目标语法点字面，是库里唯一带「人工标注」的证据，
    强度高于 build_keys_extra.py 从 pattern 反推的键。扩键在离线做完、冻结进 json，
    运行时 ja_lookup.py 仍然零模型调用，召回提升但不引入不可复现的判断。

规则（全部确定性，不调 LLM）：
    1. 只处理 rich（有讲解）且例句带 <b> 的条目；
    2. 例句现在能自命中就跳过——那是匹配层的事，不需要扩键；
    3. 候选 = <b> 片段，且必须与某个现有 key 共享 ≥2 字连续子串（证明它讲的同一个句型）；
    4. 纯活用差跳过：候选与 key 的「词典形序列」相同，说明只是活用形，
       匹配层（末 token 词典形 / 段末活用对齐）已负责，加键只会涨索引；
    5. 过滤：去空格后 <3 字、含 ASCII/数字/括号、裸虚词/碎片、已是现有 key 的一律拒；
    6. 语料验证（Tatoeba 16,288 句真实语料）：
       - LIKE 命中句数 ≤ FREQ_CAP（默认 40）才算不泛——にして 这种到处都有的字面必须拒；
       - 抽样上 _find 至少命中 1 句，证明这个字面真能代表该句型；
    7. 只增不改：已落盘的键不重复加，重跑幂等。

产物：data/grammar_keys_bold.json（与 grammar_keys_extra.json 同 schema，独立一份文件，
     避免 build_keys_extra.py --commit 覆写；ja_lookup.py 两个文件都加载）。

用法：
    python scripts/build_keys_from_bold.py            # 演练：只打印，不写
    python scripts/build_keys_from_bold.py --json     # 机器可读
    python scripts/build_keys_from_bold.py --commit   # 落盘（写前备份）
"""
import argparse
import collections
import io
import json
import pathlib
import re
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ja_lookup import (DICT_DB, GrammarDB, get_tokenizer, norm, _hira,  # noqa: E402
                       _rich, is_kanji)

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "grammar_keys_bold.json"
BOLD = re.compile(r"(?s)<b>(.*?)</b>")
TAG = re.compile(r"</?b>")
FREQ_CAP = 40          # 语料里出现超过这么多句 = 太泛，不当键
MIN_KEY_LEN = 3        # 去空格后至少 3 字


def common_substr(a, b):
    """最长公共连续子串（本身，不是长度）。用于判断候选与现有 key 是不是同一句型。"""
    best = ""
    for i in range(len(a)):
        for j in range(i + len(best) + 1, len(a) + 1):
            seg = a[i:j]
            if seg in b:
                best = seg
            else:
                break
    return best


def related(cand, keys):
    """候选与现有键的关联证据。宽松的「共享 2 字」会放进 からある←からの 这种错键，
    所以要求：公共串 ≥3 字且含汉字，或 ≥4 字纯假名。返回 (是否相关, 说明)。"""
    best, detail = "", ""
    for k in keys:
        seg = common_substr(cand, k)
        if len(seg) > len(best):
            best, detail = seg, f"与键 {k} 共享「{seg}」"
    if len(best) >= 3 and any(is_kanji(c) for c in best):
        return True, detail
    if len(best) >= 4:
        return True, detail
    return False, detail or "与现有键无足够长的公共子串"


def lemma_seq(tok, text):
    m = tok.tokenize(norm(text))
    return [norm(t.dictionary_form() or t.surface()) for t in m]


def load_done(path):
    """已落盘产物 -> {entry_id: [keys]}，用于幂等。"""
    try:
        data = json.loads(pathlib.Path(path).read_text("utf-8"))
    except Exception:
        return {}
    out = {}
    for rec in data.get("entries") or []:
        for k in rec.get("add_keys") or []:
            out.setdefault(rec.get("id"), []).append(norm(k))
    return out


def collect(g, tok, done):
    accepts, skips = [], []
    corpus = sqlite3.connect(DICT_DB)
    for e in g.entries:
        if not (_rich(e) and e.get("examples")):
            continue
        keys = [k for k, _ in g._entry_keys(e)]
        if not keys:
            continue
        seen_local = set()
        for ex in e["examples"]:
            ja = ex.get("ja") or ""
            bolds = [norm(b) for b in BOLD.findall(ja) if norm(b)]
            if not bolds:
                continue
            s = norm(TAG.sub("", ja))
            # 例句已自命中 -> 无需扩键
            if e["id"] in [h["id"] for h in g.match(s, max_hits=8, tokenizer=tok)]:
                continue
            for cand in bolds:
                why = None
                if cand in seen_local:
                    continue
                seen_local.add(cand)
                if (e["id"], cand) in done:
                    why = "已在 data/grammar_keys_bold.json 中（幂等跳过）"
                elif len(cand) < MIN_KEY_LEN:
                    why = "去空格后不足 3 字"
                elif any(c.isascii() and not c.isspace() for c in cand) or \
                        any(c in cand for c in "()（）[]［］/／、。"):
                    why = "含 ASCII/括号/标点"
                elif cand in keys:
                    why = "已是现有键"
                else:
                    ok_rel, why_rel = related(cand, keys)
                    if not ok_rel:
                        why = why_rel
                    elif sorted(lemma_seq(tok, cand)) == sorted(lemma_seq(tok, keys[0])):
                        why = "纯活用差（词典形序列与 key 相同），匹配层已负责"
                if why:
                    skips.append({"id": e["id"], "pattern": e["pattern"],
                                  "cand": cand, "why": why})
                    continue
                accepts.append({
                    "id": e["id"], "pattern": e["pattern"], "cand": cand,
                    "keys": keys, "example": s, "ja_raw": ja,
                })

    # 语料验证：太泛的字面拒掉；_find 至少要真命中 1 句
    for c in accepts:
        cand = c["cand"]
        n_occ = corpus.execute("SELECT COUNT(*) FROM sentences WHERE ja LIKE ?",
                               (f"%{cand}%",)).fetchone()[0]
        c["corpus"] = {"occurrences": n_occ, "hits": 0, "sample": []}
        if n_occ > FREQ_CAP:
            c["reject"] = f"语料出现 {n_occ} 句 > 上限 {FREQ_CAP}（太泛，如 にして 类）"
            continue
        rows = corpus.execute("SELECT ja FROM sentences WHERE ja LIKE ? LIMIT 200",
                             (f"%{cand}%",)).fetchall()
        hk = any(is_kanji(ch) for ch in cand)
        for (jp,) in rows:
            m = tok.tokenize(norm(jp))
            tk = [norm(t.surface()) for t in m]
            rd = [norm(_hira(t.reading_form() or t.surface())) for t in m]
            dc = [norm(t.dictionary_form() or t.surface()) for t in m]
            ps = [t.part_of_speech() for t in m]
            if g._find(cand, hk, norm(jp), tk, rd, dc, ps):
                c["corpus"]["hits"] += 1
                if len(c["corpus"]["sample"]) < 2:
                    c["corpus"]["sample"].append(jp)
        if not c["corpus"]["hits"]:
            c["reject"] = "语料上 _find 0 命中（字面无法代表该句型）"
            continue
        # 标注自洽：候选必须能在自己的例句上对齐（<b> 正常是连续标注；
        # 对不齐说明标注跨了标点/不连续，拿来当键会污染索引）
        m = tok.tokenize(c["example"])
        tk = [norm(t.surface()) for t in m]
        rd = [norm(_hira(t.reading_form() or t.surface())) for t in m]
        dc = [norm(t.dictionary_form() or t.surface()) for t in m]
        ps = [t.part_of_speech() for t in m]
        if not g._find(cand, hk, c["example"], tk, rd, dc, ps):
            c["reject"] = "候选在自己例句上对不齐（标注不连续/跨标点）"
    corpus.close()
    return accepts, skips


def simulate(g, tok, accepts):
    """把候选键塞进索引，看条目能否出现在自己的例句结果里（最终 top-8）。
    没出现的记下死在哪个阶段——扩键救不了「被同点多源条目去重覆盖」的情况。"""
    ok = 0
    byid = {e["id"]: e for e in g.entries}
    for c in accepts:
        if c.get("reject"):
            c["fixes_own_example"] = False
            continue
        g.index.append((c["cand"], any(is_kanji(ch) for ch in c["cand"]),
                        byid[c["id"]]))
        tr = {}
        hits = g.match(c["example"], max_hits=8, tokenizer=tok, trace=tr)
        c["fixes_own_example"] = c["id"] in [h["id"] for h in hits]
        if not c["fixes_own_example"]:
            eid = c["id"]
            if eid not in tr.get("sorted", []):
                c["why_not"] = "候选键仍没把条目打进榜"
            elif eid not in tr.get("rich", []):
                c["why_not"] = "富条目闸（不该发生）"
            elif eid not in tr.get("dedup", []):
                c["why_not"] = "被同点多源条目去重覆盖（扩键救不了）"
            else:
                c["why_not"] = f"排名 {tr['sorted'].index(eid) + 1}，在 top-8 之外"
        ok += bool(c["fixes_own_example"])
        g.index.pop()
    return ok


def main():
    ap = argparse.ArgumentParser(description="用 <b> 标注离线扩键（只增不改）")
    ap.add_argument("--commit", action="store_true", help="写 data/grammar_keys_bold.json")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--freq-cap", type=int, default=FREQ_CAP, help="语料出现句数上限")
    args = ap.parse_args()

    g = GrammarDB()
    tok = get_tokenizer()
    done = load_done(OUT_PATH)
    accepts, skips = collect(g, tok, done)
    n_fix = simulate(g, tok, accepts)

    kept = [c for c in accepts if not c.get("reject")]
    rejected = [c for c in accepts if c.get("reject")]

    if args.json:
        print(json.dumps({"accepts": kept, "rejected": rejected, "skips": skips},
                         ensure_ascii=False, indent=1))
        return 0

    print(f"rich 条目 {len([e for e in g.entries if _rich(e)])} 条 → 候选 {len(accepts)}"
          f"（采纳 {len(kept)} / 语料拒 {len(rejected)} / 规则拒 {len(skips)}）")
    print(f"模拟：采纳键里能救回自己例句的 {n_fix}/{len(kept)}\n")
    if kept:
        print("【采纳】")
        fixed = [c for c in kept if c["fixes_own_example"]]
        for c in fixed[:20]:
            print(f"  ✓ {c['pattern']}  ← 键 {c['cand']}")
            print(f"      例句: {c['example'][:56]}")
            print(f"      语料: 出现 {c['corpus']['occurrences']} 句 / _find 命中 "
                  f"{c['corpus']['hits']}")
        if len(fixed) > 20:
            print(f"  …另有 {len(fixed) - 20} 条（救回例句）")
        rest = [c for c in kept if not c["fixes_own_example"]]
        if rest:
            cnt = collections.Counter(c.get("why_not", "?").split("（")[0] for c in rest)
            print(f"\n  【采但未救回】{len(rest)} 条，按原因:")
            for k, v in cnt.most_common():
                print(f"    {v:4d}  {k}")
    if rejected:
        print("\n【语料拒】")
        for c in rejected[:15]:
            print(f"  ✗ {c['pattern']} ← {c['cand']}：{c['reject']}")
        if len(rejected) > 15:
            print(f"  …另有 {len(rejected) - 15} 条")
    if skips:
        cnt = collections.Counter(s["why"].split("（")[0] for s in skips)
        print("\n【规则拒】按原因:")
        for k, v in cnt.most_common():
            print(f"  {v:4d}  {k}")

    if not args.commit:
        print("\n（演练模式，未写文件；加 --commit 落盘）")
        return 0

    payload = {
        "version": 1,
        "generator": "scripts/build_keys_from_bold.py",
        "note": "离线扩键产物：依据是条目自带例句里的 <b> 标注（人工标出的目标字面），"
                "只增不改；与 grammar_keys_extra.json 同 schema，独立文件避免被其 --commit 覆写",
        "entries": [{
            "id": c["id"],
            "pattern": c["pattern"],
            "add_keys": [c["cand"]],
            "basis": f"<b> 标注：{c['example'][:100]}",
            "guard": None,
            "corpus": c["corpus"],
        } for c in kept],
    }
    if OUT_PATH.exists():
        bak = OUT_PATH.with_suffix(".json.bak")
        bak.write_bytes(OUT_PATH.read_bytes())
        print(f"\n旧文件已备份 → {bak.name}")
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    print(f"已写入 {OUT_PATH.relative_to(ROOT)}（{len(kept)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
