# -*- coding: utf-8 -*-
"""jisho.org 例句检索 —— **只取真实句子，不造句子**（精简版）。

从 anki-workspace 的完整版复制而来，按本工作区需求裁剪：
**只负责抓取 + 过滤 + 排序**，去掉了振假名渲染（ruby_engine/SudachiPy）、
JMdict 读音核验、paths/textio 等全部本地依赖。
现在唯一的外部依赖是 ``requests``（工作区 ``.venv`` 已装，见根目录 requirements.txt）。

Jisho 的 Sentence 搜索页背后是 Tatoeba 与 Jreibun 两个人工语料库，句子、
英文翻译、出处链接、逐词注音（annotations）都是现成的，本脚本原样取回。

用法
----
::

    # 看候选
    python jisho_example.py --word "徹夜" --reading "てつや"

    # 机器可读
    python jisho_example.py --word "猫" --json

    # 只要第 3 条
    python jisho_example.py --word "猫" --pick 3

退出码
------
* ``0``：至少有一条候选；
* ``2``：Jisho 有响应，但过滤后没有可用候选（**不要退回模型造句**）；
* ``1``：网络/解析/依赖错误。

Jisho 的 robots.txt 要求 Crawl-delay: 40，本脚本**一次调用只抓一页**
（最多 20 条），不要拿它循环刷词。
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as html_mod
import json
import os
import re
import sys
from collections import Counter
from urllib.parse import quote

try:
    import requests
except ImportError as _exc:  # pragma: no cover
    print('❌ 缺少依赖（%s）。请用工作区 venv 运行：.venv\\Scripts\\python.exe（已含 requests/lxml）。' % _exc)
    raise SystemExit(1)

# 解析 Jisho 页面：有 lxml 就用 lxml；没有就用内置的极简解析器（本文件底部）。
# 设 JISHO_FORCE_BUILTIN=1 可强制走内置解析器（测试用）。
try:
    from lxml import html as LH
except ImportError:
    LH = None
if LH is not None and os.environ.get('JISHO_FORCE_BUILTIN'):
    LH = None
HTML_BACKEND = 'lxml' if LH is not None else 'builtin'
_MINI_DOC = None  # 无 lxml 时指向下方 _MiniDoc（文件末尾回填）

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
BASE = 'https://jisho.org'
KANJI = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')
BRACKETS = {'(': ')', '（': '）', '[': ']', '［': '］', '{': '}', '｛': '｝',
            '「': '」', '『': '』', '【': '】', '〈': '〉', '《': '》'}
END_PUNCT = re.compile(r'[。．！？!?…][」』”"）\]】]*$')
SOURCE_SCORE = {'Tatoeba': 2, 'Jreibun': 1}
MATCH_SCORE = {'exact': 30, 'compound': 18, 'phrase': 12, 'none': 0}


# ============================================================================
# 解析 Jisho 页面
# ============================================================================
def search_url(word):
    """Jisho 的 Sentence 搜索地址（``#sentences`` 必须编码进路径）。"""
    return '%s/search/%s%%20%%23sentences' % (BASE, quote(word, safe=''))


def _clone_no_furigana(node):
    """深拷贝一个 li 并删掉 furigana span（后端无关）。"""
    if LH is not None:
        clone = LH.fromstring(LH.tostring(node, encoding='unicode'))
        for f in clone.xpath('.//span[contains(@class,"furigana")]'):
            f.getparent().remove(f)
        return clone
    return _mini_clone_no_furigana(node)


