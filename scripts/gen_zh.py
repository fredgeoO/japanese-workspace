# -*- coding: utf-8 -*-
"""用本地模型把库内英文讲解转写成中文（可断点续跑）

两种模式：
  --nuance   语法条目：nuance 是英文的 627 条 → data/zh_nuance.json
  --kanji    汉字层：常用汉字/常用漢字的英文义 → data/zh_kanji.json
  --sample N 只用前 N 条试跑，不写正式文件（输出到 stdout 供人工抽检）

红线：只做「翻译 + 压缩」，不新增原文没有的信息；句型原样保留；
      每条都记 model / ts / src_hash，源文变了就能识别出来重做。
"""
import argparse
import hashlib
import json
import pathlib
import re
import sqlite3
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"
MODEL = "qwen3.8-flash-next-ud_iq3_xxs"

SYS_NUANCE = """你在为一个日语学习工具做「英文 → 中文」讲解转写。输入是若干条语法点的英文语感说明，key 是条目 id。
规则：
1. 只翻译，不解释、不总结、不举例、不评论；原文没有的信息一个字都不许加；
2. 输出一个 JSON 对象，key 与输入完全一致，value 是中文译文，一般 20–80 字；
3. 句型（如 〜うちに、〜おかげで）原样保留，不要改成中文；
4. 原文提到的对比项（如 "unlike 〜せいで"）必须保留；
5. 原文信息不足以成句时，照原文直译，不要补全；
6. 只输出 JSON，不要 markdown 代码块，不要任何解释。"""

SYS_KANJI = """你在为日语学习工具补充汉字的中文义。输入是一批汉字及其英文释义。
规则：
1. 输出 JSON 对象，key 是汉字本身，value 是 2–8 个字的中文义项，多项用「；」分隔；
2. 只依据给出的英文释义转写，不补充英文里没有的义项；
3. 汉字和假名不要出现错误写法；
4. 只输出 JSON，不要 markdown，不要解释。"""


def chat(messages, max_tokens=3000, retries=3):
    payload = {"model": MODEL, "messages": messages, "max_tokens": max_tokens,
               "temperature": 0.1,
               # 这个模型默认带思考：不关掉会把输出预算花在 reasoning 上，整批 JSON 被截断
               "chat_template_kwargs": {"enable_thinking": False}}
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
            d = json.loads(urllib.request.urlopen(req, timeout=300).read())
            return d["choices"][0]["message"].get("content") or ""
        except Exception as exc:
            last = exc
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"LLM 调用失败: {last}")


