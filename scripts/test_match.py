# -*- coding: utf-8 -*-
"""语法匹配命中率测试：12 个典型句子，检查目标句型是否命中、噪音是否可控。

跑法：.venv\\Scripts\\python.exe scripts/test_match.py
退出码 0 = 回归通过（KNOWN_HITS 全部命中），1 = 有回归失败。
README 记录的基线是 7/12；未命中里多数是数据源本身缺条目，故只对
KNOWN_HITS（当前库能命中的句型）设硬闸门，其余仅报告不判失败。
"""
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

# 当前库能稳定命中的句型（回归基线，7/12）；这些一旦掉出命中即视为回归。
# 匹配按「目标词干」判定：別として 命中はべつとして、得ない 命中〜得る/得ない，
# 都是同一句型的不同表记，算命中。
KNOWN_HITS = {"別として", "に決まっている", "得ない", "ずに", "うちに", "ほかない", "ように"}
# 目标词干 → 命中判定用的子串（处理汉字/假名表记差异）
STEM = {"別として": "べつとして"}


def _hit_target(target, names):
    probe = STEM.get(target, target)
    return any(probe in n for n in names)


g = GrammarDB()
tok = get_tokenizer()
ok = 0
regressions = []
for sent, target in CASES:
    hits = g.match(sent, tokenizer=tok)
    names = [h["pattern"] for h in hits]
    hit = _hit_target(target, names)
    ok += hit
    mark = "✓" if hit else "✗"
    print(f"{mark} [{target}] {sent}")
    print(f"   -> {names if names else '（无命中）'}")
    if target in KNOWN_HITS and not hit:
        regressions.append(target)
print(f"\n命中率: {ok}/{len(CASES)}")
if regressions:
    print(f"回归失败（基线句型未命中）: {', '.join(regressions)}")
    sys.exit(1)
print("回归通过：基线句型全部命中")
sys.exit(0)