def _ja_text(ul):
    """``ul.japanese_sentence`` → 纯日文句子（丢掉 furigana、保留标点）。"""
    chunks = []
    for child in ul.iterchildren():
        if child.tag == 'li':
            bases = child.xpath(
                './/span[contains(@class,"unlinked") or contains(@class,"linked")]')
            if bases:
                chunks.append(''.join(b.text_content() for b in bases))
            else:
                clone = _clone_no_furigana(child)
                chunks.append(clone.text_content())
        elif child.tag == 'br':
            chunks.append(' ')
        if child.tail:
            chunks.append(child.tail)
    text = html_mod.unescape(''.join(chunks))
    text = re.sub(r'[ \t\r\n]+', ' ', text).strip()
    # Jisho 源码里常有换行/缩进（浏览器渲染成空格）；日文词之间不留这种排版
    # 空格，但保留拉丁词两侧的空格（「これは pen です」）。
    jp = r'\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f'
    return re.sub(r'(?<=[%s])\s+(?=[%s])' % (jp, jp), '', text)


def _annotations(li):
    """Jisho 的 ``furigana + unlinked`` 对，按 DOM 顺序保留（原样返回，不加工）。"""
    out = []
    for tok in li.xpath('.//li[contains(@class,"clearfix")]'):
        fur = tok.xpath('.//span[contains(@class,"furigana")]')
        bases = tok.xpath(
            './/span[contains(@class,"unlinked") or contains(@class,"linked")]')
        if not fur or not bases:
            continue
        base = ''.join(b.text_content() for b in bases).strip()
        reading = ''.join(f.text_content() for f in fur).strip()
        if base and reading:
            out.append({'base': base, 'reading': reading})
    return out


def _source(li):
    spans = li.xpath('.//span[contains(@class,"inline_copyright")]')
    if not spans:
        return 'Jisho'
    txt = re.sub(r'\s+', ' ', spans[0].text_content()).strip()
    txt = txt.replace('—', '').replace('–', '').strip()
    return txt or 'Jisho'


def _detail_url(li):
    hrefs = li.xpath('.//a[contains(@class,"light-details_link")]/@href')
    if not hrefs:
        return ''
    href = hrefs[0]
    if href.startswith('//'):
        return 'https:' + href
    if href.startswith('/'):
        return BASE + href
    return href


def parse_page(content):
    """Jisho 例句页 HTML → ``{total, entries}``（不联网，便于离线测试）。"""
    if isinstance(content, (bytes, bytearray)):
        content = bytes(content).decode('utf-8', 'replace')
    doc = (_MINI_DOC or LH).fromstring(content)
    total = None
    h4 = doc.xpath('//div[contains(@class,"sentences_block")]//h4')
    if h4:
        m = re.search(r'(\d+)\s+found', h4[0].text_content())
        if m:
            total = int(m.group(1))
    entries = []
    for li in doc.xpath('//li[contains(@class,"entry") and contains(@class,"sentence")]'):
        uls = li.xpath('.//ul[contains(@class,"japanese_sentence")]')
        if not uls:
            continue
        ja = _ja_text(uls[0])
        if not ja:
            continue
        en = li.xpath('.//span[contains(@class,"english")]')
        debug = li.xpath('.//div[contains(@class,"debug")]')
        entries.append({
            'ja': ja,
            'en': re.sub(r'\s+', ' ', en[0].text_content()).strip() if en else '',
            'source': _source(li),
            'sentence_id': debug[0].text_content().strip() if debug else '',
            'url': _detail_url(li),
            'annotations': _annotations(li),
        })
    if total is None:
        total = len(entries)
    return {'total': total, 'entries': entries}


def fetch_page(url, timeout=30):
    r = requests.get(url, headers={
        'User-Agent': UA,
        'Accept-Language': 'ja,en;q=0.8',
    }, timeout=timeout)
    r.raise_for_status()
    return r.content


# ============================================================================
# 匹配 / 过滤 / 排序（纯字符串，无分词器）
# ============================================================================
def _hira(s):
    """片假名 → 平假名（去浊音符号的粗匹配，够用）。"""
    out = []
    for ch in s:
        o = ord(ch)
        if 0x30A1 <= o <= 0x30F6:
            out.append(chr(o - 0x60))
        else:
            out.append(ch)
    return ''.join(out)


