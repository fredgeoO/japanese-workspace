# 日语本地字典库 + 语法数据集

为日语学习问答区准备的本地数据层：语法辞典 + 日中词典 + 分词检索。全部离线可用。

大模型回答日语翻译/学习问题时，只靠内在知识容易在假名读音、词义、语法接续、近义辨析上"脑补"出错。本项目把 JMdict/Jitendex、OpenJLPT/jkindrix、KANJIDIC2、Kanjium、日本語WordNet、Tatoeba 等权威开源日语语料离线归并成本地库，配 `ja_lookup.py` 检索 CLI 与 Agent Skill 规则，让 AI 解释日语时**必须引用库内数据、禁止凭记忆作答**——答案有据可查，从源头压掉幻觉。

## 目录结构

```
japanese-workspace/
├── data/
│   ├── raw/                    # 原始下载数据（幂等，重跑脚本会跳过）
│   │   ├── openjlpt/           # OpenJLPT 语法/词汇 JSON
│   │   ├── jkindrix/           # jkindrix 语法/惯用表达/活用表
│   │   ├── jmdict/             # jmdict-simplified 全量词典（英）
│   │   ├── jitendex_zh/        # jitendex 中文词典（Yomitan 格式）
│   │   ├── kanjidic/           # KANJIDIC2 汉字库
│   │   ├── kanjium/            # Kanjium 音调（pitch accent）
│   │   ├── tatoeba/            # Tatoeba 日中句对
│   │   └── wordnet/            # 日本語WordNet 2.1（LMF XML）
│   ├── grammar.json            # 合并清洗后的语法库（14,392 条，含 nuance_zh 中译）
│   ├── ja_grammar.sqlite       # 语法库 SQLite（含 FTS + conj 活用表 3,511 词）
│   ├── ja_dict.sqlite          # 词典库 SQLite（30.8 万条 + 汉字 + WordNet + 日中例句）
│   ├── zh_nuance.json          # 英文讲解 → 中文转写（本地模型，1,099 条，可重建）
│   └── zh_kanji.json           # 常用汉字英文义 → 中文（本地模型，可重建）
├── scripts/
│   ├── fetch_data.py           # 下载 OpenJLPT + jkindrix 语法/词汇
│   ├── fetch_jmdict.py         # 下载 jmdict-simplified（英）
│   ├── fetch_jitendex_zh.py    # 下载 jitendex 中文词典
│   ├── fetch_p1.py             # 下载 P1 源：KANJIDIC2 / Kanjium / Tatoeba / WordNet
│   ├── build_grammar.py        # 清洗合并 → grammar.json + ja_grammar.sqlite（含活用表/中译）
│   ├── build_keys_extra.py     # 离线扩键：把接续字段写明的同功能变体补成检索键（带接续闸门）
│   ├── build_dict.py           # 清洗合并 → ja_dict.sqlite（词/汉字/WordNet/句对）
│   ├── gen_zh.py               # 本地模型生成：英文讲解→中文、汉字英文义→中文（端点可用 JA_LLM_BASE_URL 覆盖）
│   ├── ja_lookup.py            # 检索库（GrammarDB / DictDB / ConjDB / KanjiDB / WordNetDB）
│   └── test_match.py           # 语法命中率测试（带回归闸门，非零退出码=回归）
├── jisho/                      # 子项目：抓 Jisho 真实例句（依赖走 .venv，见 jisho/README.md）
├── LICENSE                     # 仓库自研代码 MIT；数据各循上游许可（见下「数据源与许可」）
└── README.md
```

## 数据源与许可

