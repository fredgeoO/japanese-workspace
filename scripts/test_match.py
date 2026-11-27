# -*- coding: utf-8 -*-
"""语法匹配命中率测试：12 个典型句子，检查目标句型是否命中、噪音是否可控。

跑法：.venv\\Scripts\\python.exe scripts/test_match.py
退出码 0 = 回归通过（KNOWN_HITS 全部命中 + ALIAS_CASES 正反例都对），1 = 有回归失败。
当前基线 8/12；未命中里多数是数据源本身缺条目，故只对
KNOWN_HITS（当前库能命中的句型）设硬闸门，其余仅报告不判失败。
ALIAS_CASES（2026-11-24 加）单独守「同功能变体键 + 接续闸门」：正例必须命中、
反例（なら 接动词＝条件义）必须不命中。
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

# 当前库能稳定命中的句型（回归基线）；这些一旦掉出命中即视为回归。
KNOWN_HITS = {"別として", "に決まっている", "得ない", "ずに", "うちに", "ほかない",
              "ように", "すればするほど"}

# 等价组：同一句型在库里的不同表记/切分都算命中（判定用组，不用裸子串）。
# 2026-11-27 改：原来拿目标词直接做子串判定，すればするほど 被条目收录成「〜ば〜ほど」
# 就误报成未命中——测出来的 8/12 里有一个是假阴性。判据该按「功能等价」而不是字面。
EQUIV = {
    "別として": ["別として", "べつとして"],
    "すればするほど": ["すればするほど", "ば〜ほど", "ば～ほど"],
}


def _hit_target(target, names):
    probes = EQUIV.get(target, [target])
    return any(p in n for n in names for p in probes)


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
# 2026-11-24：同功能变体键 + 接续闸门回归。
# 库内只有「も〜ば、〜も」，ナA/N 上同一句型写 なら（接续字段自己写着 ナＡなら／Ｎなら），
# 故给该条目加了 も〜なら、〜も 变体键；但 なら 接动词是条件义，必须被接续闸门挡住。
# 三元组：(句子, 期望命中的 pattern 子串, 是否期望命中)
ALIAS_CASES = [
    ("値段も手ごろなら、アフターケアもきちんとしています。", "も〜ば", True),  # ナA＋なら
    ("この店は味も本物なら、値段も安い。", "も〜ば", True),                    # 名詞＋なら
    ("味もよければ値段も安い。", "も〜ば", True),                              # イA＋ければ（原键）
    ("値段が手ごろなら、その車を買いたい。", "も〜ば", False),                 # 条件なら，无第二個も
    ("私も行くなら、君も来るといい。", "も〜ば", False),                       # なら接动词＝条件，闸门拦
    ("彼は人ならだれでも援助する。", "も〜ば", False),                         # でも 里的 も 不算段的 も
    ("こんな薄暗い部屋で本を読んではいけない。", "てはいけない", True),        # ては→では（动词て形）
    ("学生ではいけない。", "てはいけない", True),                              # ては→では（名词）
    ("こんなに混んでいてはかなわない。", "てはかなわない", True),              # 原键仍命中
]

for sent, probe, want in ALIAS_CASES:
    names = [h["pattern"] for h in g.match(sent, tokenizer=tok)]
    got = any(probe in n for n in names)
    mark = "✓" if got == want else "✗"
    print(f"{mark} [变体键 期望{'命中' if want else '不命中'}] {sent}")
    print(f"   -> {names if names else '（无命中）'}")
    if got != want:
        regressions.append(f"变体键:{sent}")

print(f"\n命中率: {ok}/{len(CASES)}")
if regressions:
    print(f"回归失败（基线句型未命中）: {', '.join(regressions)}")
    sys.exit(1)
print("回归通过：基线句型全部命中")
sys.exit(0)
