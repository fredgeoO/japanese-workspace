#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日語學習知識圖譜引擎（jgraph）。

只讀取本工作區的資料庫，把「語法 ↔ 單詞 ↔ 例句」之間真實存在的關聯算出來，
輸出給 DSH 外掛 `@local/jp-graph` 的左側欄面板畫成關係圖。

用法：
  .venv\\Scripts\\python.exe scripts/jgraph.py --serve          # 常駐 worker（一行一個 JSON 請求）
  .venv\\Scripts\\python.exe scripts/jgraph.py --search 学校
  .venv\\Scripts\\python.exe scripts/jgraph.py --node word:12345
  .venv\\Scripts\\python.exe scripts/jgraph.py --request '{"op":"facets"}'

資料來源（全部只讀）：
  data/ja_grammar.sqlite   grammar / keys / examples / conj
  data/ja_dict.sqlite      words / sentences / wn_rel / wn_synset / wn_word
  scripts/ja_lookup.py     既有比對器（GrammarDB.match / tokenize），與 skill 同一套規則
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ja_lookup as J  # noqa: E402  （工作區既有比對器）

GRAMMAR_DB = ROOT / "data" / "ja_grammar.sqlite"
DICT_DB = ROOT / "data" / "ja_dict.sqlite"

MAX_TEXT = 1200

# ---------------------------------------------------------------------------
# 分類面向：把資料庫裡既有的 tags 整理成「功能 / 語域 / 難度 / 類型」
# ---------------------------------------------------------------------------

FACET_GROUPS = [
    ("function", "功能", [
        ("reason", "原因・理由"), ("result", "結果"), ("condition", "條件"),
        ("concession", "逆接・讓歩"), ("contrast", "對比"), ("emphasis", "強調"),
        ("negation", "否定"), ("time", "時間"), ("simultaneity", "同時"),
        ("sequence", "順序"), ("tense", "時態"), ("degree", "程度"),
        ("quantity", "數量"), ("range", "範圍"), ("comparison", "比較"),
        ("conjecture", "推量・推測"), ("possibility", "可能"), ("hearsay", "傳聞"),
        ("tendency", "傾向"), ("appearance", "樣態"), ("emotion", "感情"),
        ("surprise", "驚訝"), ("regret", "後悔"), ("wish", "願望"),
        ("exclamation", "感嘆"), ("standpoint", "立場"), ("topic", "主題"),
        ("relation", "關係"), ("addition", "添加"), ("limitation", "限定"),
        ("exclusion", "除外"), ("manner", "方式"), ("means", "手段"),
        ("quote", "引用"), ("quotation", "引用句"), ("obligation", "義務"),
        ("necessity", "必要"), ("permission", "許可"), ("prohibition", "禁止"),
        ("ability", "能力"), ("advice", "建議"), ("request", "請求"),
        ("command", "命令"), ("suggestion", "提議"), ("invitation", "邀請"),
        ("intention", "意向"), ("decision", "決定"), ("desire", "希望"),
        ("change", "變化"), ("completion", "完成"), ("attempt", "嘗試"),
        ("experience", "經驗"), ("state", "狀態"), ("form", "形式"),
        ("particle", "助詞"), ("conjugation", "活用"), ("nominalization", "名詞化"),
        ("copula", "斷定"), ("voice", "語態"), ("demonstrative", "指示"),
        ("giving-receiving", "授受"), ("honorific", "尊敬"), ("humble", "謙讓"),
        ("politeness", "敬語"), ("purpose", "目的"), ("example", "例示"),
    ]),
    ("register", "語域", [
        ("formal", "正式"), ("neutral", "中性"), ("casual", "口語"),
        ("col", "口語（俗）"), ("written", "書面語"), ("書き言葉", "書面語"),
        ("話し言葉", "口語（話）"), ("硬い", "生硬"), ("丁寧", "禮貌"),
        ("pol", "敬語"), ("hon", "尊敬語"), ("hum", "謙讓語"),
        ("arch", "古語"), ("hist", "歷史"), ("rare", "罕用"), ("obs", "廢語"),
        ("dated", "舊式"), ("sl", "俚語"), ("net-sl", "網路用語"),
        ("m-sl", "漫畫用語"), ("vulg", "粗俗"), ("derog", "貶義"),
        ("joc", "戲謔"), ("euph", "委婉"), ("fem", "女性語"), ("male", "男性語"),
        ("poet", "詩語"), ("on-mim", "擬聲擬態"), ("abbr", "縮略"),
        ("chn", "兒童語"), ("fam", "親昵"), ("uk", "常假名書寫"),
    ]),
    ("level", "難度", [
        ("N5", "N5"), ("N4", "N4"), ("N3", "N3"), ("N2", "N2"), ("N1", "N1"),
        ("★5", "★5 最重要"), ("★4", "★4"), ("★3", "★3"), ("★2", "★2"), ("★1", "★1"),
    ]),
    ("type", "類型", [
        ("textbook", "教材"), ("expression", "慣用表達"), ("id", "慣用句"),
        ("proverb", "諺語"), ("uk", "假名書寫"),
    ]),
]

TAG_LABEL = {}
for _gid, _glabel, _tags in FACET_GROUPS:
    for _tag, _label in _tags:
        TAG_LABEL.setdefault(_tag, _label)

REL_LABEL = {
    "eq_synonym": "同義", "antonym": "反義", "hypernym": "上位", "hyponym": "下位",
    "similar": "類義", "also": "關聯", "instance_hypernym": "上位（專名）",
    "instance_hyponym": "下位（專名）", "mero_part": "部分", "holo_part": "整體",
    "mero_member": "成員", "holo_member": "所屬", "attribute": "屬性",
    "domain_topic": "領域", "has_domain_topic": "領域", "entails": "蘊含",
    "is_entailed_by": "被蘊含", "causes": "致使", "is_caused_by": "受致",
    "restricts": "限定", "restricted_by": "受限定",
}

