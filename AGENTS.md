# 日语自动解释（本工作区硬规则）

**用户消息里只要出现任何形式的日语——单词、惯用句、句型、完整句子、歌词/台词、片假名外来语、文语、中日英混写中的日文部分——一律自动解释，不需要用户说"解释/翻译/什么意思"，也不要反问"你想让我做什么"。**

唯一豁免：用户明确说"别解释/只是贴一下/记一下"等非求解释意图。纯中文/纯英文输入不触发，正常对话。

## 执行方式（固定，不得凭记忆作答）

1. 加载 skill：`skill` 工具调 `ja-reading-lookup`（本工作区 `skills/ja-reading-lookup/SKILL.md`），按其流程执行。
2. 检索必跑（冷启动约 0.4s，不是成本问题）：
   ```
   .venv\Scripts\python.exe scripts/ja_lookup.py --json "<句子>"     # 整句：分词 + 语法命中
   .venv\Scripts\python.exe scripts/ja_lookup.py --dict 徹夜 聞く      # 查词
   .venv\Scripts\python.exe scripts/ja_lookup.py --grammar ましょう  # 查语法
   ```
3. 模式判定与输出模板、来源标注（`[库内·可引用]`/`[Skill 内置表]`/`[模型推断]` 等）、红线（假名零幻觉、不补全库内缺失字段）全部以 SKILL.md 为准：
   - 整句/长句/裸贴句子 → 精读模板（**已精简，2026-11-24 起**）：
     1. **重点单词**：只挑值得学的词（生词、俚语缩略、易读错/易望文生义的），照抄检索结果的假名、词性、义项；**不做逐词全列**
     2. **整句译文**
     3. **语法点**（＋有依据的易错提醒，没有就不写）
   - 单个词或语法点 → 快查三段模板
   - 检索空手而归（文语/歌词/生僻写法）→ 仍按精读模板输出并标注推断，不得因查不到就不解释。

## 真实例句补充（本地库无例句时）

本地 `--sent`/词典例句字段为空、或想要口语感更强的真实语料时，用本工作区自带的
Jisho 检索脚本（只负责抓 Jisho 的 Tatoeba/Jreibun 人工语料例句，其余解释工作仍走上面的流程）：

```
.venv\Scripts\python.exe -X utf8 jisho\tools\jisho_example.py --word "頷く" --reading "うなずく" --json
```

规则：一次调用只抓一页（Jisho 要求 40s 爬取间隔，禁止循环刷词）；句子逐字照抄返回结果，
注音在 `annotations` 字段；无候选时（exit 2）不得退回模型造句，换词形或放宽长度再试。
引用时挂 `[Jisho·Tatoeba]` / `[Jisho·Jreibun]` 标注。详见 [jisho/README.md](jisho/README.md)。
