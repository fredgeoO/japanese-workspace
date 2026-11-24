# -*- coding: utf-8 -*-
"""语法匹配命中率测试：12 个典型句子，检查目标句型是否命中、噪音是否可控。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ja_lookup import GrammarDB, get_tokenizer

CASES = [
    ("協力するかしないかは別として、まずは試してみよう", "別として"),
    ("勝つに決まっている", "に決まっている"),
    ("彼はまるで子供になったかのように喜んだ", "かのように"),
    ("そんなことはあり得ない", "得ない"),
    ("何も言わずに部屋を出て行った", "ずに"),
    ("彼は日本にいるうちに日本語を覚えた", "うちに"),
    ("寝ないで仕事を続けるほかない", "ほかない"),
    ("勉強すればするほど面白くなる", "すればするほど"),
    ("この本を読む価値がある", "価値"),
    ("彼はまるで何もなかったように振る舞った", "ように"),
    ("先生にお会いできるのを楽しみにしています", "楽しみにして"),
    ("彼はついに社長に成り上がった", "成り上がる"),
]

g = GrammarDB()
tok = get_tokenizer()
ok = 0
for sent, target in CASES:
    hits = g.match(sent, tokenizer=tok)
    names = [h["pattern"] for h in hits]
    hit = any(target in n for n in names)
    ok += hit
    mark = "✓" if hit else "✗"
    print(f"{mark} [{target}] {sent}")
    print(f"   -> {names if names else '（无命中）'}")
print(f"\n命中率: {ok}/{len(CASES)}")