POS_ZH = {
    "n": "名詞", "v1": "動詞", "v5": "動詞", "vs": "サ変名詞", "vk": "カ変動詞",
    "adj-i": "い形容詞", "adj-na": "な形容詞", "adv": "副詞", "exp": "慣用句",
    "prt": "助詞", "conj": "接續詞", "int": "感嘆詞", "pn": "代名詞", "num": "數詞",
    "pref": "接頭辭", "suf": "接尾辭", "ctr": "量詞", "aux": "助動詞", "cop": "斷定",
}

CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]")
B_TAG_RE = re.compile(r"</?b>")

STOPWORDS = {
    "この", "その", "あの", "どの", "これ", "それ", "あれ", "どれ", "ここ", "そこ",
    "ある", "いる", "する", "なる", "ない", "です", "ます", "こと", "もの", "ため",
    "よう", "そう", "どう", "とても", "ちょっと", "もう", "まだ", "そして", "でも",
}

# 資料庫的中文釋義是簡體；使用者常打繁體。這裡只為「搜尋」做一個輕量對照，
# 不影響任何顯示文字。（只收常見字，查不到時仍會用原字再查一次。）
T2S_PAIRS = (
    "愛爱 罷罢 備备 貝贝 筆笔 幣币 畢毕 邊边 變变 標标 別别 賓宾 補补 參参 殘残 慘惨 "
    "倉仓 產产 長长 場场 嘗尝 車车 稱称 陳陈 塵尘 襯衬 處处 傳传 創创 純纯 詞词 從从 "
    "錯错 達达 帶带 單单 擔担 膽胆 當当 檔档 導导 燈灯 敵敌 點点 電电 動动 東东 讀读 "
    "斷断 對对 隊队 頓顿 爾尔 發发 範范 飛飞 費费 紛纷 豐丰 風风 複复 復复 該该 蓋盖 "
    "幹干 趕赶 剛刚 鋼钢 個个 給给 觀观 關关 廣广 歸归 規规 貴贵 國国 過过 還还 號号 "
    "後后 華华 畫画 話话 壞坏 歡欢 環环 換换 會会 幾几 機机 計计 記记 際际 繼继 價价 "
    "間间 簡简 見见 鍵键 講讲 將将 獎奖 較较 結结 節节 進进 經经 靜静 舉举 據据 劇剧 "
    "覺觉 決决 開开 課课 況况 來来 賴赖 類类 離离 裡里 裏里 歷历 麗丽 連连 聯联 練练 "
    "兩两 臨临 靈灵 領领 麼么 沒没 們们 夢梦 難难 腦脑 內内 擬拟 寧宁 農农 溫温 歐欧 "
    "盤盘 親亲 輕轻 請请 慶庆 區区 權权 讓让 熱热 認认 榮荣 賽赛 聲声 時时 實实 識识 "
    "師师 書书 數数 術术 誰谁 說说 雖虽 鬆松 態态 體体 聽听 條条 統统 頭头 圖图 團团 "
    "萬万 為为 圍围 問问 無无 務务 誤误 習习 係系 戲戏 顯显 現现 線线 寫写 學学 選选 "
    "尋寻 訓训 樣样 業业 億亿 義义 議议 應应 優优 語语 與与 預预 園园 遠远 願愿 運运 "
    "雜杂 責责 則则 擇择 戰战 張张 這这 針针 證证 隻只 紙纸 製制 種种 週周 專专 轉转 "
    "裝装 狀状 準准 資资 總总 組组 續续 歲岁 屬属 隨随 彈弹 牆墙 擴扩 擇择 瀏浏 覽览 "
    "億亿 傳传 劍剑 勁劲 勞劳 勢势 勤勤 勵励 匯汇 厲厉 厭厌 厲厉 嘆叹 囑嘱 嘩哗 噴喷 "
    "嚮向 嚴严 囉罗 圓圆 圖图 塵尘 墊垫 壞坏 壯壮 夠够 奮奋 媽妈 嬰婴 孫孙 學学 寶宝 "
    "尷尴 屆届 屜屉 屬属 島岛 嶺岭 巖岩 幣币 幫帮 廣广 廳厅 彈弹 彌弥 彎弯 徵征 徹彻 "
    "恆恒 悅悦 惱恼 愛爱 慣惯 慶庆 憂忧 憑凭 懷怀 懸悬 戀恋 挾挟 捨舍 掃扫 掛挂 採采 "
    "換换 揮挥 損损 搖摇 搶抢 摑掴 摟搂 摸 撐撑 撥拨 擁拥 擇择 擊击 擔担 據据 擠挤 "
    "擬拟 擴扩 擺摆 擾扰 攝摄 攤摊 敗败 敵敌 斃毙 斷断 於于 旅 晉晋 晝昼 暈晕 曆历 "
    "書书 會会 條条 楊杨 榮荣 構构 槍枪 樂乐 樓楼 標标 樞枢 樣样 樹树 橋桥 檢检 權权 "
    "歐欧 殺杀 殼壳 毀毁 氣气 氫氢 漢汉 湯汤 溝沟 滅灭 滯滞 滿满 漁渔 滾滚 漲涨 澀涩 "
    "濃浓 濕湿 濟济 濤涛 濫滥 濾滤 瀟潇 灑洒 灘滩 災灾 為为 烏乌 無无 煉炼 煙烟 煩烦 "
    "熱热 營营 燦灿 燭烛 爐炉 爭争 牆墙 狀状 獨独 獲获 獄狱 獸兽 獻献 環环 瓊琼 產产 "
    "畫画 異异 當当 疊叠 痺痹 瘋疯 療疗 癒愈 發发 皺皱 盃杯 盜盗 盡尽 監监 盤盘 眾众 "
    "睜睁 瞞瞒 矚瞩 礦矿 碼码 磚砖 磁 禮礼 禱祷 稅税 穩稳 窮穷 竊窃 競竞 筆笔 節节 "
    "範范 築筑 篤笃 籃篮 籌筹 簽签 簡简 糧粮 糾纠 紀紀 約约 紅红 級级 納纳 純纯 紙纸 "
    "紛纷 細细 組组 終终 紹绍 結结 給给 絕绝 統统 絲丝 綁绑 經经 綜综 綠绿 網网 維维 "
    "緒绪 緣缘 編编 緩缓 練练 縣县 縱纵 縮缩 總总 績绩 繁 織织 繞绕 繪绘 繼继 續续 "
    "罰罚 罷罢 義义 習习 聯联 聲声 職职 聽听 肅肃 脅胁 脫脱 腦脑 臉脸 臨临 臺台 舊旧 "
    "艙舱 艦舰 藝艺 節节 華华 萬万 葉叶 著着 蓋盖 蓮莲 薦荐 藥药 蘇苏 蘭兰 處处 號号 "
    "虛虚 蟲虫 術术 衛卫 衝冲 補补 裝装 裡里 襲袭 見见 規规 視视 親亲 覺觉 覽览 觀观 "
    "觸触 訂订 計计 訊讯 討讨 訓训 記记 講讲 許许 論论 設设 訪访 証证 評评 詞词 試试 "
    "詩诗 話话 該该 詳详 認认 語语 誠诚 說说 誰谁 課课 調调 談谈 請请 論论 諮谘 證证 "
    "識识 譜谱 變变 讚赞 豐丰 豬猪 貝贝 負负 財财 貫贯 責责 貴贵 買买 費费 貼贴 資资 "
    "賓宾 賜赐 賞赏 賠赔 賢贤 賣卖 質质 賴赖 購购 賽赛 贈赠 贊赞 贏赢 趙赵 趨趋 蹤踪 "
    "軍军 車车 軌轨 軒轩 軟软 較较 載载 輔辅 輕轻 輛辆 輝辉 輪轮 輸输 轉转 轟轰 辦办 "
    "辭辞 農农 邊边 過过 達达 違违 遠远 適适 選选 遷迁 遺遗 遼辽 邁迈 還还 邇迩 鄉乡 "
    "鄧邓 醫医 釋释 裡里 鑒鉴 針针 釘钉 釣钓 鈴铃 銀银 銅铜 銘铭 鋼钢 錄录 錢钱 錦锦 "
    "錯错 鍊炼 鍋锅 鍵键 鍾钟 鎖锁 鎮镇 鏡镜 鑑鉴 長长 門门 閃闪 閉闭 開开 閒闲 間间 "
    "閣阁 閱阅 闆板 關关 陽阳 陰阴 陳陈 陸陆 陰阴 隊队 階阶 隨随 險险 隱隐 難难 雛雏 "
    "雜杂 雞鸡 離离 電电 霧雾 靈灵 靜静 韓韩 頁页 頂顶 項项 順顺 須须 預预 頓顿 領领 "
    "頭头 頻频 顆颗 題题 顏颜 願愿 類类 顧顾 顯显 風风 飛飞 餐 餘余 館馆 首 香 馬马 "
    "駐驻 駕驾 騎骑 驚惊 驗验 髮发 鬥斗 魚鱼 鮮鲜 鳥鸟 鳴鸣 鴻鸿 鵝鹅 鷹鹰 鹽盐 麗丽 "
    "麥麦 黃黄 點点 黨党 鼓 鼠 龍龙 龜龟 齒齿 齡龄 擴扩 藏 灣湾 瀏浏 覽览 讚赞 郵邮 "
)
T2S = {}
for _pair in T2S_PAIRS.split():
    if len(_pair) == 2:
        T2S[ord(_pair[0])] = _pair[1]


