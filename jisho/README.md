# jisho/ —— Jisho 真实例句检索（精简版）

从 anki-workspace 的 `code/tools/jisho_example.py` 复制后**按本工作区需求裁剪**：
只负责**抓取 + 过滤 + 排序**真实例句（Jisho 背后的 Tatoeba / Jreibun 人工语料），
不造句子、不写 Anki、不调模型。

## 已去除的依赖（相对 anki-workspace 完整版）

| 去掉 | 原因 |
|---|---|
| ruby_engine + SudachiPy（振假名渲染/分词） | 例句检索不需要；Jisho 自带的逐词注音原样保留在 `annotations` 字段 |
| jmdict_check + 551 MB jmdict.db（读音核验） | 只服务振假名注解可信度 |
| paths / textio / minihtml / check_cards 等 anki 工作区模块 | 全部内联或删除 |
| lxml | 可选：装了就用，没装走内置极简解析器（已实测无 lxml 可跑） |

现在**唯一外部依赖是 requests**（工作区 `.venv` 已装，见根目录 `requirements.txt`；
原先随带的 `data/pylibs/` 已删除，统一走 venv）。

## 用法（本工作区根目录）

```powershell
.venv\Scripts\python.exe -X utf8 jisho\tools\jisho_example.py --word "徹夜" --reading "てつや"   # 看候选
& $py -X utf8 jisho\tools\jisho_example.py --word "猫" --json                 # JSON
& $py -X utf8 jisho\tools\jisho_example.py --word "猫" --pick 3               # 只要第 3 条
```

退出码：`0` 有候选；`2` 无可用候选（**不要退回模型造句**，换词形/放宽长度再试）；`1` 网络/解析错误。

## 与完整版的匹配差异（如实说明）

- 匹配从「Sudachi 分词按词边界比对」改为「**词干剥尾 + 假名化字符串包含**」
  （頷く→词干「頷」，能命中 頷いた/頷きながら；实测 頷く/地味/徹夜/猫 全部正常）。
  没有分词器，跨词假阳性（搜 ヤニ 命中 ニヤニヤ 一类）可能漏进来——exact 排序优先，挑句时留意。
- 输出不再有 `ruby_html`；逐词注音在 `annotations`（`[{base, reading}]`），要振假名自己拼或交给模型。

## 注意

- **一次调用只抓一页**（最多 20 条）。Jisho robots.txt 要求 Crawl-delay: 40，不要循环刷词。
- 需要完整版能力（振假名渲染、JMdict 核验、写卡联动）时去 anki-workspace 用原版。
