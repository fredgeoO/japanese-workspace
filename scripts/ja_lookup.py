# -*- coding: utf-8 -*-
"""ja_lookup —— 本地日语语法 + 词典检索库（供问答 / RAG 注入 Prompt 用）

用法（命令行）：
    python scripts/ja_lookup.py "協力するかしないかは別として、まずは試そう"
    python scripts/ja_lookup.py --dict 徹夜 わけ        # 词典：释义/音调/频度/语域/关联词
    python scripts/ja_lookup.py --grammar だけは         # 语法条目
    python scripts/ja_lookup.py --conj 聞く              # 活用表（10 形态）
    python scripts/ja_lookup.py --kanji 検              # 汉字（音训/部首/笔画）
    python scripts/ja_lookup.py --syn 検討               # WordNet 辨析（近义/上位/对义）
    python scripts/ja_lookup.py --sent 言葉              # Tatoeba 日中真实例句
    python scripts/ja_lookup.py --json "〜てしまう"

用法（Python）：
    from ja_lookup import GrammarDB, DictDB, ConjDB, KanjiDB, WordNetDB
    g = GrammarDB(); d = DictDB()
    hits = g.match("協力するかしないかは別として")   # 命中的语法点（含讲解+例句）
    rows = d.lookup("徹夜")                          # 词典条目（中文释义+例句）
    rows = ConjDB().lookup("聞く")                   # 活用形态
    rows = WordNetDB().explain("検討")               # 义项/近义/上位/对义

语法匹配策略（token 级对齐，不做子串包含）：
  1. 句子归一化（NFKC、去空白）+ SudachiPy 分词，tokens 同时保留
     surface / 平假名读音 / 词典形 / 词性；
  2. 检索键（key）在加载期做合法性过滤（_entry_ok：裸虚词、碎片 key、
     含 ASCII/全角波浪号等一律不进索引）；
  3. 命中判定 _find()，两支都要求「连续整 token 精确拼接」：
     - 分支 1（所有 key）：连续 token 的 surface 或平假名读音拼接 == key；
     - 分支 2（仅含汉字 key）：汉字锚点段 == 某 token 的 surface/词典形/读音，
       假名尾串紧随其后（间隔 ≤6 token、不跨句读点）由连续 token 精确拼接；
       key 的汉字段经词典库展开成假名变体（_reading_cache），
       使假名书写的句子（わけではない）也能命中汉字 key（訳ではない）；
  4. 命中多条时按「命中 key 更长 → 等级更高」排序，去包含冗余
     （同长 key 按 rich 优先），出口只保留 rich 条目（有讲解或例句）。

调试：设环境变量 JA_LOOKUP_DEBUG=1，命中详情打到 stderr。
"""
import json, os, re, sqlite3, sys, unicodedata, pathlib

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent.parent
GRAMMAR_JSON = ROOT / "data" / "grammar.json"
GRAMMAR_DB = ROOT / "data" / "ja_grammar.sqlite"
DICT_DB = ROOT / "data" / "ja_dict.sqlite"

LEVEL_ORDER = {"N5": 5, "N4": 4, "N3": 3, "N2": 2, "N1": 1}
DEBUG = bool(os.environ.get("JA_LOOKUP_DEBUG"))

SENT_PUNCT = set("。！？、…ー〜～")  # 尾串不得跨越的句读/长音边界（、也断开，防跨词粘连）

def norm(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))

def is_kana(ch):
    return "぀" <= ch <= "ゟ" or "゠" <= ch <= "ヿ"

def is_kanji(ch):
    return "\u4e00" <= ch <= "\u9fff"

def _hira(s):
    """片假名 -> 平假名（sudachi reading_form 返回片假名）。"""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)

def _rich(e):
    return bool(e.get("nuance") or e.get("examples") or e.get("formation"))