| 数据 | 来源 | 许可 | 规模 |
|---|---|---|---|
| 语法（JLPT 分级） | [OpenJLPT](https://github.com/evanclan/OpenJLPT) | CC BY-SA 4.0 | 526 条，N5–N1，含接续/例句/笔记 |
| 语法（详细语感） | [jkindrix/japanese-language-data](https://github.com/jkindrix/japanese-language-data) | CC BY-SA 4.0 | 595 条，含 meaning_detailed 语感说明 |
| 惯用表达 | 同上（JMdict exp 抽取） | CC BY-SA 4.0 | 12,639 条 |
| **活用表** | 同上（conjugations.json） | CC BY-SA 4.0 | **3,511 词 × 10 形态** |
| 词典（中文释义） | [jitendex-yomitan-zh](https://github.com/greyindex/jitendex-yomitan-zh) | CC BY-SA 4.0（基于 JMdict） | 21 万条，全库模型校订版 |
| 词典（英文/词性/常用标记/语域） | [jmdict-simplified](https://github.com/scriptin/jmdict-simplified) | CC BY-SA 4.0（EDRDG） | 21.9 万条 |
| 词汇（JLPT 分级+例句） | OpenJLPT vocab | CC BY-SA 4.0 | 7,811 词，93% 带振假名例句 |
| **汉字** | [KANJIDIC2](https://www.edrdg.org/kanjidic/kanjidic2.xml.gz) | CC BY-SA 4.0（EDRDG） | **13,108 字**：音训/名乘/笔画/部首/学年/JLPT |
| **音调** | [Kanjium](https://github.com/mifunetoshiro/kanjium) | CC BY-SA 4.0 | 12.4 万条 pitch accent |
| **词频** | [wordfreq](https://pypi.org/project/wordfreq/) | MIT/Apache（数据 CC BY-SA） | 21 万词 Zipf 频度（构建期预计算） |
| **类义/上位/对义** | [日本語WordNet 2.1](https://bond-lab.github.io/wnja/jpn/downloads.html) + 1.1 关系补齐 | NICT 免费许可（类 BSD） | **67,509 义项 / 113,871 关系**（含 3.6 万上位、5,734 对义） |
| **日中例句** | [Tatoeba](https://tatoeba.org/en/downloads) | CC BY 2.0 FR | **16,288 对**日中句对 |
| **中文讲解转写** | 本地模型（qwen3.8-flash-next）译自库内英文 | 派生数据（不上游） | **1,099 条**语法语感 + **2,109 字**常用汉字义 |
| 分词 | [SudachiPy](https://github.com/WorksApplications/SudachiPy) + sudachidict_core | Apache 2.0 | — |

> 许可边界：仓库**自研代码与文档**（`scripts/`、`skills/`、`README.md`、`AGENTS.md` 等）采用 [MIT](LICENSE)；
> 上表所列**数据**不受 MIT 管辖，各自遵循上游许可。`data/grammar.json`、`data/anki_grammar.json`、
> `data/zh_*.json` 虽是脚本产物，但分别依赖外部 Anki 库快照与本地 LLM 非确定翻译，移出 git 会破坏
> 「clone 即用 + 结果可复现」，故作为稳定资产保留跟踪。

## 环境（自包含，不依赖其他工作区）

本工作区自带独立虚拟环境 `.venv/`（已装 sudachipy + sudachidict_core，见 `requirements.txt`）。
**所有脚本一律用 venv 的 Python 跑**，不要用系统 `python`（系统环境没装分词依赖）：

```bash
.venv\Scripts\python.exe scripts/ja_lookup.py "完整句子"
```

重建 venv：`python -m venv .venv && .venv\Scripts\python -m pip install -r requirements.txt`
（官方源慢/超时可加 `--index-url https://mirrors.aliyun.com/pypi/simple/`）。

外部素材（如 anki-workspace 的 `subtitles/` 字幕句子）**只读作测试与例句参考**，
本工作区的功能与数据不依赖它。

## 重建数据

```bash
# 1. 下载（幂等）
python scripts/fetch_data.py
python scripts/fetch_jmdict.py
python scripts/fetch_jitendex_zh.py
python scripts/fetch_p1.py        # KANJIDIC2 / Kanjium / Tatoeba / WordNet

# 2. 清洗合并
python scripts/build_grammar.py
python scripts/build_dict.py

# 2.5（可选）重新生成中文讲解 / 汉字中文义，需要本地模型端点（默认 127.0.0.1:8080，
#     可用环境变量 JA_LLM_BASE_URL 覆盖，如 $env:JA_LLM_BASE_URL="http://127.0.0.1:8080"）
#     生成结果落在 data/zh_nuance.json / data/zh_kanji.json，再跑一次上面的 build 才会入库
python scripts/gen_zh.py --nuance
python scripts/gen_zh.py --kanji

# 3. 验证（两个脚本都带退出码：0=通过，1=回归/失败，可直接用于 CI 或提交前自查）
python scripts/test_match.py       # 语法命中率回归闸门（基线句型未命中则非零退出）
python scripts/test_p1.py          # 汉字/活用/WordNet/例句/音调/频度 冒烟测试

# 2.6 离线扩键（可选）：接续字段写明了某个变体、但 keys 里没有时，补成检索键
#     默认只打印候选表与跳过原因（不改文件）；--commit 才写 data/grammar_keys_extra.json
python scripts/build_keys_extra.py           # 演练
python scripts/build_keys_extra.py --commit  # 落盘（自动备份旧文件）
```

依赖：见上方「环境」一节——用 `.venv\Scripts\python.exe` 跑即可，无需再 pip install。

## 语法库结构（data/grammar.json）

```json
{
  "id": "jk:ni-kataku-nai",
  "pattern": "Noun / V dict + に難くない",
  "level": "N1",
  "formation": "Noun + に難くない; V dict-form + に難くない.",
  "meaning_en": "easy to imagine / not hard to",
  "nuance": "〜に難くない is a literary construction ...",
  "examples": [{"ja": "...", "en": "..."}],
  "tags": ["formal"],
  "keys": ["に難くない"],
  "sources": ["jkindrix"]
}
```

`keys` 是检索特征尾串（句型本体，接续前缀已剥离），供分词匹配用。

## 检索用法

### 命令行

```bash
# 分析句子：分词 + 命中语法点（含讲解/接续/例句）
python scripts/ja_lookup.py "協力するかしないかは別として、まずは試してみよう"

# 查词典（中文释义 + 音调/频度/语域/关联词 + Tatoeba 例句）
python scripts/ja_lookup.py --dict 徹夜 わけ

# 查语法条目
python scripts/ja_lookup.py --grammar 別として

# 查活用表（辞书形/ます/て/た/ない/なかった… 共 10 形态）
python scripts/ja_lookup.py --conj 聞く

# 查汉字（音读/训读/名乘/笔画/部首/学年）
python scripts/ja_lookup.py --kanji 検

# 查 WordNet 义项：日语释义 + 同义项词（近义）+ 上位/下位/对义（辨析用）
python scripts/ja_lookup.py --syn 検討

# 查 Tatoeba 日中对照真实例句
python scripts/ja_lookup.py --sent 言葉

# JSON 输出（喂给 LLM / RAG）
python scripts/ja_lookup.py --json "彼は日本にいるうちに日本語を覚えた"
```

### Python

```python
from ja_lookup import GrammarDB, DictDB, ConjDB, KanjiDB, WordNetDB, tokenize, get_tokenizer

g, d = GrammarDB(), DictDB()

# 句子 → 命中语法点（排序：更具体优先、等级更高优先）
hits = g.match("協力するかしないかは別として", tokenizer=get_tokenizer())
for e in hits:
    print(e["pattern"], e.get("level"), e["meaning_en"])

# 词典查询
for row in d.lookup("徹夜"):
    print(row["surface"], row["reading"], row["gloss_zh"], row["pitch"], row["zipf"], row["examples"])

# 活用 / 汉字 / 辨析 / 例句
ConjDB().lookup("聞く")
KanjiDB().get("検")
WordNetDB().explain("検討")
d.sentences("言葉", limit=3)

# 分词（带读音/原形/词性）
tokenize("協力するかしないかは別として")
```

## 匹配策略（为什么不用全句匹配）

日语句型在真实句子里会变形或被助词切开（`協力するかしないかは別として` 中间夹 `かしないか`）。
检索层按你建议的思路做「特征词根组合」匹配：

1. **SudachiPy 分词**，得到 token 序列；
2. 每个语法条目预提取**特征尾串**（`別として`、`ほかない`、`に決まっている`…），接续前缀不参与匹配；
3. **含汉字尾串**按 token 窗口匹配（每字对齐 token 字面/读音，容忍 `決まっ+て+いる` 这类附加折进 token），并要求窗口内含**实义词锚点**（名/动/形/副/连体），裸虚词组合不触发；
4. **纯假名尾串**按 token 序列拼接匹配（`うちに` ← `うち`+`に`），`副词+ない` 型（何も〜ない）要求副词真实出现；
5. **条目级过滤**：keys 含裸虚词（する/たり/ない…）或英文碎片（Vない）的条目整体不参与，杜绝「する 一出现误触发一大片」；
6. 命中多条时按「尾串更长（更具体）→ 等级更高」排序，再做窗口包含去冗余。

命中率测试（`scripts/test_match.py`）：12 个典型句型句，**命中 8/12**（`別として` 按表记归一计入 `べつとして`；严格字面为 7/12）。4 个未命中：`成り上がる`/`楽しみにする`/`価値` 是数据源本身缺条目，`すればするほど` 是表记差异（句子命中的条目写作 `〜ば〜ほど`，语义相同）。脚本对 `KNOWN_HITS` 基线设了硬闸门，基线句型一旦掉出命中即 exit 1。

## 校验边界与熔断（2026-11-24 起）

解释日语时的检索范围按**三层校验边界**决定（权威定义在 `skills/ja-reading-lookup/SKILL.md` 第 0 节，`AGENTS.md` 同步）：

| 层 | 范围 | 规则 |
|---|---|---|
| **L1 强校验** | 假名读音、音调、词典释义、词频/语域、汉字音训、10 形态内活用、语法条目 ✓ 字段 | 必检索、必照抄，零例外 |
| **L2 条件校验** | 受身/使役/可能形与口语省略、长句拆解 | 库内命中才核对，未命中转 L3 |
| **L3 放手** | 近义辨析/语用/敬语、惯用语整体义、新词俚语、10 形态外活用说明、整句翻译与语境理解 | 模型自然讲解，不逐点打 `[模型推断]`（句末统一注明一句即可） |

设计意图：把强校验收缩到库的强项（词级硬事实），消除句级/语用层「整句 `[模型推断]` 刷屏」的伪严谨。放手不等于放飞——假名零幻觉、视角主体、音调口径等红线在任何层都不放手。

**熔断（降级）条件**，出现即按顺序砍层：

1. 一次解释里 L1 检索明显拖慢对话节奏 → 先只查用户问到的词，不跑整句全量；
2. 输出里「未经核对」类说明仍占讲解主体一半以上 → 砍 L2，退回「仅 L1 词级强校验，句级与语用全放手」；
3. 仍觉得检索是负担 → 只保留 `--dict` 读音/释义核对，其余全放手。

恢复条件：用户明确要求"严格核对"时临时升回原层级。

## 已知限制

- 语法库以 JLPT 体系为主，DoJG（日本語文型辞典）级别的出版辞典辨析未纳入（无开源 JSON 版）；jkindrix 的 `meaning_detailed` 是目前语感最细的来源。
- 词典库只保留「有中文释义 ∪ jmdict 常用 ∪ JLPT」的词条（30.8 万），生僻词被过滤以控制体积。
- 中文释义来自模型校订版 Jitendex，专业疑点按上游校订报告保留。
- **活用表只覆盖 10 种形态**（辞书/ます/ました/ません/ませんでした/て/た/ない/なかった/连体）。受身/使役/可能/意志/条件形**不在库内**，讲这些只能靠规则 + 标注推断。
- **音调来自 Kanjium**（NHK 音调辞典派生）：`0` = 平板型，`N` = 第 N 拍后下降，`0,2` 表示两种可接受读法。只覆盖 Kanjium 收录的 12.4 万条。
- **Zipf 频度来自 wordfreq**，是通用语料频度，不代表 JLPT 等级，也不能当"难度"用。
- **WordNet 辨析层**是日语词义网络：`--syn` 只对 WordNet 收录的词有效（衍生词、口语缩略常查不到）。同一 synset 的成员是近义，不是完全同义，讲解时仍需说明语域差异。
- **WordNet 2.1 自身的语义关系很稀疏**（hypernym 只有 723 条），构建期用 1.1 的 `synlink` 按 offset-pos 补齐。映射抽样验证：382 个义项成员重合率 95%，因此**只对「1.1 与 2.1 成员有交集」的义项合并关系**——映射漂移的义项宁可缺关系，也不挂错。补齐后 hypernym 36,105 条、antonym 5,734 条。
- **Tatoeba 日中句对只有 16,293 对**（Tatoeba 的中文翻译覆盖率本身就不高），需要更多例句时以词条自带的教材例句为主。
- `words.info` 是 JMdict 的英文用法注（如 "usually written using kana alone"），不是中文，引用时要自己判断，别当中文讲解直接抄。
- **`nuance_zh`（中文讲解转写）是本地模型对英文 `nuance` 的转写**，不是教材原文：1,099 条覆盖全部英文语感条目，原英文仍保留，冲突时以英文为准。SKILL 里的 `[库内·中译]` 标注就是给这层用的。
- **汉字中文义同理**（`zh_kanji.json`）：只做常用汉字（学年 1–8，2,136 字）的英文义转写，生僻字不生成——宁可没有，也不瞎编。

### 想加但拿不到的源（留档，别再试）

| 源 | 为什么用不了 |
|---|---|
| **Jreibun 日本語例文バンク**（18,877 例文，专家编写 + 英译） | 数据经**言語資源協会（GSK）**发放，**必须先成为 GSK 会员**才能获取；官网只提供 Jisho 上的在线浏览（`#jreibun #sentences`），批量抓取不符合其条款。 |
| **TUFS 誤用タグ付き学習者コーパス**（含中文母语者误用标注） | `corpus.icjs.jp` 是**在线检索界面**（按误用类型/母语筛选），没有批量下载出口，需按使用条款单独申请。 |
| **日本語文型辞典（DoJG）** | 无开源数据，商业版权。 |