def translate_batch(payload, system, depth=0):
    """整批翻译；覆盖不足 60% 就二分重试，最后逐条兜底。返回 {key: 中文}。"""
    content = chat([{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
    got = {k: v for k, v in parse_json(content).items()
           if isinstance(v, str) and re.search(r"[\u4e00-\u9fff]", v)}
    if len(got) >= len(payload) * 0.6 or depth >= 2 or len(payload) == 1:
        if not got:
            print(f"[WARN] 整批解析失败，输出前 120 字: {content[:120]!r}", flush=True)
        return got
    keys = list(payload)
    mid = len(keys) // 2
    left, right = keys[:mid], keys[mid:]
    out = translate_batch({k: payload[k] for k in left}, system, depth + 1)
    out.update(translate_batch({k: payload[k] for k in right}, system, depth + 1))
    return out


def parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        return json.loads(text[i:j + 1])
    except json.JSONDecodeError:
        return {}


def is_english(s):
    """判断一条讲解是否「英文原文」。

    不能只看有没有汉字：英文讲解里常引用日语句型（〜間に、〜そのもの），
    这些汉字会让「含汉字 = 已是中文」的简单判断误判。按 ASCII 字母数 + 汉字上限判。
    """
    return len(re.findall(r"[A-Za-z]", s)) >= 20 and len(re.findall(r"[\u4e00-\u9fff]", s)) <= 10


def sha(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- nuance
def gen_nuance(sample=0, batch_size=10, force=False):
    out_path = DATA / "zh_nuance.json"
    done = {}
    if out_path.exists() and not sample and not force:
        done = json.loads(out_path.read_text("utf-8"))
    entries = json.loads((DATA / "grammar.json").read_text("utf-8"))
    todo = []
    for e in entries:
        nu = (e.get("nuance") or "").strip()
        if not nu or not is_english(nu):
            continue  # 没讲解 / 本来就是中文
        if e["id"] in done and done[e["id"]].get("src_hash") == sha(nu):
            continue
        todo.append(e)
    if sample:
        todo = todo[:sample]
    print(f"待转写: {len(todo)} 条", flush=True)

    stats = {"ok": 0, "bad": 0}
    for start in range(0, len(todo), batch_size):
        chunk = todo[start:start + batch_size]
        payload = {e["id"]: (e.get("nuance") or "")[:600] for e in chunk}
        t0 = time.time()
        got = translate_batch(payload, SYS_NUANCE)
        for e in chunk:
            zh = (got.get(e["id"]) or "").strip()
            if zh and re.search(r"[\u4e00-\u9fff]", zh):
                stats["ok"] += 1
                if sample:
                    print(f"\n[{e['id']}] {e.get('pattern')}\n  EN: {(e.get('nuance') or '')[:160]}\n  ZH: {zh}")
                else:
                    done[e["id"]] = {"zh": zh, "model": MODEL,
                                     "src_hash": sha(e.get("nuance") or ""),
                                     "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
            else:
                stats["bad"] += 1
                if sample:
                    print(f"[MISS] {e['id']} -> {content[:120]}")
        print(f"{start + len(chunk)}/{len(todo)} ok={stats['ok']} bad={stats['bad']} "
              f"({round(time.time() - t0, 1)}s)", flush=True)
        if sample:
            continue
        out_path.write_text(json.dumps(done, ensure_ascii=False, indent=1), "utf-8")
    print(f"完成: ok={stats['ok']} bad={stats['bad']} 总存量={len(done)}", flush=True)


# ---------------------------------------------------------------- kanji
def gen_kanji(sample=0, batch_size=20):
    out_path = DATA / "zh_kanji.json"
    done = {}
    if out_path.exists() and not sample:
        done = json.loads(out_path.read_text("utf-8"))
    con = sqlite3.connect(DATA / "ja_dict.sqlite")
    # 只做常用汉字（学年 1-8），生僻字不做，避免瞎编
    rows = con.execute(
        "SELECT literal, meaning_en FROM kanji WHERE grade BETWEEN 1 AND 8 AND meaning_en<>''"
    ).fetchall()
    con.close()
    todo = [(lit, en) for lit, en in rows
            if lit not in done or done[lit].get("src_hash") != sha(en)]
    if sample:
        todo = todo[:sample]
    print(f"待转写汉字: {len(todo)} 字", flush=True)

    stats = {"ok": 0, "bad": 0}
    for start in range(0, len(todo), batch_size):
        chunk = todo[start:start + batch_size]
        payload = {lit: en[:200] for lit, en in chunk}
        got = translate_batch(payload, SYS_KANJI)
        for lit, en in chunk:
            zh = (got.get(lit) or "").strip()
            if zh and re.search(r"[\u4e00-\u9fff]", zh):
                stats["ok"] += 1
                if sample:
                    print(f"  {lit}  {zh}   (EN: {en[:60]})")
                else:
                    done[lit] = {"zh": zh, "model": MODEL, "src_hash": sha(en),
                                 "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
            else:
                stats["bad"] += 1
        print(f"{start + len(chunk)}/{len(todo)} ok={stats['ok']} bad={stats['bad']}", flush=True)
        if sample:
            continue
        out_path.write_text(json.dumps(done, ensure_ascii=False, indent=1), "utf-8")
    print(f"完成: ok={stats['ok']} bad={stats['bad']} 总存量={len(done)}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--nuance", action="store_true")
    ap.add_argument("--kanji", action="store_true")
    ap.add_argument("--sample", type=int, default=0, help="试跑条数（不写正式文件）")
    ap.add_argument("--force", action="store_true", help="忽略已有结果，整批重做")
    a = ap.parse_args()
    if not (a.nuance or a.kanji):
        ap.print_help()
        sys.exit(1)
    if a.nuance:
        gen_nuance(a.sample, force=a.force)
    if a.kanji:
        gen_kanji(a.sample)