def _stem(word, reading):
    """剥掉词尾的活用后缀，取词干：動詞「〜く/ぐ/す/つ/る/む/ぶ/ぬ/う」去 1 字，
    「〜る」动词去 2 字（取る→取），「〜す」去 2 字（出す→出）；名词原样。"""
    if not KANJI.search(word) and not reading:
        return word
    if len(word) >= 2:
        tail = word[-1]
        if tail == 'る' and len(word) >= 3:
            return word[:-2]
        if tail in 'くぐすつるむぶぬう':
            return word[:-1]
    return word


def classify_match(sentence, word, reading):
    """``exact``（词干/读音在句中出现）/ ``compound``（汉字复合词内含）/
    ``phrase``（≥4 字词面包含）/ ``none``。

    完整版用 Sudachi 的 token 边界挡 Jisho 的跨词假阳性（搜 ヤニ 命中 ニヤニヤ）；
    精简版没有分词器，改为：汉字词要求句中出现同一汉字串（再核对读音），
    纯假名词要求词形或读音整串出现——假阳性仍可能漏进来，exact 排序优先，
    人工挑句时留意。
    """
    stem = _stem(word, reading)
    rstem = _stem(reading, '') if reading else ''
    targets = {word, stem, _hira(word), _hira(stem)}
    if reading:
        targets |= {reading, rstem, _hira(reading), _hira(rstem)}
    targets = {x for x in targets if x}
    hira_sent = _hira(sentence)
    if any(t in sentence or t in hira_sent for t in targets):
        return 'exact'
    for m in KANJI.finditer(sentence):
        seg = m.group(0)
        seg_h = _hira(seg)
        if word in seg or (reading and reading in seg_h):
            return 'compound'
        if _hira(word) in seg_h:
            return 'compound'
    if len(word) >= 4 and (word in sentence or (reading and reading in sentence)):
        return 'phrase'
    return 'none'


def _balanced(text):
    stack = []
    for ch in text:
        if ch in BRACKETS:
            stack.append(BRACKETS[ch])
        elif ch in BRACKETS.values():
            if not stack or stack.pop() != ch:
                return False
    return not stack


def _dedupe_key(text):
    return re.sub(r'[\s。、，．.!?！？「」『』（）()\[\]【】]', '', text)


def _score(match, source, chars):
    return (MATCH_SCORE[match] + SOURCE_SCORE.get(source, 0)
            + max(0, 25 - abs(chars - 22)))


def filter_entries(entries, word, reading, args):
    """过滤 + 排序；返回 ``(kept, dropped_counts)``。"""
    dropped = Counter()
    seen = set()
    kept = []
    for entry in entries:
        ja = entry['ja']
        chars = len(ja)
        key = _dedupe_key(ja)
        if key in seen:
            dropped['duplicate'] += 1
            continue
        seen.add(key)
        if chars < args.min_chars or chars > args.max_chars:
            dropped['length'] += 1
            continue
        if not _balanced(ja):
            dropped['brackets'] += 1
            continue
        if not END_PUNCT.search(ja):
            dropped['no_end_punct'] += 1
            continue
        match = classify_match(ja, word, reading)
        if match == 'none' or (args.exact and match != 'exact'):
            dropped['no_match'] += 1
            continue
        entry = dict(entry)
        entry['chars'] = chars
        entry['match'] = match
        entry['score'] = _score(match, entry['source'], chars)
        kept.append(entry)
    kept.sort(key=lambda e: (-e['score'], e['chars'], e['ja']))
    return kept, dropped


# ============================================================================
# 输出
# ============================================================================
def _candidate(result, index):
    if not 1 <= index <= len(result['candidates']):
        raise IndexError('--pick 超出范围：1–%d' % len(result['candidates']))
    return result['candidates'][index - 1]