# ---------------- 语法库 ----------------
class GrammarDB:
    def __init__(self):
        entries = json.loads(GRAMMAR_JSON.read_text("utf-8"))
        self.entries = entries
        # 预编译：每条 -> 合法 keys（加载期一次性过滤，match 期不再判）
        # key 归一化：NFKC + 剥前缀波浪号（token 流里没有 〜，带它永不命中）
        self.index = []          # [(key, has_kanji, entry)]
        self._entry_ok_cache = {}
        for e in entries:
            if not self._entry_ok(e):
                continue
            for k in e.get("keys", []):
                k = norm(k).lstrip("〜~～")
                if len(k) >= 2 and self._key_valid(k):
                    self.index.append((k, any(is_kanji(c) for c in k), e))
        # 汉字段 -> 假名读音变体（来自词典库，懒加载）
        self._reading_cache = {}
        self._dict = None

    # ---- 加载期闸门 ----
    # 裸虚词：条目 keys 里出现这些短 key 时，该条目整体不参与匹配
    _BARE = {"する", "たり", "ない", "こと", "もの", "ため", "ところ", "はず",
             "わけ", "のち", "ほど", "だけ", "しか", "まで", "から", "ので",
             "のに", "ながら", "つつ", "きり", "さえ", "かけ", "かける", "なる",
             "できる", "ある", "いる", "まい"}

    # 短 key 白名单：虽短但是句型本体，允许参与匹配
    _SHORT_OK = {"ずに", "ずつ", "ずと", "つつ", "きり", "さえ", "まま",
                 "ども", "なり", "や否や", "ぬき", "なし", "わけ", "はず",
                 "まい", "べし", "まじ"}

    # 高频碎片 key：由常见 token（し/ない/か…）拼成、几乎必然误报的短组合。
    # 这些组合若真是句型，必有更长的完整 key（如 しないではない），
    # 裸碎片一律不进索引。
    _FRAG = {"しない", "しないか", "ないか", "かは", "だけは", "としては",
             "として", "ないの", "のか", "のだ", "んだ", "ので", "のに",
             "ないで", "なくて", "ないが", "ないし", "ないなら"}

    @staticmethod
    def _key_valid(key):
        """检索键合法性：必须含假名（排除 Vない / Verb-ない 这类模式描述碎片），
        不能含 ASCII，不能是裸 ない 或已知碎片。
        内部 〜 是槽位标记（〜か〜ないかのうちに 类），不是残渣，放行。"""
        if not re.search(r"[\u3041-\u30ff]", key):
            return False
        if re.search(r"[A-Za-z]", key):
            return False
        if re.fullmatch(r"〜?ない", key):
            return False
        if key in GrammarDB._FRAG:
            return False
        return True

    @staticmethod
    def _entry_ok(e):
        """条目级过滤（逐 key 制）：非法/裸虚词/碎片 key 从该条目剔除，
        只要还剩一个合法 key，条目就保留参与匹配。
        （旧版是"任一 key 非法就整条出局"，实测误杀 417 条带讲解条目，
        如 〜ほかない 仅因 keys 里混有裸「ない」被整条排除。）
        教材源（sources 含 anki_besshiki）的 keys 由 extract_keys 从 pattern
        生成、可信度高，短纯假名 key 不再按 _BARE/_SHORT_OK 名单杀。"""
        textbook = "anki_besshiki" in (e.get("sources") or [])
        kept = 0
        for k in e.get("keys", []):
            k = norm(k).lstrip("〜~～")
            if not GrammarDB._key_valid(k):
                continue
            if k in GrammarDB._BARE:
                continue
            if len(k) <= 2 and not any(is_kanji(c) for c in k) \
                    and k not in GrammarDB._SHORT_OK and not textbook:
                continue
            kept += 1
        return kept > 0

    def _entry_keys(self, e):
        """条目在索引里的合法 keys（与 index 同一套过滤），带 has_kanji 标记。"""
        textbook = "anki_besshiki" in (e.get("sources") or [])
        out = []
        for k in e.get("keys", []):
            k = norm(k).lstrip("〜~～")
            if len(k) >= 2 and self._key_valid(k) and k not in GrammarDB._BARE \
                    and (textbook or len(k) > 2 or any(is_kanji(c) for c in k)
                         or k in GrammarDB._SHORT_OK):
                out.append((k, any(is_kanji(c) for c in k)))
        return out

    def _entry_keys(self, e):
        """条目在索引里的合法 keys（与 index 同一套过滤），带 has_kanji 标记。"""
        textbook = "anki_besshiki" in (e.get("sources") or [])
        out = []
        for k in e.get("keys", []):
            k = norm(k).lstrip("〜~～")
            if len(k) >= 2 and self._key_valid(k) and k not in GrammarDB._BARE \
                    and (textbook or len(k) > 2 or any(is_kanji(c) for c in k)
                         or k in GrammarDB._SHORT_OK):
                out.append((k, any(is_kanji(c) for c in k)))
        return out

    # ---- 汉字段 -> 假名读音展开（词典库懒加载） ----
    def _dict_db(self):
        if self._dict is None:
            try:
                self._dict = DictDB()
            except Exception:
                self._dict = False
        return self._dict

    def _kanji_readings(self, kanji_seg):
        """汉字段（如 訳 / 別）经词典展开成平假名读音集合（わけ / べつ）。"""
        if kanji_seg in self._reading_cache:
            return self._reading_cache[kanji_seg]
        out = set()
        d = self._dict_db()
        if d:
            try:
                for r in d.lookup(kanji_seg, limit=8):
                    rd = norm(_hira(r.get("reading") or ""))
                    if rd and all(is_kana(c) for c in rd):
                        out.add(rd)
            except Exception:
                pass
        self._reading_cache[kanji_seg] = out
        return out

    def _key_variants(self, key):
        """含汉字 key 的匹配变体：原 key + 汉字段替换为词典假名读音。
        訳ではない -> [訳ではない, わけではない]。"""
        if not any(is_kanji(c) for c in key):
            return [key]
        # 按「汉字段 / 非汉字段」切分，如 訳|ではない
        segs = re.findall(r"[\u4e00-\u9fff]+|[^\u4e00-\u9fff]+", key)
        variants = [key]
        for i, seg in enumerate(segs):
            if not is_kanji(seg[0]):
                continue
            for rd in self._kanji_readings(seg):
                v = "".join(rd if j == i else s for j, s in enumerate(segs))
                variants.append(v)
        return variants

    # ---- 命中判定：唯一实现，返回 (start, end) token 窗口或 None ----
    def _find(self, key, has_kanji, s, tokens, readings, dicts, pos_list):
        """唯一命中实现，返回 (start, end) token 窗口或 None。
        key 先按内部 〜 槽位切段（〜か〜ないかのうちに -> [か, ないかのうちに]），
        每段独立做读音展开后按序对齐：
        分支 1：连续整 token（surface 或平假名读音）精确拼接 == 段；
        分支 2（仅含汉字段）：汉字锚点整 token 对齐 + 假名尾串紧随精确拼接；
        段间 ≤6 token、不跨句读点。tokens is None 时退化为归一化字符串子串。"""
        segs = [seg for seg in re.split(r"[〜~～]+", key) if seg]
        if not segs:
            return None
        # 槽位 key 的段若是纯假名功能词（も/ば/は…），整 token 匹配要求它独立成 token，
        # 但助词常黏在活用形里（なければ = ない+ければ 含 ば）。对这类段放宽为
        # 「段是某 token 的子串」，仅限 _SHORT_OK 级短段（≤2 字），防长段退化为子串包含。
        # 首段例外：句首槽位（〜も〜ば〜も 的第一个も）若允许子串，
        # 任何含 も 的句子都满足第一段，失去锚定作用——首段保持整 token 精确。
        # 例外之例外：首段若是单字纯假名（も〜ば〜も 的 も），整 token 精确会漏掉
        # 它黏在活用形里的情况（犬も歩けば…的 も 独立成 token 才命中，
        # 而 金もなければ 的 も 独立、但 食べもすれば 的 も 可能黏合）——
        # 单字首段仍要求独立 token 或读音精确，不放子串。
        seg_loose = [bool(seg) and len(seg) <= 2 and all(is_kana(c) for c in seg)
                     for seg in segs] if len(segs) > 1 else [False] * len(segs)
        if tokens is None:
            pos, span_start = 0, None
            for seg in segs:
                found = None
                for v in (self._key_variants(seg) if any(is_kanji(c) for c in seg) else [seg]):
                    i = s.find(v, pos)
                    if i >= 0 and (found is None or i < found[0]):
                        found = (i, i + len(v))
                if not found:
                    return None
                if span_start is None:
                    span_start = found[0]
                pos = found[1]
            return (span_start, pos) if span_start is not None else None
        n = len(tokens)
        # 多段槽位 key：每段独立取最左命中（段间 ≤6 token、不跨句读），
        # 不做「段 1 最左命中后段 2 必须紧随」的贪心链——
        # 否则 金もなければ時間もない 里 も(1) 会挡住 ば(3) 的可达性。
        # 段间窗口内出现句读点则拒绝该段起点（防跨句粘连）。
        def _clean_between(a, b):
            return not any(any(c in SENT_PUNCT for c in tokens[k]) for k in range(a, b))
        spans = []
        for si, seg in enumerate(segs):
            span = None
            for base in ([spans[-1]] if spans else [None]):
                span = self._find_seg(seg, s, tokens, readings, dicts, base, seg_loose[si])
                if span and (not spans or _clean_between(spans[-1][1], span[0])):
                    break
                span = None
            if span is None:
                return None
            if spans and span[0] - spans[-1][1] > 6:
                return None
            spans.append(span)
        return (spans[0][0], spans[-1][1])

    def _find_seg(self, seg, s, tokens, readings, dicts, after, loose=False):
        """单段对齐；after 为上一段命中窗口 (i,j)，本段须落在其后 ≤6 token 内。
        loose=True（多段 key 的短假名槽位段）：允许段作为单 token 的子串命中。"""
        n = len(tokens)
        lo = after[1] if after else 0
        hi = min(after[1] + 6, n) if after else n
        variants = self._key_variants(seg) if any(is_kanji(c) for c in seg) else [seg]
        for vkey in variants:
            has_kanji = any(is_kanji(c) for c in vkey)
            if loose:
                for i in range(lo, hi):
                    if vkey in tokens[i] or vkey in readings[i]:
                        return (i, i + 1)
                continue
            # 分支 1：连续整 token 精确拼接（surface 或读音）
            for src in (tokens, readings):
                for i in range(lo, hi):
                    acc = ""
                    for j in range(i, min(i + 8, n)):
                        t = src[j]
                        if not t or not vkey.startswith(acc + t):
                            break
                        acc += t
                        if acc == vkey:
                            return (i, j + 1)
            # 分支 1.5：词典形整 token 对齐（活用形句子里的整词 key）。
            # 教材源 pattern 多为辞书形（あげる/あいだ），而 Sudachi 把
            # あげました 切成 あげ/まし/た —— surface/读音永远拼不出「あげる」。
            # 仅对纯假名 key 启用，要求整 token 词典形精确相等，不做拼接，
            # 避免退化为子串包含。
            if not has_kanji:
                for i in range(lo, hi):
                    if dicts[i] == vkey:
                        return (i, i + 1)
            if not has_kanji:
                continue
            # 分支 2：汉字锚点 + 假名尾串
            m = re.match(r"^([\u4e00-\u9fff]+)(.*)$", vkey)
            if not m:
                continue
            anchor, tail = m.group(1), m.group(2)
            anchor_forms = {anchor} | self._kanji_readings(anchor)
            for i in range(lo, hi):
                hit_anchor = (tokens[i] in anchor_forms
                              or dicts[i] in anchor_forms
                              or readings[i] in anchor_forms)
                if not hit_anchor:
                    continue
                if not tail:
                    return (i, i + 1)
                for src in (tokens, readings):
                    acc = ""
                    for j in range(i + 1, min(i + 7, n)):
                        if any(c in SENT_PUNCT for c in src[j]):
                            break
                        acc += src[j]
                        if not tail.startswith(acc):
                            break
                        if acc == tail:
                            return (i, j + 1)
        return None

    def match(self, sentence, max_hits=8, tokenizer=None):
        s = norm(sentence)
        tokens = readings = dicts = pos_list = None
        if tokenizer is not None:
            m = tokenizer.tokenize(sentence)
            tokens = [norm(t.surface()) for t in m]
            readings = [norm(_hira(t.reading_form() or t.surface())) for t in m]
            dicts = [norm(t.dictionary_form() or t.surface()) for t in m]
            pos_list = [t.part_of_speech() for t in m]
        charset = set(s)
        if tokens is not None:
            charset |= set("".join(tokens)) | set("".join(readings)) | set("".join(dicts))
        hits = {}
        for key, has_kanji, e in self.index:
            # 字符集预筛：key 任一变体的字符必须都在句中出现（必要条件）；
            # 内部 〜 是槽位标记，不参与字符集判定
            if not all(c in charset for c in key if c not in "〜~～"):
                continue
            span = self._find(key, has_kanji, s, tokens, readings, dicts, pos_list)
            if span:
                if DEBUG:
                    sys.stderr.write(f"[hit] {e['pattern']} | key={key!r} | span={span}\n")
                eid = e["id"]
                if eid not in hits or len(key) > hits[eid][1]:
                    hits[eid] = (e, len(key))
        result = [e for e, _ in hits.values()]
        # 排序：命中 key 更长（更具体）优先，其次等级更高（N1>N5）
        def best_len(e):
            ks = [k for k, hk in self._entry_keys(e)
                  if self._find(k, hk, s, tokens, readings, dicts, pos_list)]
            return max((len(k) for k in ks), default=0)
        result.sort(key=lambda e: (-best_len(e), -LEVEL_ORDER.get(e.get("level") or "", 0)))
        result = self._dedup(result, s, tokens, readings, dicts, pos_list)
        # 出口质量闸门：只保留 rich 条目（有接续/语感/例句），裸条目视同未命中
        result = [e for e in result if _rich(e)]
        return result[:max_hits]

    def _entry_keys(self, e):
        """条目在索引里的合法 keys（与 index 同一套过滤），带 has_kanji 标记。"""
        out = []
        for k in e.get("keys", []):
            k = norm(k).lstrip("〜~～")
            if len(k) >= 2 and self._key_valid(k):
                out.append((k, any(is_kanji(c) for c in k)))
        return out

    def _dedup(self, result, s, tokens, readings, dicts, pos_list):
        """key 字符串包含去冗余：A 的所有命中 key 都被 B 的命中 key 字符串包含，
        且 B 更长（或等长但 B 更 rich）-> 丢 A。
        同 key 集合双 rich 条目：按 (字段完整度 desc, pattern 长度 desc, id asc)
        确定性保留一条，消掉「Verb stem + ましょう / 〜ましょう」并列重复。"""
        def hit_keys(e):
            return [k for k, hk in self._entry_keys(e)
                    if self._find(k, hk, s, tokens, readings, dicts, pos_list)]
        def completeness(e):
            return sum(1 for f in ("formation", "nuance", "examples") if e.get(f))
        def keyset(e):
            return frozenset(k for k, _ in self._entry_keys(e))
        def rank(e):
            return (-completeness(e), -len(norm(e["pattern"])), e["id"])
        out = []
        for e in result:
            drop = False
            ek = hit_keys(e)
            for other in result:
                if other is e:
                    continue
                ok = hit_keys(other)
                if not (ek and ok):
                    continue
                if all(any(x in y for y in ok) for x in ek):
                    mx_e, mx_o = max(map(len, ek)), max(map(len, ok))
                    if mx_o > mx_e or (mx_o == mx_e and _rich(other) and not _rich(e)):
                        drop = True
                        break
            if not drop and keyset(e) and any(
                    keyset(o) == keyset(e) and rank(o) < rank(e) for o in result if o is not e):
                drop = True
            if not drop:
                out.append(e)
        return out

    def get(self, pattern):
        p = norm(pattern).lstrip("〜~～")
        for e in self.entries:
            if norm(e["pattern"]).lstrip("〜~～") == p:
                return e
        return None

    def search(self, q, limit=10):
        """定向查询三层分层（输出结构解决主次，不靠 prose 指令）：
        ★ 主条目  get(Q) 精确命中（norm + lstrip 〜）
        ○ 相关    pattern 或 reading 含 Q（rich 闸门内）
        · 对比参考 仅 nuance 含 Q，上限 3 条
        三层全空时退回 get() 兜底（裸条目，带 [裸条目·无讲解] 标签）。
        返回 [(tier, entry)]，tier ∈ {"main","rel","ref","bare"}。"""
        qn = norm(q)
        main = self.get(qn)
        out = []
        if main and _rich(main):
            out.append(("main", main))
        rel, ref = [], []
        for e in self.entries:
            if not self._entry_ok(e) or not _rich(e) or (main and e["id"] == main["id"]):
                continue
            pat, rdg = norm(e["pattern"]), norm(e.get("reading") or "")
            if qn in pat or (rdg and qn in rdg):
                rel.append(e)
            elif qn in norm(e.get("nuance") or ""):
                ref.append(e)
        # 主条目缺失（get 命中的是裸条目或无精确命中）时，
        # 从相关层里提 pattern 最短的一条升格为 ★，避免全平铺无主次
        if not out and rel:
            top = min(rel, key=lambda e: (len(norm(e["pattern"])), e["id"]))
            out.append(("main", top))
            rel = [e for e in rel if e["id"] != top["id"]]
        out += [("rel", e) for e in rel[:limit]]
        out += [("ref", e) for e in ref[:3]]
        if not out:
            e = self.get(qn)
            if e:
                out = [("bare", e)]
        return out

