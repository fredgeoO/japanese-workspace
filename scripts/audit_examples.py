# -*- coding: utf-8 -*-
"""例句体检：用库内自带例句（含 <b> 标注＝标准答案）量出匹配层的真实召回。

为什么需要这个脚本：
    test_match.py 只有 12 个人工句子、判据是子串比对，看不出「是匹配漏了还是库缺条目」。
    本脚本不造句子、不调 LLM：拿 1,753 条 rich 条目自带的 5,839 句例句，
    其中 2,527 句带 <b> 标出了目标语法点的字面位置——
      ① 自命中：例句是该条目的例句，match() 却连它自己都没返回 → 硬召回失败；
      ② span：命中条目的 _span 是否与 <b> 标注的 token 区间重叠（2.5 闸门第①道依赖它）；
      ③ 失败归因：键被过滤 / 键是裸功能词 / _find 全败 / 命中后被截断。
    三者都是确定性的，可以当回归基线用。

跑法：
    .venv\\Scripts\\python.exe scripts/audit_examples.py                 # 全量
    .venv\\Scripts\\python.exe scripts/audit_examples.py --limit 300     # 抽样
    .venv\\Scripts\\python.exe scripts/audit_examples.py --json          # 机器可读
    .venv\\Scripts\\python.exe scripts/audit_examples.py --source anki_besshiki
    .venv\\Scripts\\python.exe scripts/audit_examples.py --write report.md
    .venv\\Scripts\\python.exe scripts/audit_examples.py --min-effective 0.60   # 回归闸门

退出码：0 = 跑完（不设闸门）；1 = 数据层异常或 --min-effective 未达标。
"""
import argparse
import collections
import io
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ja_lookup import GrammarDB, get_tokenizer, norm, _rich  # noqa: E402

BOLD = re.compile(r"(?s)<b>(.*?)</b>")
TAG = re.compile(r"</?b>")


def clean_and_bolds(ja):
    """去掉 <b> 标签 -> 干净句子 + 粗体片段（按出现顺序，已 NFKC 归一）。"""
    clean = norm(TAG.sub("", ja or ""))
    bolds = [norm(b) for b in BOLD.findall(ja or "") if norm(b)]
    return clean, bolds


def bold_char_spans(s, bolds):
    """把粗体片段映射回归一化句子里的字符区间（顺序游标，跳过找不到的）。"""
    spans, cursor = [], 0
    for b in bolds:
        i = s.find(b, cursor)
        if i < 0:
            i = s.find(b)          # 游标失败时退回首个出现位置
        if i < 0:
            continue
        spans.append((i, i + len(b)))
        cursor = i + len(b)
    return spans


def token_index_at(offsets, pos):
    """字符位置 -> 覆盖它的 token 下标。"""
    for i, (b, e) in enumerate(offsets):
        if b <= pos < e:
            return i
    return None


def _hit_keys(g, e, s, tokens, readings, dicts, pos):
    return [k for k, hk in g._entry_keys(e) if g._find(k, hk, s, tokens, readings, dicts, pos)]


def diagnose(g, e, s, tokens, readings, dicts, pos, trace=None, max_hits=4):
    """自命中失败时归因。返回 (reason, detail)。
    有 trace（match 阶段快照）时按出口管线逐段定位：
    hits → sorted → rich（富条目闸）→ dedup（去冗余）→ final（截断）。
    dedup 丢掉但幸存条目共享同一个命中键 -> 记为 dup_covered（同一语法点的多源重复，
    不算漏配），否则才是真丢。"""
    eid = e["id"]
    found = _hit_keys(g, e, s, tokens, readings, dicts, pos)
    in_hits = bool(trace) and eid in trace.get("hits", [])
    if not in_hits:
        keys = e.get("keys") or []
        valid = g._entry_keys(e)
        if not keys:
            return "no_keys", "条目没有 keys"
        if not valid:
            return "no_valid_key", f"keys={keys} 全部被 _key_valid 拒（无假名/含 ASCII/裸ない）"
        if not g._entry_ok(e):
            short = [k for k, _hk in valid if len(k) <= 2]
            return "filtered_key", (f"keys 全被加载期闸门挡掉（裸虚词/≤2 字纯假名/碎片）："
                                    f"{short or [k for k, _ in valid]}，按设计不参与整句匹配")
        in_stop = [k for k, _hk in valid if k in GrammarDB._MATCH_STOP]
        if not [k for k, _hk in valid if k not in GrammarDB._MATCH_STOP]:
            return "stopword", f"keys 全在 _MATCH_STOP：{in_stop}"
        return "no_find" if not found else "hit_lost", f"命中键 {found}，但未进 hits 榜"
    if not found:
        return "no_find", f"合法键均未对齐：{[k for k, _ in g._entry_keys(e)]}"
    rank = trace["sorted"].index(eid) + 1 if eid in trace.get("sorted", []) else -1
    if eid not in trace.get("rich", []):
        return "rich_gate", f"排序第 {rank} 位，无接续/语感/例句被出口闸门丢掉"
    if eid not in trace.get("dedup", []):
        ek = set(found)
        for oid in trace.get("dedup", []):
            other = next((x for x in g.entries if x["id"] == oid), None)
            if not other:
                continue
            shared = ek & set(_hit_keys(g, other, s, tokens, readings, dicts, pos))
            if shared:
                return "dup_covered", f"排序第 {rank} 位，被同一语法点的多源条目「{other['pattern']}」覆盖（共享键 {sorted(shared)}）"
        return "dedup", f"排序第 {rank} 位，被去冗余丢掉；命中键 {found}"
    return "truncated", f"排序第 {rank} 位，超出 max_hits={max_hits} 被截断"