def print_table(result):
    word = result['word']
    reading = ('（%s）' % result['reading']) if result['reading'] else ''
    print('# %s%s — Jisho 例句' % (word, reading))
    print('查询：%s' % result['query_url'])
    print('共 %s 条，抓取 %d 条，匹配 %d 条，显示 %d 条' % (
        result['total_found'], result['fetched'], result['matched'],
        len(result['candidates'])))
    dropped = result['dropped']
    if dropped:
        print('过滤：' + ' / '.join('%s %d' % (k, v) for k, v in dropped.items()))
    for i, c in enumerate(result['candidates'], 1):
        print('\n%d. [%s ｜ %s ｜ %d 字] %s' % (
            i, c['match'], c['source'], c['chars'], c['ja']))
        if c.get('en'):
            print('   EN: %s' % c['en'])
        if c.get('annotations'):
            print('   注音: %s' % ' '.join(
                '%s(%s)' % (a['base'], a['reading']) for a in c['annotations']))
        print('   %s' % c['url'])


def print_pick(c):
    print('Sentence: %s' % c['ja'])
    print('Sentence Translation (EN): %s' % (c.get('en') or ''))
    print('Source: %s' % c['source'])
    print('URL: %s' % c['url'])
    if c.get('annotations'):
        print('Annotations: %s' % json.dumps(c['annotations'], ensure_ascii=False))


# ============================================================================
# 主流程
# ============================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='jisho_example.py',
        description='去 jisho.org 找真实例句（只取不造；不写 Anki）。')
    ap.add_argument('--word', required=True, help='目标日语词（词形或假名）')
    ap.add_argument('--reading', default='', help='可选读音（假名）；用于匹配')
    ap.add_argument('--limit', type=int, default=8, help='最多输出几条（默认 8）')
    ap.add_argument('--min-chars', type=int, default=5, help='句子最短字数（默认 5）')
    ap.add_argument('--max-chars', type=int, default=60, help='句子最长字数（默认 60）')
    ap.add_argument('--exact', action='store_true',
                    help='只保留整词命中（去掉复合词/短语）')
    ap.add_argument('--json', action='store_true', help='输出 JSON（stdout 只有 JSON）')
    ap.add_argument('--pick', type=int, default=0, metavar='N',
                    help='只输出第 N 条候选（1 起）')
    ap.add_argument('--out', default='', help='把完整 JSON 另存到这个路径')
    ap.add_argument('--timeout', type=float, default=30.0, help='HTTP 超时秒数')
    args = ap.parse_args(argv)

    word = args.word.strip()
    if not word:
        ap.error('--word 不能为空')
    if args.limit < 1:
        ap.error('--limit 必须 >= 1')
    if args.min_chars < 0 or args.max_chars < args.min_chars:
        ap.error('--min-chars/--max-chars 不合法')
    reading = args.reading.strip()
    query = search_url(word)

    try:
        content = fetch_page(query, args.timeout)
    except Exception as exc:
        print('❌ 请求 Jisho 失败：%s' % exc, file=sys.stderr)
        print('   查询地址：%s' % query, file=sys.stderr)
        return 1
    try:
        page = parse_page(content)
    except Exception as exc:
        print('❌ 解析 Jisho 页面失败：%s' % exc, file=sys.stderr)
        return 1
    if not page['entries']:
        print('❌ Jisho 没有返回例句（可能 0 条，或页面结构变了）。', file=sys.stderr)
        print('   查询地址：%s' % query, file=sys.stderr)
        return 2

    matched, dropped = filter_entries(page['entries'], word, reading, args)
    if not matched:
        print('❌ 没有可用候选（抓取 %d 条，全部被过滤）。' % len(page['entries']),
              file=sys.stderr)
        if dropped:
            print('   过滤：' + ' / '.join('%s %d' % (k, v) for k, v in dropped.items()),
                  file=sys.stderr)
        print('   查询地址：%s' % query, file=sys.stderr)
        print('   不要退回模型造句；可换词形/读音，或放宽 --min-chars/--max-chars 再试。',
              file=sys.stderr)
        return 2

    kept = matched[:args.limit]

    result = {
        'word': word,
        'reading': reading,
        'query_url': query,
        'fetched_at': dt.datetime.now().isoformat(timespec='seconds'),
        'total_found': page['total'],
        'fetched': len(page['entries']),
        'matched': len(matched),
        'kept': len(kept),
        'filters': {
            'min_chars': args.min_chars,
            'max_chars': args.max_chars,
            'exact': bool(args.exact),
        },
        'dropped': dict(dropped),
        'candidates': kept,
    }

    if args.out:
        out_path = args.out if os.path.isabs(args.out) else os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
            fh.write('\n')
        print('已写入：%s' % out_path, file=sys.stderr)

    if args.pick:
        try:
            candidate = _candidate(result, args.pick)
        except IndexError as exc:
            print('❌ %s' % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(candidate, ensure_ascii=False, indent=2))
        else:
            print_pick(candidate)
        return 0

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_table(result)
    return 0