# ---------------- 词典库 ----------------
class DictDB:
    def __init__(self):
        self.con = sqlite3.connect(DICT_DB)
        self.con.row_factory = sqlite3.Row

    def lookup(self, word, limit=5):
        rows = self.con.execute(
            "SELECT surface,reading,jlpt,pos,freq,gloss_zh,gloss_en,examples,"
            "pitch,zipf,misc,field,dialect,info,related,antonym,lang_src "
            "FROM words WHERE surface=? OR reading=? "
            "ORDER BY (jlpt IS NULL), common DESC, freq DESC LIMIT ?",
            (word, word, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["examples"] = json.loads(d["examples"] or "[]")
            for k in ("related", "antonym", "lang_src"):
                d[k] = json.loads(d[k] or "[]")
            out.append(d)
        return out

    # ---- 标签代码 → 中文说明（tags 表：jitendex 官方说明 + 标签词典）----
    def _labels(self):
        if not hasattr(self, "_lab"):
            self._lab = {r[0]: r[2] for r in self.con.execute("SELECT code, category, label FROM tags")}
        return self._lab

    def label(self, code):
        return self._labels().get(code)

    def tag_text(self, codes):
        """'col,arch' -> '口语(col) / 古语(arch)'"""
        out = []
        for c in [x for x in (codes or "").split(",") if x]:
            lab = self.label(c)
            out.append(f"{lab}({c})" if lab else c)
        return " / ".join(out)

    # ---- Tatoeba 日中句对：按子串检索真实例句（16k 句，LIKE 足够快）----
    def sentences(self, word, limit=3):
        rows = self.con.execute(
            "SELECT ja, zh FROM sentences WHERE ja LIKE ? ORDER BY length(ja) LIMIT ?",
            ("%" + word + "%", limit)).fetchall()
        return [dict(r) for r in rows]

    def search(self, q, limit=10):
        rows = self.con.execute(
            "SELECT w.surface,w.reading,w.jlpt,w.pos,w.gloss_zh,w.gloss_en "
            "FROM words_fts f JOIN words w ON w.rowid=f.rowid "
            "WHERE words_fts MATCH ? LIMIT ?", (q, limit)).fetchall()
        return [dict(r) for r in rows]

# ---------------- 活用表（P0：jkindrix conjugations） ----------------
class ConjDB:
    """动词/形容词全形态活用（data/ja_grammar.sqlite / conj 表，3,511 条）"""
    FORM_LABELS = [
        ("dictionary", "辞书形"), ("polite_nonpast", "ます形"), ("polite_past", "ました形"),
        ("polite_negative", "ません形"), ("polite_past_negative", "ませんでした"),
        ("te_form", "て形"), ("ta_form", "た形"), ("nai_form", "ない形"),
        ("nakatta_form", "なかった形"), ("attributive", "连体形"),
    ]

    def __init__(self):
        if not GRAMMAR_DB.exists():
            raise SystemExit("[Error] 缺少 data/ja_grammar.sqlite，请先跑 scripts/build_grammar.py")
        self.con = sqlite3.connect(GRAMMAR_DB)
        self.con.row_factory = sqlite3.Row

    def lookup(self, word, limit=3):
        rows = self.con.execute(
            "SELECT dict_form, reading, cls, forms, display FROM conj "
            "WHERE dict_form=? OR reading=? LIMIT ?", (word, word, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["forms"] = json.loads(d["forms"] or "{}")
            d["display"] = json.loads(d["display"] or "{}")
            out.append(d)
        return out

    def pairs(self, d):
        """按固定顺序取 (标签, 显示形)，优先汉字显示形。"""
        out = []
        for key, lab in self.FORM_LABELS:
            v = d["display"].get(key) or d["forms"].get(key)
            if v:
                out.append((lab, v))
        return out


# ---------------- 汉字层（P1：KANJIDIC2） ----------------
class KanjiDB:
    def __init__(self):
        self.con = sqlite3.connect(DICT_DB)
        self.con.row_factory = sqlite3.Row

    def get(self, ch):
        r = self.con.execute("SELECT * FROM kanji WHERE literal=?", (ch,)).fetchone()
        return dict(r) if r else None


# ---------------- WordNet 辨析层（P1：日本語WordNet 2.1） ----------------
class WordNetDB:
    """同类义项词（近义）+ 上位/下位/对义 + 日语释义。用于「A 和 B 有什么区别」类讲解。"""

    def __init__(self):
        self.con = sqlite3.connect(DICT_DB)
        self.con.row_factory = sqlite3.Row

    def _words_of_synset(self, sid, limit=6):
        return [r[0] for r in self.con.execute(
            "SELECT surface FROM wn_word WHERE synset=? ORDER BY length(surface) LIMIT ?", (sid, limit))]

    def explain(self, word, max_synsets=3):
        sids = [r[0] for r in self.con.execute(
            "SELECT DISTINCT synset FROM wn_word WHERE surface=? LIMIT ?", (word, max_synsets * 2))]
        out = []
        for sid in sids[:max_synsets]:
            syn = self.con.execute(
                "SELECT pos, definition, examples FROM wn_synset WHERE id=?", (sid,)).fetchone()
            if not syn:
                continue
            rels = {}
            for tgt, rel in self.con.execute("SELECT dst, rel FROM wn_rel WHERE src=?", (sid,)):
                rels.setdefault(rel, []).append(tgt)

            def rel_words(kind, n=4):
                res = []
                for t in rels.get(kind, [])[:n]:
                    w = self.con.execute("SELECT surface FROM wn_word WHERE synset=? LIMIT 1", (t,)).fetchone()
                    if w and w[0] not in res:
                        res.append(w[0])
                return res
            out.append({
                "synset": sid,
                "pos": syn["pos"],
                "definition": syn["definition"],
                "examples": json.loads(syn["examples"] or "[]"),
                "members": self._words_of_synset(sid, 10),
                "hypernym": rel_words("hypernym") or rel_words("instance_hypernym"),
                "hyponym": rel_words("hyponym") or rel_words("instance_hyponym"),
                "antonym": rel_words("antonym"),
            })
        return out



# ---------------- 分词 ----------------
_tokenizer = None

def get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        try:
            from sudachipy import dictionary
        except ImportError:
            sys.stderr.write("[Error] 缺少分词依赖，请先执行: pip install sudachipy sudachidict-core\n")
            sys.exit(2)
        _tokenizer = dictionary.Dictionary().tokenizer()
    return _tokenizer

def tokenize(sentence):
    tok = get_tokenizer()
    return [{"surface": t.surface(), "reading": t.reading_form() or t.surface(),
             "dict_form": t.dictionary_form(), "pos": t.part_of_speech()}
            for t in tok.tokenize(sentence)]

# ---------------- CLI ----------------
def _truncate(s, n=300):
    """按句边界截断，截断处标注，防模型脑补后半句。"""
    if len(s) <= n:
        return s
    cut = s[:n]
    for p in ("。", "．", ".", "；", ";"):
        i = cut.rfind(p)
        if i >= n // 2:
            return cut[:i + 1] + "…(截断)"
    return cut + "…(截断)"

def format_grammar(e, tier=None):
    fields = [("接续", "formation"), ("语感", "nuance"), ("例句", "examples")]
    marks = " ".join(f"{name}{'✓' if e.get(f) else '✗'}" for name, f in fields)
    head = f"◆ {e['pattern']}" + (f"（{e['reading']}）" if e.get("reading") else "") \
        + (f"  [{e['level']}]" if e.get("level") else "")
    if tier == "bare":
        head += "  [裸条目·无讲解]"
    lines = [f"{head}  {{{marks}}}"]
    if e.get("formation"):
        lines.append(f"  接续: {e['formation']}")
    if e.get("meaning_en"):
        lines.append(f"  EN: {e['meaning_en']}")
    if e.get("nuance_zh"):
        lines.append(f"  语感(中译): {_truncate(e['nuance_zh'])}")
    if e.get("nuance"):
        lines.append(f"  语感: {_truncate(e['nuance'])}")
    for ex in e.get("examples", [])[:2]:
        lines.append(f"  例: {ex['ja']}" + (f"  ({ex['en']})" if ex.get("en") else ""))
    return "\n".join(lines)

def format_dict(d, db=None):
    lines = [f"◆ {d['surface']}（{d['reading']}）"
             + (f"  [{d['jlpt']}]" if d.get("jlpt") else "")
             + (f"  {d['pos']}" if d.get("pos") else "")]
    if d.get("gloss_zh"):
        lines.append(f"  中: {d['gloss_zh'][:200]}")
    if d.get("gloss_en"):
        lines.append(f"  EN: {d['gloss_en'][:150]}")
    # P0/P1 新增维度：语域、领域、音调、频度
    meta = []
    if db is not None:
        if d.get("misc"):
            meta.append("语域: " + db.tag_text(d["misc"]))
        if d.get("field"):
            meta.append("领域: " + db.tag_text(d["field"]))
        if d.get("dialect"):
            meta.append("方言: " + db.tag_text(d["dialect"]))
    if d.get("pitch"):
        meta.append(f"音调: {d['pitch']} 型")
    if d.get("zipf"):
        meta.append(f"频度: zipf {d['zipf']}")
    if meta:
        lines.append("  ▸ " + " | ".join(meta))
    if d.get("info"):
        lines.append(f"  注: {_truncate(d['info'], 160)}")
    rel = d.get("related") or []
    if rel:
        lines.append("  关联: " + " / ".join(
            f"{r['word']}" + (f"({r['rel']})" if r.get("rel") else "") for r in rel[:4]))
    if d.get("antonym"):
        lines.append("  反义: " + " / ".join(d["antonym"][:4]))
    if d.get("lang_src"):
        lines.append("  外来语源: " + " / ".join(
            f"{(x.get('lang') or '?')}:{x['text']}" + ("(和制)" if x.get("wasei") else "")
            for x in d["lang_src"][:3]))
    for ex in d.get("examples", [])[:2]:
        t = ex.get("ja", "")
        tr = ex.get("zh") or ex.get("en") or ""
        lines.append(f"  例: {t}" + (f"  ({tr})" if tr else ""))
    return "\n".join(lines)


def format_conj(d, cdb):
    head = f"◆ {d['dict_form']}（{d['reading']}）  [{d['cls']}]"
    body = " | ".join(f"{lab} {v}" for lab, v in cdb.pairs(d))
    return head + "\n  " + body


def format_kanji(k):
    flags = []
    if k.get("grade"):
        flags.append(f"学年 {k['grade']}")
    if k.get("jlpt"):
        flags.append(f"JLPT N{k['jlpt']}")
    if k.get("stroke"):
        flags.append(f"{k['stroke']} 画")
    if k.get("radical"):
        flags.append(f"部首 {k['radical']}")
    if k.get("freq"):
        flags.append(f"新闻频次 {k['freq']}")
    lines = [f"◆ {k['literal']}  " + ("（" + " / ".join(flags) + "）" if flags else "")]
    if k.get("on_read"):
        lines.append(f"  音读: {k['on_read']}")
    if k.get("kun_read"):
        lines.append(f"  训读: {k['kun_read']}")
    if k.get("nanori"):
        lines.append(f"  名乘: {k['nanori']}")
    if k.get("meaning_zh"):
        lines.append(f"  中: {k['meaning_zh']}")
    if k.get("meaning_en"):
        lines.append(f"  EN: {k['meaning_en']}")
    return "\n".join(lines)


def format_syn(word, rows):
    if not rows:
        return f"— {word}：WordNet 无义项（可能是衍生词或口语缩略）"
    blocks = [f"— {word}"]
    for i, r in enumerate(rows, 1):
        blocks.append(f"  [{i}] {r['pos']}  {_truncate(r['definition'], 120)}")
        mem = [w for w in r["members"]]
        if mem:
            blocks.append("      近义/同类义项词: " + " / ".join(mem))
        for lab, key in (("上位", "hypernym"), ("下位", "hyponym"), ("对义", "antonym")):
            if r.get(key):
                blocks.append(f"      {lab}: " + " / ".join(r[key]))
        for ex in r.get("examples", [])[:1]:
            blocks.append(f"      例: {ex}")
    return "\n".join(blocks)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="本地日语语法+词典+汉字+活用+辨析检索")
    ap.add_argument("text", nargs="?", help="要分析的句子")
    ap.add_argument("--dict", nargs="+", metavar="WORD", help="查词典")
    ap.add_argument("--grammar", nargs="+", metavar="PATTERN", help="查语法条目")
    ap.add_argument("--conj", nargs="+", metavar="WORD", help="查活用表（动词/形容词全形态）")
    ap.add_argument("--kanji", nargs="+", metavar="CHAR", help="查汉字（音训/笔画/部首/年级）")
    ap.add_argument("--syn", nargs="+", metavar="WORD", help="查 WordNet 义项（近义/上位/对义，辨析用）")
    ap.add_argument("--sent", nargs="+", metavar="WORD", help="查 Tatoeba 日中对照真实例句")
    ap.add_argument("--limit", type=int, default=3, help="每项返回条数（默认 3）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    if a.dict:
        d = DictDB()
        out = {}
        for w in a.dict:
            rows = d.lookup(w)
            for r in rows:
                r["tatoeba"] = d.sentences(w, 2)
            out[w] = rows
        print(json.dumps(out, ensure_ascii=False, indent=1) if a.json
              else "\n\n".join("\n".join(format_dict(x, d) for x in v) for v in out.values()))
        return
    if a.grammar:
        g = GrammarDB()
        TIER = {"main": "★ 主条目", "rel": "○ 相关", "ref": "· 对比参考", "bare": "▽ 兜底"}
        if a.json:
            out = {p: [{"tier": t, **e} for t, e in g.search(p)] for p in a.grammar}
            print(json.dumps(out, ensure_ascii=False, indent=1))
        else:
            blocks = []
            for p in a.grammar:
                rows = g.search(p)
                if not rows:
                    blocks.append(f"— {p}：库内无匹配条目")
                    continue
                parts, last = [], None
                for t, e in rows:
                    if t != last:
                        parts.append(f"[{TIER[t]}]")
                        last = t
                    parts.append(format_grammar(e, tier=t))
                blocks.append(f"— {p}\n" + "\n".join(parts))
            print("\n\n".join(blocks))
        return
    if a.conj:
        cdb = ConjDB()
        rows = {w: cdb.lookup(w, a.limit) for w in a.conj}
        if a.json:
            out = {}
            for w, rs in rows.items():
                out[w] = [{"dict_form": r["dict_form"], "reading": r["reading"], "cls": r["cls"],
                           "forms": dict(cdb.pairs(r))} for r in rs]
            print(json.dumps(out, ensure_ascii=False, indent=1))
        else:
            blocks = []
            for w, rs in rows.items():
                if not rs:
                    blocks.append(f"— {w}：活用表无此词（3,511 词，只收动词/形容词）")
                    continue
                blocks += [format_conj(r, cdb) for r in rs]
            print("\n\n".join(blocks))
        return
    if a.kanji:
        kdb = KanjiDB()
        rows = {}
        for ch in a.kanji:
            rows[ch] = kdb.get(ch)
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            print("\n\n".join(format_kanji(v) if v else f"— {k}：KANJIDIC2 无此字" for k, v in rows.items()))
        return
    if a.syn:
        wdb = WordNetDB()
        out = {w: wdb.explain(w, a.limit) for w in a.syn}
        print(json.dumps(out, ensure_ascii=False, indent=1) if a.json
              else "\n\n".join(format_syn(w, v) for w, v in out.items()))
        return
    if a.sent:
        d = DictDB()
        out = {w: d.sentences(w, a.limit) for w in a.sent}
        print(json.dumps(out, ensure_ascii=False, indent=1) if a.json
              else "\n\n".join(
                  f"— {w}\n" + ("\n".join(f"  {r['ja']}\n    {r['zh']}" for r in v) or "  （无匹配例句）")
                  for w, v in out.items()))
        return
    if a.text:
        g = GrammarDB()
        tok = get_tokenizer()
        hits = g.match(a.text, tokenizer=tok)
        toks = tokenize(a.text)
        if a.json:
            print(json.dumps({"tokens": toks, "hits": hits}, ensure_ascii=False, indent=1))
        else:
            print(f"句子: {a.text}")
            print("分词: " + " / ".join(
                f"{t['surface']}({_hira(t['reading'])})[{t['dict_form']}]" for t in toks))
            print(f"命中语法点 {len(hits)} 个:\n")
            print("\n\n".join(format_grammar(e) for e in hits) or "（无）")
        return
    ap.print_help()

if __name__ == "__main__":
    main()