def main():
    ap = argparse.ArgumentParser(description="库内例句召回体检（ground truth = <b> 标注）")
    ap.add_argument("--limit", type=int, default=0, help="最多检查多少条例句（0=全量）")
    ap.add_argument("--max-hits", type=int, default=8, help="match 取几条（默认 8，比线上 4 宽松）")
    ap.add_argument("--source", help="只看某个来源（anki_besshiki/jkindrix/openjlpt）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--write", metavar="PATH", help="把文本报告写到文件")
    ap.add_argument("--min-effective", type=float, metavar="RATE",
                    help="回归闸门：有效召回低于该值（如 0.60）则 exit 1")
    ap.add_argument("--min-top4", type=float, metavar="RATE",
                    help="回归闸门：前 4 条位置对上课本标注的比例低于该值（如 0.80）则 exit 1")
    a = ap.parse_args()

    try:
        g = GrammarDB()
        tok = get_tokenizer()
    except BaseException as ex:
        sys.stderr.write(f"[Error] 数据层不可用：{type(ex).__name__}: {ex}\n")
        return 1

    entries = [e for e in g.entries if _rich(e) and e.get("examples")]
    if a.source:
        entries = [e for e in entries if a.source in (e.get("sources") or [])]

    stats = collections.Counter()
    reasons = collections.Counter()
    by_source = collections.defaultdict(collections.Counter)
    failures, misaligned = [], []
    checked = 0

    for e in entries:
        for ex in e["examples"]:
            if a.limit and checked >= a.limit:
                break
            ja = ex.get("ja") or ""
            s, bolds = clean_and_bolds(ja)
            if not s:
                continue
            checked += 1
            stats["examples"] += 1
            if bolds:
                stats["with_bold"] += 1

            m = tok.tokenize(s)
            tokens = [norm(t.surface()) for t in m]
            readings = [norm(t.reading_form() or t.surface()) for t in m]
            dicts = [norm(t.dictionary_form() or t.surface()) for t in m]
            pos = [t.part_of_speech() for t in m]

            tr = {}
            try:
                hits = g.match(s, max_hits=a.max_hits, tokenizer=tok, trace=tr)
            except TypeError:      # 旧版 match 无 trace 参数（A/B 对比时用）
                hits = g.match(s, max_hits=a.max_hits, tokenizer=tok)
            ids = [h["id"] for h in hits if h["id"] != e["id"]]
            stats["noise_hits"] += len(ids)

            # 前 4 条（线上默认 max_hits=4）里有没有一条的位置对上了 <b> 标注：
            # 这是「输出被截断后质量」的代理指标，比候选总数更贴近 SKILL 实际看到的。
            if bolds:
                m4 = tok.tokenize(s)
                off4 = [(t.begin(), t.end()) for t in m4]
                want4 = set()
                for cs, ce in bold_char_spans(s, bolds):
                    ti, tj = token_index_at(off4, cs), token_index_at(off4, ce - 1)
                    if ti is not None and tj is not None:
                        want4.update(range(ti, tj + 1))
                if want4:
                    stats["top4_checked"] += 1
                    for h in hits[:4]:
                        sp = h.get("_span")
                        if sp and set(range(sp[0], sp[1])) & want4:
                            stats["top4_hit"] += 1
                            break

            src = next((x for x in (e.get("sources") or [])), "?")
            self_hit = False
            span_ok = span_exact = None
            if bolds:
                offsets = [(t.begin(), t.end()) for t in m]
                want = set()
                for cs, ce in bold_char_spans(s, bolds):
                    ti, tj = token_index_at(offsets, cs), token_index_at(offsets, ce - 1)
                    if ti is not None and tj is not None:
                        want.update(range(ti, tj + 1))
            for h in hits:
                if h["id"] != e["id"]:
                    continue
                self_hit = True
                if bolds and want and h.get("_span"):
                    got = set(range(h["_span"][0], h["_span"][1]))
                    span_ok = bool(got & want)
                    span_exact = got == want
                break

            if self_hit:
                stats["self_hit"] += 1
                by_source[src]["self_hit"] += 1
                if bolds and want:
                    stats["with_span"] += 1
                    stats["span_overlap"] += bool(span_ok)
                    stats["span_exact"] += bool(span_exact)
                    if not span_ok:
                        misaligned.append({
                            "id": e["id"], "pattern": e["pattern"], "ja": s,
                            "bold": bolds,
                            "hit_span": next((h["_span"] for h in hits if h["id"] == e["id"]), None),
                            "want_tokens": sorted(want),
                        })
            else:
                reason, detail = diagnose(g, e, s, tokens, readings, dicts, pos, tr,
                                          max_hits=a.max_hits)
                reasons[reason] += 1
                stats["miss"] += 1
                if reason == "dup_covered":
                    stats["dup_covered"] += 1
                if len(failures) < 400:
                    failures.append({
                        "id": e["id"], "pattern": e["pattern"], "source": src,
                        "ja": s, "bold": bolds, "reason": reason, "detail": detail,
                        "returned": [h["pattern"] for h in hits],
                    })
            by_source[src]["examples"] += 1
        if a.limit and checked >= a.limit:
            break

    ex_n = max(1, stats["examples"])
    report = {
        "checked_examples": stats["examples"],
        "entries": len(entries),
        "with_bold": stats["with_bold"],
        "self_hit": stats["self_hit"],
        "self_hit_rate": round(stats["self_hit"] / ex_n, 4),
        "dup_covered": stats["dup_covered"],
        "effective_rate": round((stats["self_hit"] + stats["dup_covered"]) / ex_n, 4),
        "miss_rate": round(stats["miss"] / ex_n, 4),
        "span_checked": stats["with_span"],
        "span_overlap_rate": round(stats["span_overlap"] / max(1, stats["with_span"]), 4),
        "span_exact_rate": round(stats["span_exact"] / max(1, stats["with_span"]), 4),
        "top4_checked": stats["top4_checked"],
        "top4_span_rate": round(stats["top4_hit"] / max(1, stats["top4_checked"]), 4),
        "avg_noise_hits": round(stats["noise_hits"] / ex_n, 2),
        "miss_reasons": reasons.most_common(),
        "by_source": {k: {"examples": v["examples"],
                          "rate": round(v["self_hit"] / max(1, v["examples"]), 4)}
                      for k, v in by_source.items()},
        "failures": failures[:200],
        "misaligned": misaligned[:60],
    }

    if a.json:
        text = json.dumps(report, ensure_ascii=False, indent=1)
    else:
        L = []
        L.append(f"例句 {report['checked_examples']} 句（{report['entries']} 条 rich 条目）"
                 f"，其中 {report['with_bold']} 句带 <b> 标注")
        L.append(f"自命中率: {report['self_hit_rate']:.1%}"
                 f"（{stats['self_hit']}/{stats['examples']}）  未命中率: {report['miss_rate']:.1%}")
        L.append(f"有效召回（自命中＋被同点多源条目覆盖）: {report['effective_rate']:.1%}"
                 f"（dup_covered {stats['dup_covered']}）")
        L.append(f"span 与 <b> 重叠: {report['span_overlap_rate']:.1%}"
                 f"（n={stats['with_span']}）  完全一致: {report['span_exact_rate']:.1%}")
        L.append(f"前 4 条位置对上课本标注: {report['top4_span_rate']:.1%}"
                 f"（n={stats['top4_checked']}）")
        L.append(f"平均噪音条数（除目标外）: {report['avg_noise_hits']}")
        L.append("")
        L.append("按来源:")
        for k, v in report["by_source"].items():
            L.append(f"  {k}: {v['rate']:.1%}  ({v['examples']} 句)")
        L.append("")
        L.append("未命中归因:")
        for r, n in report["miss_reasons"]:
            L.append(f"  {r}: {n}")
        if failures:
            L.append("")
            L.append("未命中样本（前 20，按 pattern 去重）:")
            seen = set()
            shown = 0
            for f in failures:
                if f["pattern"] in seen:
                    continue
                seen.add(f["pattern"])
                L.append(f"  ✗ [{f['reason']}] {f['pattern']}")
                L.append(f"      例句: {f['ja'][:60]}")
                L.append(f"      返回: {f['returned'][:4]}")
                shown += 1
                if shown >= 20:
                    break
        if misaligned:
            L.append("")
            L.append("span 未对齐样本（前 8）:")
            for f in misaligned[:8]:
                L.append(f"  · {f['pattern']} | {f['ja'][:50]} | bold={f['bold']} "
                         f"| span={f['hit_span']} want={f['want_tokens']}")
        text = "\n".join(L)

    print(text)
    if a.write:
        pathlib.Path(a.write).write_text(text + "\n", encoding="utf-8")
        if not a.json:
            print(f"\n报告已写入 {a.write}")
    if a.min_effective is not None and report["effective_rate"] < a.min_effective:
        sys.stderr.write(f"\n[gate] 有效召回 {report['effective_rate']:.1%} "
                         f"低于门槛 {a.min_effective:.1%}\n")
        return 1
    if a.min_top4 is not None and report["top4_checked"] \
            and report["top4_span_rate"] < a.min_top4:
        sys.stderr.write(f"\n[gate] 前 4 条对齐 {report['top4_span_rate']:.1%} "
                         f"低于门槛 {a.min_top4:.1%}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