# ============================================================================
# 内置极简 HTML 解析器（无 lxml 时的兜底；只实现本文件用到的几种 xpath）
# ============================================================================
class _MiniNode:
    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent
        self.text = ''
        self.tail = ''

    # ---- lxml 兼容接口 ----
    def get(self, key, default=None):
        return self.attrs.get(key, default)

    def iterchildren(self):
        return list(self.children)

    def text_content(self):
        parts = [self.text]
        for c in self.children:
            parts.append(c.text_content())
            parts.append(c.tail)
        return ''.join(parts)

    def getparent(self):
        return self.parent

    def remove(self, child):
        self.children.remove(child)

    def xpath(self, path):
        return _mini_xpath(self, path)

    def itertext(self):
        yield self.text
        for c in self.children:
            yield from c.itertext()
            yield c.tail

    def get(self, key, default=None):
        return self.attrs.get(key, default)


class _MiniDoc:
    """极简 HTML 解析：标签栈 + 属性正则。只支持本文件用到的谓词。"""

    @staticmethod
    def fromstring(content):
        root = _MiniNode('#root')
        stack = [root]
        VOID = {'br', 'img', 'hr', 'meta', 'link', 'input'}
        pos = 0
        token = re.compile(r'<(/?)([a-zA-Z][\w:-]*)((?:\s+[^>]*?)?)(/?)>'
                           r'|<!--.*?-->|<!\[CDATA\[.*?\]\]>|<![^>]*>', re.S)
        # 兜底分支（注释/doctype）没有 4 个捕获组，group(2)/(3) 会是 None
        # 自闭合标签（void）
        VOID = {'br', 'img', 'hr', 'meta', 'link', 'input'}
        # 隐式闭合：新标签出现时，栈上这些标签若允许被它闭合就弹出
        # （HTML 允许 <li>/<p>/<dt>/<dd>/<tr>/<td>/<th>/<option> 等不写闭合）
        IMPLICIT_CLOSE = {
            'li': {'li'},
            'p': {'p'},
            'dt': {'dt', 'dd'}, 'dd': {'dt', 'dd'},
            'tr': {'tr'}, 'td': {'td', 'th'}, 'th': {'td', 'th'},
            'option': {'option'}, 'optgroup': {'option', 'optgroup'},
            'thead': {'tbody', 'tfoot'}, 'tbody': {'thead', 'tbody', 'tfoot'},
            'tfoot': {'tbody', 'tfoot'},
        }
        # 这些容器标签可以包含隐式闭合的后代（遇到新标签时继续向上找）
        CONTAINER = {'html', 'head', 'body', 'ul', 'ol', 'table', 'tbody',
                     'thead', 'tfoot', 'tr', 'select', 'dl', 'form', 'fieldset'}
        while pos < len(content):
            m = token.search(content, pos)
            if not m:
                stack[-1].text += content[pos:]
                break
            if m.start() > pos:
                stack[-1].text += content[pos:m.start()]
            pos = m.end()
            closing, tag, attrstr, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
            if tag is None:
                continue
            tl = tag.lower()
            if closing:
                for i in range(len(stack) - 1, 0, -1):
                    if stack[i].tag.lower() == tl:
                        del stack[i:]
                        break
                continue
            # 隐式闭合：从栈顶向下找可被当前标签闭合的标签
            for i in range(len(stack) - 1, 0, -1):
                top = stack[i].tag.lower()
                if top in IMPLICIT_CLOSE.get(tl, ()):
                    del stack[i:]
                    break  # 闭合后重新检查栈顶（可能还有可隐式闭合的）
                elif top in CONTAINER:
                    continue  # 容器标签允许隐式闭合的后代直接挂在其下
                else:
                    break
            attrs = dict(re.findall(r'([\w:@-]+)\s*=\s*"([^"]*)"', attrstr))
            node = _MiniNode(tag, attrs, parent=stack[-1])
            stack[-1].children.append(node)
            if tag.lower() not in VOID and not selfclose:
                stack.append(node)
        # 文本里残留的 HTML 实体（&nbsp; 等）统一解码
        _mini_unescape(root)
        return root