def t2s(text: str) -> str:
    return (text or "").translate(T2S)


# 只有「功能」面向的標籤才拿來當語法之間的連線；語域／難度／類型不算同功能。
FUNCTION_TAGS = {tag for gid, _label, tags in FACET_GROUPS if gid == "function" for tag, _ in tags}


def strip_b(text: str) -> str:
    return B_TAG_RE.sub("", text or "")


def clip(text: str, limit: int = MAX_TEXT) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def is_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text or ""))


def norm_key(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip()


# ---------------------------------------------------------------------------
# 資料層
# ---------------------------------------------------------------------------


class Data:
    def __init__(self) -> None:
        self.g = sqlite3.connect(f"file:{GRAMMAR_DB}?mode=ro", uri=True)
        self.g.row_factory = sqlite3.Row
        self.d = sqlite3.connect(f"file:{DICT_DB}?mode=ro", uri=True)
        self.d.row_factory = sqlite3.Row
        self.grammar = J.GrammarDB()
        self._by_id = {e.get("id"): e for e in self.grammar.entries if e.get("id")}
        self._tok = None
        self._tag_counts = None
        self._word_cache: dict[str, dict | None] = {}
        self._match_cache: dict[str, list] = {}
        self._facet_cache: dict[tuple, dict] = {}

    # -- 基礎 ------------------------------------------------
    def tokenizer(self):
        if self._tok is None:
            self._tok = J.get_tokenizer()
        return self._tok

    def tokens(self, text: str) -> list[dict]:
        try:
            return J.tokenize(text or "")
        except Exception:
            return []

    def word_by_surface(self, surface: str, reading: str | None = None) -> dict | None:
        key = f"{surface}\u0000{reading or ''}"
        if key in self._word_cache:
            return self._word_cache[key]
        row = None
        if reading:
            row = self.d.execute(
                "select * from words where surface=? and reading=? order by jlpt is null, jlpt, zipf desc limit 1",
                (surface, reading),
            ).fetchone()
        if row is None:
            row = self.d.execute(
                "select * from words where surface=? order by jlpt is null, jlpt, zipf desc limit 1",
                (surface,),
            ).fetchone()
        value = dict(row) if row else None
        self._word_cache[key] = value
        return value

    def word_by_id(self, ident: str) -> dict | None:
        row = self.d.execute("select * from words where id=?", (ident,)).fetchone()
        return dict(row) if row else None

    def grammar_entry(self, ident: str) -> dict | None:
        entry = self._by_id.get(ident)
        if entry is not None:
            return entry
        row = self.g.execute("select * from grammar where id=?", (ident,)).fetchone()
        if not row:
            return None
        e = dict(row)
        for field in ("tags", "sources"):
            try:
                e[field] = json.loads(e.get(field) or "[]")
            except Exception:
                e[field] = []
        e.setdefault("keys", [])
        e["examples"] = [
            {"ja": r["ja"], "en": r["en"], "zh": ""}
            for r in self.g.execute("select ja, en from examples where id=?", (ident,)).fetchall()
        ]
        return e

    def match(self, sentence: str, max_hits: int = 4) -> list[dict]:
        text = strip_b(sentence or "")
        if not text:
            return []
        cached = self._match_cache.get(text)
        if cached is not None:
            return cached
        try:
            hits = self.grammar.match(text, max_hits=max_hits)
        except Exception:
            hits = []
        self._match_cache[text] = hits
        return hits

    # -- 分類 ------------------------------------------------
    def tag_counts(self) -> dict[str, int]:
        if self._tag_counts is None:
            counts: dict[str, int] = {}
            for (raw,) in self.g.execute("select tags from grammar where tags is not null and tags != ''"):
                try:
                    tags = json.loads(raw)
                except Exception:
                    continue
                for tag in tags:
                    counts[tag] = counts.get(tag, 0) + 1
            self._tag_counts = counts
        return self._tag_counts

    def facets(self) -> dict:
        counts = self.tag_counts()
        groups = []
        for gid, glabel, tags in FACET_GROUPS:
            items = []
            for tag, label in tags:
                n = counts.get(tag, 0)
                if gid == "level" and tag.startswith("N"):
                    n = self.g.execute("select count(*) from grammar where level=?", (tag,)).fetchone()[0]
                if n:
                    items.append({"tag": tag, "label": label, "count": n})
            if items:
                groups.append({"id": gid, "label": glabel, "tags": items})
        return {"groups": groups}

    def facet_nodes(self, group: str, tag: str, limit: int = 30) -> dict:
        cache_key = (group, tag, limit)
        if cache_key in self._facet_cache:
            return self._facet_cache[cache_key]
        level_order = {"N5": 5, "N4": 4, "N3": 3, "N2": 2, "N1": 1}
        if group == "level" and tag.startswith("N"):
            rows = self.g.execute(
                "select id from grammar where level=? limit 400", (tag,)
            ).fetchall()
        else:
            rows = self.g.execute(
                "select id from grammar where tags like ? limit 400", (f'%"{tag}"%',)
            ).fetchall()
        entries = []
        for row in rows:
            entry = self.grammar_entry(row["id"])
            if entry is None:
                continue
            entries.append(entry)

        def rank(e: dict):
            tags = e.get("tags") or []
            star = min([int(t[1]) for t in tags if re.fullmatch(r"★[1-5]", str(t))] or [9])
            return (
                0 if e.get("nuance_zh") or e.get("nuance") else 1,
                0 if e.get("level") else 1,
                level_order.get(e.get("level") or "", 9),
                star,
                len(e.get("pattern") or ""),
            )

        entries.sort(key=rank)
        picked = entries[:limit]
        result = {
            "ok": True,
            "group": group,
            "tag": tag,
            "total": len(entries),
            "nodes": [self.node_compact("grammar", e) for e in picked],
        }
        self._facet_cache[cache_key] = result
        return result

    # -- 節點摘要 --------------------------------------------
    def node_compact(self, kind: str, data) -> dict:
        if kind == "grammar":
            e = data
            tags = [t for t in (e.get("tags") or []) if t in TAG_LABEL][:3]
            sub_parts = [e.get("level") or "", (e.get("meaning_en") or "")[:36]]
            return {
                "key": f"grammar:{e['id']}", "kind": "grammar", "id": e["id"],
                "title": e.get("pattern") or e["id"],
                "sub": " · ".join([p for p in sub_parts if p]),
                "badges": tags,
            }
        if kind == "word":
            w = data
            jlpt = w.get("jlpt") or ""
            pos = (w.get("pos") or "").split(",")[0]
            return {
                "key": f"word:{w['id']}", "kind": "word", "id": str(w["id"]),
                "title": w.get("surface") or "",
                "sub": " · ".join([p for p in [w.get("reading"), jlpt, POS_ZH.get(pos, pos)] if p]),
                "badges": [b for b in [jlpt] if b],
            }
        if kind == "sent":
            s = data
            return {
                "key": f"sent:{s['id']}", "kind": "sent", "id": s["id"],
                "title": clip(s.get("ja") or "", 60),
                "sub": clip(s.get("zh") or "", 40),
                "badges": [],
            }
        if kind == "gex":
            s = data
            return {
                "key": f"gex:{s['id']}", "kind": "gex", "id": s["id"],
                "title": clip(strip_b(s.get("ja") or ""), 60),
                "sub": clip(s.get("pattern") or "", 40),
                "badges": [],
            }
        raise ValueError(f"unknown kind {kind}")

    # -- 關聯 ------------------------------------------------
    def grammar_siblings(self, entry: dict, limit_keys: int = 5, limit_tags: int = 6) -> tuple[list, list]:
        eid = entry["id"]
        by_key: list[dict] = []
        seen = {eid}
        keys = [k for k in (entry.get("keys") or []) if len(k) >= 2][:4]
        for key in keys:
            rows = self.g.execute(
                "select distinct id from keys where key=? limit 12", (key,)
            ).fetchall()
            for row in rows:
                ident = row["id"]
                if ident in seen:
                    continue
                other = self.grammar_entry(ident)
                if other is None:
                    continue
                seen.add(ident)
                by_key.append(other)
                if len(by_key) >= limit_keys:
                    break
            if len(by_key) >= limit_keys:
                break

        by_tag: list[dict] = []
        tags = [t for t in (entry.get("tags") or []) if t in FUNCTION_TAGS][:2]
        level = entry.get("level")
        patterns = {str(o.get("pattern") or "") for o in by_key}
        for tag in tags:
            rows = self.g.execute(
                "select id from grammar where tags like ? and level=? limit 10"
                if level else "select id from grammar where tags like ? limit 10",
                (f'%"{tag}"%', level) if level else (f'%"{tag}"%',),
            ).fetchall()
            for row in rows:
                ident = row["id"]
                if ident in seen:
                    continue
                other = self.grammar_entry(ident)
                if other is None:
                    continue
                pattern = str(other.get("pattern") or "")
                if pattern in patterns:
                    continue
                patterns.add(pattern)
                seen.add(ident)
                by_tag.append(other)
                if len(by_tag) >= limit_tags:
                    break
            if len(by_tag) >= limit_tags:
                break
        return by_key, by_tag

    def grammar_words(self, entry: dict, limit: int = 8) -> list[dict]:
        out: list[dict] = []
        seen = set()
        for key in (entry.get("keys") or []):
            if len(key) < 2 or len(key) > 12:
                continue
            w = self.word_by_surface(key)
            if w and w["id"] not in seen:
                seen.add(w["id"])
                out.append(w)
            if len(out) >= limit:
                return out
        for ex in (entry.get("examples") or [])[:2]:
            for w in self.content_words(strip_b(ex.get("ja") or ""), limit=4, strict=True):
                if w["id"] not in seen:
                    seen.add(w["id"])
                    out.append(w)
                if len(out) >= limit:
                    return out
        return out

    def content_words(self, sentence: str, limit: int = 8, strict: bool = False) -> list[dict]:
        out: list[dict] = []
        seen = set()
        for token in self.tokens(sentence):
            pos = token.get("pos") or ("",)
            head = pos[0] if pos else ""
            if head not in ("名詞", "動詞", "形容詞", "形状詞", "副詞"):
                continue
            if len(pos) > 1 and pos[1] in ("代名詞", "数詞"):
                continue
            base = (token.get("dict_form") or token.get("surface") or "").strip()
            if len(base) < 2 or not is_cjk(base) or base in STOPWORDS:
                continue
            w = self.word_by_surface(base)
            if w is None or w["id"] in seen:
                continue
            if strict and not (w.get("jlpt") or (w.get("zipf") or 0) >= 4.0):
                continue
            seen.add(w["id"])
            out.append(w)
            if len(out) >= limit:
                break
        return out

    def wordnet(self, word: dict, per_rel: int = 4, total: int = 10) -> list[tuple[str, dict]]:
        surface = word.get("surface") or ""
        reading = word.get("reading") or ""
        synsets = [row["synset"] for row in self.d.execute(
            "select distinct synset from wn_word where surface=? limit 6", (surface,)
        ).fetchall()]
        if not synsets and reading:
            synsets = [row["synset"] for row in self.d.execute(
                "select distinct synset from wn_word where reading=? limit 6", (reading,)
            ).fetchall()]
        out: list[tuple[str, dict]] = []
        seen = {word["id"]}
        for sid in synsets:
            rels = self.d.execute(
                "select rel, dst from wn_rel where src=? and rel in "
                "('eq_synonym','antonym','similar','hypernym','hyponym','also',"
                "'instance_hypernym','instance_hyponym') limit 40",
                (sid,),
            ).fetchall()
            per: dict[str, int] = {}
            for rel in rels:
                kind = rel["rel"]
                if per.get(kind, 0) >= per_rel:
                    continue
                words = self.d.execute(
                    "select distinct surface, reading from wn_word where synset=? limit 3", (rel["dst"],)
                ).fetchall()
                for other in words:
                    if other["surface"] == surface:
                        continue
                    w = self.word_by_surface(other["surface"], other["reading"])
                    if w is None or w["id"] in seen:
                        continue
                    seen.add(w["id"])
                    per[kind] = per.get(kind, 0) + 1
                    out.append((REL_LABEL.get(kind, kind), w))
                    if len(out) >= total:
                        return out
                    break
        return out

    def sentences_of(self, word: dict, limit: int = 8) -> list[dict]:
        surface = word.get("surface") or ""
        if not surface:
            return []
        rows = self.d.execute(
            "select id, ja, zh from sentences where ja like ? order by (zh is null or zh=''), length(ja) limit 40",
            (f"%{surface}%",),
        ).fetchall()
        out, seen = [], set()
        for row in rows:
            text = row["ja"]
            if text in seen:
                continue
            seen.add(text)
            out.append({"id": row["id"], "ja": text, "zh": row["zh"] or ""})
            if len(out) >= limit:
                break
        return out


# ---------------------------------------------------------------------------
# 各節點的詳細資料與鄰居
# ---------------------------------------------------------------------------


def build_word(data: Data, ident: str) -> dict:
    word = data.word_by_id(ident)
    if word is None:
        raise ValueError(f"找不到單詞 {ident}")
    surface = word.get("surface") or ""
    reading = word.get("reading") or ""
    pos = (word.get("pos") or "").split(",")[0]
    try:
        examples = json.loads(word.get("examples") or "[]")
    except Exception:
        examples = []

    groups: list[dict] = []

    grammar: list[dict] = []
    seen = set()
    for key in {surface, reading, norm_key(surface)}:
        if not key:
            continue
        for row in data.g.execute("select distinct id from keys where key=? limit 8", (key,)).fetchall():
            if row["id"] in seen:
                continue
            entry = data.grammar_entry(row["id"])
            if entry is None:
                continue
            seen.add(row["id"])
            grammar.append(entry)
    if surface:
        for row in data.g.execute(
            "select id from grammar where pattern like ? order by (level is null), length(pattern) limit 10",
            (f"%{surface}%",),
        ).fetchall():
            if row["id"] in seen:
                continue
            entry = data.grammar_entry(row["id"])
            if entry is None:
                continue
            seen.add(row["id"])
            grammar.append(entry)
        for sentence in data.sentences_of(word, 3):
            for hit in data.match(sentence["ja"], max_hits=2):
                eid = hit.get("id")
                if eid in seen:
                    continue
                seen.add(eid)
                grammar.append(hit)
    grammar = grammar[:12]
    if grammar:
        groups.append({"rel": "grammar", "label": "相關語法", "items": [data.node_compact("grammar", e) for e in grammar]})

    sentences = data.sentences_of(word, 8)
    if sentences:
        groups.append({"rel": "sentence", "label": "例句", "items": [data.node_compact("sent", s) for s in sentences]})

    related = data.wordnet(word, per_rel=3, total=10)
    if related:
        buckets: dict[str, list] = {}
        for label, w in related:
            buckets.setdefault(label, []).append(data.node_compact("word", w))
        for label, items in buckets.items():
            groups.append({"rel": "word", "label": label, "items": items})

    dict_sentences = []
    for idx, ex in enumerate(examples[:2]):
        ja = (ex.get("ja") if isinstance(ex, dict) else str(ex)) or ""
        if ja:
            dict_sentences.append({
                "id": f"dict:{ident}:{idx}", "ja": ja, "zh": "",
                "pattern": "辭書例句",
            })
    if dict_sentences:
        groups.append({
            "rel": "gex", "label": "辭書例句",
            "items": [data.node_compact("gex", s) for s in dict_sentences],
        })

    meta = [
        ("讀音", reading),
        ("詞性", POS_ZH.get(pos, pos)),
        ("JLPT", word.get("jlpt") or "—"),
        ("音調", str(word.get("pitch") or "—")),
        ("頻度", str(word.get("zipf") or word.get("freq") or "—")),
        ("常用", "是" if word.get("common") else "—"),
    ]
    sections = []
    if word.get("gloss_zh"):
        sections.append({"label": "釋義", "text": clip(word["gloss_zh"], 600)})
    if word.get("gloss_en"):
        sections.append({"label": "English", "text": clip(word["gloss_en"], 400)})
    return {
        "ok": True,
        "node": {
            "key": f"word:{ident}", "kind": "word", "id": str(ident),
            "title": surface, "reading": reading,
            "sub": " · ".join([p for p in [reading, word.get("jlpt") or "", POS_ZH.get(pos, pos)] if p]),
            "badges": [b for b in [word.get("jlpt") or "", "單詞"] if b],
            "meta": [{"label": k, "value": v} for k, v in meta if v],
            "sections": sections,
        },
        "groups": groups,
    }


def build_grammar(data: Data, ident: str) -> dict:
    entry = data.grammar_entry(ident)
    if entry is None:
        raise ValueError(f"找不到語法 {ident}")
    groups: list[dict] = []

    examples = []
    for ex in (entry.get("examples") or [])[:4]:
        ja = strip_b(ex.get("ja") or "")
        if not ja:
            continue
        examples.append({
            "id": f"{ident}#{len(examples) + 1}", "ja": ja,
            "jaMarked": ex.get("ja") or ja,
            "zh": ex.get("zh") or "", "en": ex.get("en") or "",
            "pattern": entry.get("pattern") or "",
        })
    for row in data.g.execute("select ja, en from examples where id=? limit 8", (ident,)).fetchall():
        ja = strip_b(row["ja"])
        if not ja or any(strip_b(e["ja"]) == ja for e in examples):
            continue
        examples.append({
            "id": f"{ident}#{len(examples) + 1}", "ja": ja,
            "jaMarked": row["ja"], "zh": "", "en": row["en"] or "",
            "pattern": entry.get("pattern") or "",
        })
        if len(examples) >= 4:
            break
    if examples:
        groups.append({"rel": "example", "label": "例句", "items": [data.node_compact("gex", e) for e in examples]})

    by_key, by_tag = data.grammar_siblings(entry)
    if by_key:
        groups.append({"rel": "similar", "label": "同檢索鍵（近義・對照）", "items": [data.node_compact("grammar", e) for e in by_key]})
    if by_tag:
        groups.append({"rel": "tag", "label": "同功能標籤", "items": [data.node_compact("grammar", e) for e in by_tag]})

    words = data.grammar_words(entry, limit=8)
    if words:
        groups.append({"rel": "word", "label": "構成詞", "items": [data.node_compact("word", w) for w in words]})

    tags = [k for k in (entry.get("tags") or []) if k in TAG_LABEL]
    meta = [
        ("接續", entry.get("formation") or ""),
        ("級別", entry.get("level") or "—"),
        ("標籤", "・".join(TAG_LABEL.get(t, t) for t in tags)),
        ("來源", "・".join(entry.get("sources") or [])),
    ]
    sections = []
    if entry.get("meaning_en"):
        sections.append({"label": "英文釋義", "text": clip(entry["meaning_en"], 400)})
    nuance = entry.get("nuance_zh") or entry.get("nuance") or ""
    if nuance:
        sections.append({"label": "語感・用法", "text": clip(nuance, MAX_TEXT)})
    return {
        "ok": True,
        "node": {
            "key": f"grammar:{ident}", "kind": "grammar", "id": ident,
            "title": entry.get("pattern") or ident,
            "reading": entry.get("reading") or "",
            "sub": " · ".join([p for p in [entry.get("level") or "", (entry.get("meaning_en") or "")[:40]] if p]),
            "badges": [b for b in [entry.get("level") or "", "語法"] if b],
            "meta": [{"label": k, "value": v} for k, v in meta if v],
            "sections": sections,
        },
        "groups": groups,
    }


def build_sent(data: Data, ident: str) -> dict:
    row = data.d.execute("select id, ja, zh from sentences where id=?", (ident,)).fetchone()
    if row is None:
        raise ValueError(f"找不到例句 {ident}")
    ja, zh = row["ja"], row["zh"] or ""
    groups: list[dict] = []

    hits = data.match(ja, max_hits=4)
    if hits:
        groups.append({"rel": "grammar", "label": "句中語法", "items": [data.node_compact("grammar", h) for h in hits]})

    words = data.content_words(ja, limit=10)
    if words:
        groups.append({"rel": "word", "label": "句中單詞", "items": [data.node_compact("word", w) for w in words]})

    siblings = []
    if len(words) >= 2:
        a, b = words[0]["surface"], words[1]["surface"]
        seen = {ja}
        for other in data.d.execute(
            "select id, ja, zh from sentences where ja like ? and ja like ? and length(ja) < 60 limit 8",
            (f"%{a}%", f"%{b}%"),
        ).fetchall():
            if other["ja"] in seen:
                continue
            seen.add(other["ja"])
            siblings.append({"id": other["id"], "ja": other["ja"], "zh": other["zh"] or ""})
            if len(siblings) >= 4:
                break
    if siblings:
        groups.append({"rel": "sentence", "label": "共用詞的例句", "items": [data.node_compact("sent", s) for s in siblings]})

    return {
        "ok": True,
        "node": {
            "key": f"sent:{ident}", "kind": "sent", "id": ident,
            "title": clip(ja, 120), "reading": "",
            "sub": clip(zh, 60),
            "badges": ["例句"],
            "meta": [{"label": "來源", "value": ("Tatoeba" if ident.startswith("tatoeba") else ident)}],
            "sections": [{"label": "中文", "text": zh}] if zh else [],
        },
        "groups": groups,
    }


def build_gex(data: Data, ident: str) -> dict:
    # id 形狀：<grammarId>#<n> 或 dict:<wordId>
    if ident.startswith("dict:"):
        parts = ident.split(":")
        word = data.word_by_id(parts[1])
        idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        if word is None:
            raise ValueError(f"找不到例句 {ident}")
        try:
            examples = json.loads(word.get("examples") or "[]")
        except Exception:
            examples = []
        picked = examples[idx] if idx < len(examples) else (examples[0] if examples else None)
        ja = (picked.get("ja") if isinstance(picked, dict) else "") or ""
        groups = []
        words = data.content_words(ja, limit=8)
        if words:
            groups.append({"rel": "word", "label": "句中單詞", "items": [data.node_compact("word", w) for w in words]})
        hits = data.match(ja, max_hits=3)
        if hits:
            groups.append({"rel": "grammar", "label": "句中語法", "items": [data.node_compact("grammar", h) for h in hits]})
        return {
            "ok": True,
            "node": {
                "key": f"gex:{ident}", "kind": "gex", "id": ident, "title": clip(ja, 120),
                "sub": "辭書例句", "badges": ["例句"],
                "meta": [{"label": "出處", "value": f"辭書・{word.get('surface') or ''}"}],
                "sections": [],
            },
            "groups": groups,
        }

    gid = ident.split("#", 1)[0]
    entry = data.grammar_entry(gid)
    if entry is None:
        raise ValueError(f"找不到例句 {ident}")
    ja, zh, en, marked = "", "", "", ""
    for ex in (entry.get("examples") or []):
        if ex.get("ja"):
            ja, marked = strip_b(ex["ja"]), ex["ja"]
            zh, en = ex.get("zh") or "", ex.get("en") or ""
            break
    if not ja:
        row = data.g.execute("select ja, en from examples where id=? limit 1", (gid,)).fetchone()
        if row:
            ja, marked, en = strip_b(row["ja"]), row["ja"], row["en"] or ""
    groups = [{"rel": "grammar", "label": "出處語法", "items": [data.node_compact("grammar", entry)]}]
    words = data.content_words(ja, limit=8)
    if words:
        groups.append({"rel": "word", "label": "句中單詞", "items": [data.node_compact("word", w) for w in words]})
    return {
        "ok": True,
        "node": {
            "key": f"gex:{ident}", "kind": "gex", "id": ident, "title": clip(ja, 120),
            "sub": entry.get("pattern") or "", "badges": ["例句"],
            "meta": [{"label": "出處語法", "value": entry.get("pattern") or ""}],
            "sections": ([{"label": "中文", "text": zh}] if zh else []) + ([{"label": "English", "text": en}] if en else []),
        },
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# 搜尋
# ---------------------------------------------------------------------------


def search(data: Data, query: str, limit: int = 24) -> dict:
    q = norm_key(query)
    if not q:
        return {"ok": True, "results": []}
    results: list[dict] = []
    seen = set()

    def rank_of(kind: str, obj) -> tuple:
        if kind == "word":
            jlpt = obj.get("jlpt") or ""
            return (0 if jlpt else 1, jlpt or "ZZ", -(obj.get("zipf") or 0))
        if kind == "grammar":
            level = obj.get("level") or ""
            return (0 if level else 1, level or "ZZ", 0)
        return (0, "", 0)

    def push(kind: str, data_obj, score: float):
        node = data.node_compact(kind, data_obj)
        if node["key"] in seen:
            return
        seen.add(node["key"])
        results.append({**node, "score": score, "_rank": rank_of(kind, data_obj)})

    def collect(qq: str, penalty: float) -> None:
        for row in data.d.execute(
            "select * from words where surface=? or reading=? order by jlpt is null, jlpt limit 5", (qq, qq)
        ).fetchall():
            push("word", dict(row), 100 - penalty)

        for row in data.g.execute("select id from grammar where pattern=? limit 5", (qq,)).fetchall():
            entry = data.grammar_entry(row["id"])
            if entry:
                push("grammar", entry, 95 - penalty)

        for row in data.g.execute("select distinct id from keys where key=? limit 6", (qq,)).fetchall():
            entry = data.grammar_entry(row["id"])
            if entry:
                push("grammar", entry, 90 - penalty)

        try:
            for row in data.d.execute(
                "select * from words_fts where words_fts match ? order by rank limit 12",
                (f'"{qq}"' if " " in qq else qq,),
            ).fetchall():
                surface = row["surface"]
                w = data.word_by_surface(surface)
                if w:
                    push("word", w, 70 - penalty)
        except Exception:
            pass

        if is_cjk(qq):
            for row in data.d.execute(
                "select * from words where surface like ? order by jlpt is null, jlpt, zipf desc limit 8",
                (f"%{qq}%",),
            ).fetchall():
                push("word", dict(row), 60 - penalty)

        try:
            for row in data.g.execute(
                "select g.id from grammar_fts f join grammar g on g.rowid=f.rowid where grammar_fts match ? limit 10",
                (f'"{qq}"' if " " in qq else qq,),
            ).fetchall():
                entry = data.grammar_entry(row["id"])
                if entry:
                    push("grammar", entry, 55 - penalty)
        except Exception:
            pass

        for row in data.d.execute(
            "select id, ja, zh from sentences where ja like ? order by (zh is null or zh=''), length(ja) limit 6",
            (f"%{qq}%",),
        ).fetchall():
            push("sent", {"id": row["id"], "ja": row["ja"], "zh": row["zh"] or ""}, 40 - penalty)

    collect(q, 0)
    simplified = t2s(q)
    if simplified != q:
        collect(simplified, 3)

    results.sort(key=lambda r: (-r["score"], r["_rank"]))
    for item in results:
        item.pop("_rank", None)
    return {"ok": True, "query": query, "results": results[:limit]}


# ---------------------------------------------------------------------------
# 首頁：先給一小簇「N5 教材語法」，讓面板一打開就有東西看
# ---------------------------------------------------------------------------


def home(data: Data) -> dict:
    """首頁不是一堆散點，而是一個真實的鄰域：一條語法 + 它全部的關聯。

    種子挑教材裡例句、檢索鍵齊全的條目，這樣一打開就能看到「語法 → 例句／
    相近語法／構成詞」三種連線，而不是一片同標籤的蜘蛛網。
    """
    seeds = ["jk:ga-kedo-although", "jk:te-shimau", "ag:1725102351139"]
    entry = None
    for ident in seeds:
        candidate = data.grammar_entry(ident)
        if candidate and candidate.get("examples"):
            entry = candidate
            break
    if entry is None:
        row = data.g.execute(
            "select id from grammar where level is not null and tags like '%\"textbook\"%' limit 1"
        ).fetchone()
        entry = data.grammar_entry(row["id"]) if row else None
    if entry is None:
        return {"ok": True, "nodes": [], "edges": [], "hint": ""}

    detail = build_grammar(data, entry["id"])
    root = data.node_compact("grammar", entry)
    nodes = [root]
    edges = []
    seen = {root["key"]}
    for group in detail["groups"]:
        for item in group["items"][:5]:
            if item["key"] in seen:
                edges.append({
                    "source": root["key"], "target": item["key"],
                    "rel": group["rel"], "label": group["label"],
                })
                continue
            seen.add(item["key"])
            nodes.append(item)
            edges.append({
                "source": root["key"], "target": item["key"],
                "rel": group["rel"], "label": group["label"],
            })
    return {
        "ok": True,
        "nodes": nodes,
        "edges": edges,
        "hint": f"從「{root['title']}」出發：點右邊的關聯把它接進來，或搜尋任何單詞／語法／例句。",
    }


# ---------------------------------------------------------------------------
# 分派與 worker
# ---------------------------------------------------------------------------


class Engine:
    def __init__(self) -> None:
        self.data = Data()

    def handle(self, request: dict) -> dict:
        op = (request or {}).get("op") or ""
        data = self.data
        try:
            if op == "hello":
                return {
                    "ok": True,
                    "version": 1,
                    "counts": {
                        "grammar": data.g.execute("select count(*) from grammar").fetchone()[0],
                        "words": data.d.execute("select count(*) from words").fetchone()[0],
                        "sentences": data.d.execute("select count(*) from sentences").fetchone()[0],
                        "examples": data.g.execute("select count(*) from examples").fetchone()[0],
                    },
                }
            if op == "home":
                return home(data)
            if op == "search":
                return search(data, str(request.get("q") or ""), int(request.get("limit") or 24))
            if op == "facets":
                return {"ok": True, **data.facets()}
            if op == "facet":
                return data.facet_nodes(
                    str(request.get("group") or "function"),
                    str(request.get("tag") or ""),
                    int(request.get("limit") or 24),
                )
            if op == "node":
                key = str(request.get("key") or request.get("id") or "")
                if ":" not in key:
                    raise ValueError(f"節點格式應為 kind:id，收到 {key!r}")
                kind, ident = key.split(":", 1)
                if kind == "word":
                    return build_word(data, ident)
                if kind == "grammar":
                    return build_grammar(data, ident)
                if kind == "sent":
                    return build_sent(data, ident)
                if kind == "gex":
                    return build_gex(data, ident)
                raise ValueError(f"未知節點類型 {kind}")
            return {"ok": False, "error": f"未知操作 {op!r}"}
        except Exception as exc:  # 回給外掛顯示，不讓 worker 崩掉
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def serve() -> int:
    engine = Engine()
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except Exception as exc:
            out.write(json.dumps({"ok": False, "error": f"bad json: {exc}"}, ensure_ascii=False) + "\n")
            out.flush()
            continue
        if isinstance(request, dict) and request.get("op") == "shutdown":
            break
        response = engine.handle(request if isinstance(request, dict) else {})
        out.write(json.dumps(response, ensure_ascii=False) + "\n")
        out.flush()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="日語學習知識圖譜引擎")
    parser.add_argument("--serve", action="store_true", help="常駐 worker：stdin/stdout 逐行 JSON")
    parser.add_argument("--request", help="單次請求 JSON")
    parser.add_argument("--search", help="搜尋單詞／語法／例句")
    parser.add_argument("--node", help="節點，如 word:12345 / grammar:ag:xxx / sent:tatoeba-1-2")
    parser.add_argument("--facets", action="store_true", help="列出分類面向")
    parser.add_argument("--home", action="store_true", help="首頁節點")
    args = parser.parse_args()

    if args.serve:
        return serve()

    engine = Engine()
    if args.request:
        request = json.loads(args.request)
    elif args.search:
        request = {"op": "search", "q": args.search}
    elif args.node:
        request = {"op": "node", "key": args.node}
    elif args.facets:
        request = {"op": "facets"}
    elif args.home:
        request = {"op": "home"}
    else:
        parser.print_help()
        return 2
    print(json.dumps(engine.handle(request), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