def _mini_unescape(node):
    node.text = html_mod.unescape(node.text)
    for c in node.children:
        c.tail = html_mod.unescape(c.tail)
        _mini_unescape(c)


def _mini_clone_no_furigana(node):
    clone = _MiniNode(node.tag, dict(node.attrs))
    clone.text = node.text
    for c in node.children:
        if c.tag == 'span' and 'furigana' in (c.attrs.get('class') or ''):
            # furigana 的 tail（送假名）要并进前一个兄弟的 tail，否则丢字
            if clone.children:
                clone.children[-1].tail += c.tail
            else:
                clone.text += c.tail
            continue
        cc = _mini_clone_no_furigana(c)
        cc.parent = clone
        clone.children.append(cc)
    return clone


def _mini_xpath(node, path):
    """支持：'//tag[谓词]' 与 './/tag[谓词]' 链式组合、'@attr' 属性步、
    谓词内 and/or/not。谓词原子仅支持 contains(@class,"x")。"""
    steps = []
    for m in re.finditer(r'(\.)?/(/)?(?:([a-zA-Z][\w:-]*)((?:\[[^\]]*\])*)|@([\w:@-]+))', path):
        leading_dot, dbl, tag, preds, attr = m.groups()
        steps.append((bool(leading_dot), tag, attr, preds or ''))

    def _eval_atom(n, atom):
        atom = atom.strip()
        neg = atom.startswith('not(')
        if neg:
            atom = atom[4:-1]
        m = re.match(r'contains\(@(\w+),"([^"]*)"\)', atom)
        if not m:
            return False
        ok = m.group(2) in (n.attrs.get(m.group(1)) or '')
        return (not ok) if neg else ok

    def _eval_pred(n, expr):
        for clause in re.split(r'\s+or\s+', expr):
            if all(_eval_atom(n, a) for a in re.split(r'\s+and\s+', clause)):
                return True
        return False

    def match(n, tag, preds):
        if tag != '*' and n.tag.lower() != tag.lower():
            return False
        if preds and not _eval_pred(n, preds[1:-1]):
            return False
        return True

    def descendants(n):
        for c in n.children:
            yield c
            yield from descendants(c)

    current = [node]
    for leading_dot, tag, attr, preds in steps:
        if attr is not None:  # 属性步：返回字符串列表
            return [v for n in current for v in [n.attrs.get(attr)] if v]
        next_nodes = []
        for base in current:
            for d in descendants(base):
                if match(d, tag, preds):
                    next_nodes.append(d)
        seen, uniq = set(), []
        for n in next_nodes:
            if id(n) not in seen:
                seen.add(id(n))
                uniq.append(n)
        current = uniq
    return current


# 无 lxml / 强制内置时接线
if LH is None:
    _MINI_DOC = _MiniDoc


if __name__ == '__main__':
    raise SystemExit(main())
