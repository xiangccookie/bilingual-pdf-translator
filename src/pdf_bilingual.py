#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pdf_bilingual —— 把英文 PDF 论文做成「中英对照 PDF」的通用工具

================================================================================
这个工具解决什么
================================================================================
输入：一份英文 PDF 论文（尤其是双栏、图表是矢量图形的学术论文）
输出：一份中英对照 PDF —— 每段的「英文原文，中文译文」排在同一行内
      （译文紧跟原文、用逗号隔开，字号与颜色沿用英文原文那套），
      图表英文原样保留、图注照常翻译，可选追加「术语解释」章节。

它把一次真实项目（Tsushima et al. 2019, Genome Biol. Evol. 11(5):1487-1500）
里踩到的坑全部固化成了自动处理，不再需要手工坐标：

  坑 1  双栏排版的阅读顺序：必须「读完整左栏再读右栏」，不能按 y 排序
  坑 2  PDF 省略空格：两端对齐的段落里空格字符不存在，只能靠字形间距判断。
        实测（9.5pt 字号）：词内相邻字母间距为负值 −0.22 ~ −0.16，
        真实空格间距为 +1.19 ~ +1.22（比值 0.126）。本工具阈值取 0.08，
        两侧都有余量。
  坑 3  连字符断词 vs 真实复合词：com-partmentalized 要去掉连字符，
        而 gene-sparse 必须保留 —— 用「全文出现过的复合词表」来判别，
        不依赖外部词典。
  坑 4  静默的字符错误：老式字体编码会把 = 抽成 ¼、≥ 抽成 \\x05、
        é 拆成「组合变音符 + 无点 i」、上标负号抽成 \\x04、× 抽成 \\x03……
        全部可配置修复（改 repair_rules，不要改代码）。
  坑 5  图内文字是矢量轮廓：get_text() 取不到，只能「白底覆盖 + 原位重写」；
        角度必须用「圆心方位角 + 90°」，尺寸必须用「实测墨迹像素」，
        且**测量 dpi 必须与画布 dpi 一致**。
  坑 6  中文段落若用两端对齐，会把拉丁词挤到右边界 —— 必须用左对齐。
  坑 7  中文字体没有斜体变体，拉丁学名不会自动斜体 —— 需注册字体族，
        让 <i> 改走拉丁斜体字体。

================================================================================
怎么用（四步）
================================================================================
  # 第 1 步 · 提取：PDF → 结构化文本块（自动分类 + 自动找图表 + 自动裁图）
  python pdf_bilingual.py extract  论文.pdf --out work/ --config cfg.json

  # 第 2 步 · 拆批次：生成翻译工作包（每批交给一个译者或一个子任务）
  python pdf_bilingual.py worklist work/ --batch-words 1500 --config cfg.json

  # 第 3 步 · 翻译：把结果写成 work/zh/batch_01.json 等
  #          格式 {"B001": "第一段的中文译文", "B002": "……"}，块 ID 一个不能漏

  # 第 4 步 · 合成：译文 + 图表 + 术语 → 中英对照 PDF
  python pdf_bilingual.py build work/ --out 中英对照.pdf --config cfg.json \
        --glossary glossary.json --figures figures.json

  # 附加 · 图内标签换中文（独立子命令，build 不调用它；见 README）
  python pdf_bilingual.py figannot 论文.pdf --spec figspec.json --out fig/

依赖：pymupdf（读 PDF）、reportlab（写 PDF）、Pillow + numpy（图标注）
本机可直接用插件自带环境运行：
  ~/.dsh/dsh-bio-genie/python-env/Scripts/python.exe pdf_bilingual.py ...
或双击同目录的 run.ps1。

================================================================================
设计原则
================================================================================
· 所有「论文相关的具体值」（页眉文字、图表坐标、修复规则、术语）
  一律放配置文件，不写进代码 —— 换一篇论文只需改 JSON。
· 自动判断优先：页眉页脚靠「在页面顶/底区重复出现」识别，
  图表靠「该页矢量绘图多 + 有图注」识别，都不硬编码字符串。
· 每一处「看起来能算出来」的地方都写明实测依据，便于下一个人复核。
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import os
import re
import sys

# ============================================================================
# 0. 常量与默认规则（数值全部来自实测）
# ============================================================================

GAP_K = 0.08
"""断词阈值：字符间距 > GAP_K × 字号 就补一个空格。
实测依据：真实空格 ≈ 0.126 × 字号；词内相邻字母为负值（字形框轻微重叠）。
取 0.08 使两侧都有余量（不会把词切碎，也不会漏掉空格）。"""

LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
    "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "--", "\u02d8": "",
}
"""连字与花引号归一化。不还原的话，strain-speciﬁc 里的 ﬁ 是单个字符，
会污染翻译与检索。"""

DEFAULT_REPAIR = [
    # —— 上下文特例：必须排在通用规则之前 ——
    ["Domazet-Lo\u0003so", "Domazet-Lošo"],       # \x03 在数字中是 ×，在人名中是 š
    ["\u0002 The Author(s)", "© The Author(s)"],   # \x02 在版权行是 ©
    ["Sim~ao", "Simão"],                           # ~ 在此处是组合波浪符
    ["\u0002C", "°C"],                             # \x02 在温度处是度符号
    ["\u0002ı", "í"], ["\u0002i", "í"],
    ["\u0002e", "é"], ["\u0002o", "ó"], ["\u0002a", "á"],
    ["\u0002", ""],                                # 残余变音符丢弃
    # —— 通用规则 ——
    ["\u0003", "×"],       # 放大 420× 渲染确认：65\x03 = 65×
    ["\u0004", "⁻"],       # 上标负号：10\x046 = 10⁻⁶
    ["\u0005", "≥"],       # 放大确认：\x0599% = ≥99%
    ["\u0006", "≤"],       # 放大确认：\x0690% = ≤90%
    ["\u0007", "∼"],       # 放大确认：(\x077%) = (∼7%)
    ["¼", "="],            # 本文 12 处 ¼ 全部是等号
    ["5-ll droplets", "5-μl droplets"],
    ["conidia ml1", "conidia ml⁻¹"],
    ["22 C with", "22 °C with"],
    ["Mo¨ ller", "Möller"], ["Scho¨ ffmann", "Schöffmann"],
    # —— 变音符被拆成「基字母 + 独立变音符号（有时后跟一个空格）」——
    # 实测于多篇 PLoS 论文：抽取出的 Schüssler 变成 "Schu¨ ssler"、
    # Kõljalg 变成 "Ko˜ ljalg"、München 变成 "Mu¨nchen"、Průhonice 变成 "Pru˚honice"。
    # 400 dpi 放大原图逐字核对后确认。带空格的写法必须排在前面，否则空格会残留。
    ["u\u00a8 ", "ü"], ["u\u00a8", "ü"],
    ["o\u00a8 ", "ö"], ["o\u00a8", "ö"],
    ["a\u00a8 ", "ä"], ["a\u00a8", "ä"],
    ["e\u00a8 ", "ë"], ["e\u00a8", "ë"],
    ["i\u00a8 ", "ï"], ["i\u00a8", "ï"],
    ["o\u02dc ", "õ"], ["o\u02dc", "õ"],
    ["a\u02dc ", "ã"], ["a\u02dc", "ã"],
    ["u\u02da ", "ů"], ["u\u02da", "ů"],
    ["c\u02c7 ", "č"], ["c\u02c7", "č"],
    ["s\u02c7 ", "š"], ["s\u02c7", "š"],
    # —— 比较符 < > 被抽成 , . ——（实测于 PLoS 论文，520 dpi 放大逐处核对）
    # 只写完整短语，绝不做「句点→大于号」这类全局替换（会把正常句号全毁掉）。
    [",200 bp", "<200 bp"],
    [".2 obvious", ">2 obvious"],
    [",10 sequences", "<10 sequences"],
    [".10 chimeric", ">10 chimeric"],
]
"""默认字符修复表。**顺序有语义**：上下文特例必须在通用规则之前，
否则 \x03 会被无差别换成 ×，人名 Domazet-Lošo 就错了。
换论文时请按本文实际抽出的怪字符增补，并逐一放大原图核对。"""

DEFAULT_SPECIES = [
    "Colletotrichum higginsianum", "Arabidopsis thaliana", "Fusarium oxysporum",
    "Phytophthora infestans", "Magnaporthe oryzae", "Zymoseptoria tritici",
    "Bipolaris maydis", "Aspergillus terreus", "Saccharomyces cerevisiae",
    "Colletotrichum gloeosporioides", "Colletotrichum graminicola",
    "Colletotrichum orbiculare", "Colletotrichum destructivum",
    "C. higginsianum", "A. thaliana", "C. gloeosporioides", "C. graminicola",
]
"""拉丁学名清单：中文段落里也要按学术规范显示为斜体。"""

HEAD_RE = re.compile(
    r"^(FIG\.|Fig\.|Figure\s*\d|TABLE\s*\d|Table\s*\d|Supplementary\s+Material)", re.I)
REF_HEAD_RE = re.compile(r"^(Literature Cited|References|Bibliography)\s*$", re.I)
MID_X = 300.0
"""双栏版面的中缝位置（pt）。A4 双栏学术论文通常在 x≈300 处分栏。
非 A4 或栏宽不同时，在配置里覆盖。"""

TOP_BAND, BOTTOM_BAND = 70.0, 720.0
"""页眉区 / 页脚区的 y 范围（pt）。只有落在这两个带内且重复出现的文本，
才被判为页眉页脚 —— 避免误删正文中重复出现的短句。"""


# ============================================================================
# 1. 文字提取（解决坑 2 / 坑 3 / 坑 4）
# ============================================================================

def _norm_ligatures(s: str) -> str:
    """把连字与花引号还原成普通字符"""
    for k, v in LIGATURES.items():
        s = s.replace(k, v)
    return s


def apply_repair(text: str, rules=None) -> str:
    """按顺序套用字符修复规则（解决坑 4：不报错但答案是错的字符错误）"""
    for old, new in (rules or DEFAULT_REPAIR):
        if old in text:
            text = text.replace(old, new)
    return text


def _lines_of_block(block) -> list:
    """把 rawdict 的一个块拆成若干行，用**字形间距**补回被省略的空格（坑 2）。

    原理：PDF 生成器常把「空格」省掉、只靠坐标拉开距离。此时
      · 若 PDF 里真的有空格字符，字符本身就是 ' '
      · 若被省掉了，相邻字符的 x 间距会跳到约 0.126×字号
    两种都覆盖，才能拿到正确的断词。
    """
    lines = []
    for line in block["lines"]:
        buf, prev_x1 = "", None
        for span in line["spans"]:
            fs = span.get("size", 10.0)
            for ch in span["chars"]:
                c = ch["c"]
                if prev_x1 is not None and c != " " and not buf.endswith(" "):
                    if ch["bbox"][0] - prev_x1 > GAP_K * fs:
                        buf += " "
                buf += c
                prev_x1 = ch["bbox"][2]
        if buf.strip():
            lines.append((_norm_ligatures(buf.strip()), line["bbox"]))
    return lines


def _collect_hyphenated_terms(pdf) -> set:
    """扫全文，收集**本文真实使用过**的复合词（gene-sparse、between-strain…）。

    这是坑 3 的解法：不靠外部词典，靠本文自身的用法。
    行尾的 "gene-" 接下一行 "sparse" 时，
    若本文别处出现过 "gene-sparse"，说明连字符是真的，必须保留；
    否则就是断词（com- + partmentalized），要去掉连字符直接拼接。
    """
    terms = set()
    for page in pdf:
        for b in page.get_text("rawdict")["blocks"]:
            if b["type"] != 0:
                continue
            for ln, _ in _lines_of_block(b):
                for m in re.finditer(r"([A-Za-z]{2,})-([A-Za-z]{2,})", ln):
                    terms.add((m.group(1) + "-" + m.group(2)).lower())
    return terms


def _join_lines(lines, hyph_terms) -> str:
    """把若干行合并成一段（处理行尾断词，坑 3）"""
    txt = ""
    for ln, _ in lines:
        ln = ln.strip()
        if not ln:
            continue
        if txt.endswith("-"):
            left = re.search(r"([A-Za-z]+)-$", txt)
            right = re.match(r"([A-Za-z]+)", ln)
            if left and right:
                with_hyphen = (left.group(1) + "-" + right.group(1)).lower()
                if with_hyphen in hyph_terms:
                    txt += ln                    # 本文确实这么用过 → 保留连字符
                else:
                    txt = txt[:-1] + ln          # 是断词 → 去掉连字符直接接上
                continue
        txt += (" " if txt else "") + ln
    return re.sub(r"\s+", " ", txt).strip()


def extract_blocks(pdf_path, repair_rules=None, progress=True):
    """把 PDF 拆成带坐标的文本块列表（只忠实抽取，不分类、不排序）

    返回 (blocks, pdf 对象)。调用方负责 close。
    """
    import pymupdf
    pdf = pymupdf.open(pdf_path)
    hyph = _collect_hyphenated_terms(pdf)
    if progress:
        print(f"  全文真实复合词表：{len(hyph)} 条（用于区分断词与真复合词）")
    blocks = []
    for pno, page in enumerate(pdf, 1):
        for b in page.get_text("rawdict")["blocks"]:
            if b["type"] != 0:
                continue
            lines = _lines_of_block(b)
            if not lines:
                continue
            bb = b["bbox"]
            blocks.append({
                "page": pno,
                "x0": round(bb[0], 1), "y0": round(bb[1], 1),
                "x1": round(bb[2], 1), "y1": round(bb[3], 1),
                "text": apply_repair(_join_lines(lines, hyph), repair_rules),
            })
        if progress:
            print(f"  抽取第 {pno} 页 … 累计 {len(blocks)} 块", end="\r")
    if progress:
        print()
    return blocks, pdf


# ============================================================================
# 2. 分类与阅读顺序（解决坑 1）
# ============================================================================

def detect_repeated_bands(blocks, min_pages=3, verbose=True):
    """自动识别页眉页脚：在**页面顶/底区域**重复出现的短文本。

    刻意不硬编码 "Tsushima et al. GBE" 这类字符串 —— 换论文也能用。
    页码前缀/后缀会先剥离，因为页码本身每页都不同。
    """
    cnt = collections.Counter()
    for b in blocks:
        if b["y0"] < TOP_BAND or b["y0"] > BOTTOM_BAND:
            t = re.sub(r"^\d+\s+", "", b["text"]).strip()      # 去前缀页码
            t = re.sub(r"[\s]*\d{3,4}$", "", t).strip()         # 去后缀页码
            if 2 < len(t) < 120:      # 允许 "GBE" 这类 3 字符页眉（实测曾因 3<len 漏掉）
                cnt[t] += 1
    repeated = {t for t, n in cnt.items() if n >= min_pages}
    if verbose:
        print(f"  自动识别到 {len(repeated)} 条重复页眉/页脚：")
        for t in sorted(repeated)[:8]:
            print(f"      · {t[:72]}")
    return repeated


def classify(blocks, repeated, table_rects=None, mid_x=MID_X):
    """给每个块打初步标签

    action: translate（要译并做对照）/ skip（丢弃）
            —— ref（保留英文）由 mark_references() 二次判定，因为
               必须按**阅读顺序**判断，不能只看 y（见该函数说明）
    kind  : running-head / footer / copyright / table-cell / figcap / head / body
    col   : span（跨两栏的全宽块）/ L（左栏）/ R（右栏）

    优先级：丢弃规则 > 表格区 > 翻译。**先命中者不再被后续规则覆盖** ——
    曾因参考文献规则写在后面、把页眉页脚覆盖成 ref，导致页眉混进参考文献列表。
    """
    table_rects = table_rects or []
    recs = []
    for b in blocks:
        t = b["text"].strip()
        act, kind = None, "body"

        # —— ① 页眉 / 页脚 / 版权 → 丢弃 ——
        s1 = re.sub(r"^\d+\s+", "", t).strip()
        s2 = re.sub(r"[\s]*\d{3,4}$", "", s1).strip()
        if t in repeated or s1 in repeated or s2 in repeated:
            act, kind = "skip", "running-head"
        elif len(t) <= 8 and t.isupper() and b["y0"] < TOP_BAND:
            # 页眉里的刊名缩写（GBE / PNAS / Nature…）。
            # 实测：GBE 只在第 1 页出现一次，不满足「重复 ≥3 页」的条件，
            # 靠"重复"抓不到，必须靠"顶部 + 全大写 + 很短"这个特征。
            act, kind = "skip", "running-head"
        elif b["y1"] > BOTTOM_BAND and re.search(r"doi:\s*10\.|Advance Access publication", t):
            # ⚠️ 必须限定在页脚区。参考文献条目里同样含 "doi:10.xxx"，
            # 不加这个条件会把正文/参考文献误判成页脚丢掉（实测第 14 页丢 1 条）。
            act, kind = "skip", "footer"
        elif re.match(r"^(©|The Author\(s\)|\u0002)", t) or "Creative Commons Attribution" in t:
            act, kind = "skip", "copyright"

        # —— ② 已被裁图整体呈现的表格区域 → 丢弃单元格碎片 ——
        # 图注本身不算单元格：它是"表的标题"，必须保留（它触发裁图插入）
        if act is None and not HEAD_RE.match(t):
            for tr in table_rects:
                if (tr["page"] == b["page"] and tr["x0"] <= b["x0"] and b["x1"] <= tr["x1"]
                        and tr["y0"] <= b["y0"] <= tr["y1"]):
                    act, kind = "skip", "table-cell"
                    break

        # —— ③ 其余默认要翻译 ——
        if act is None:
            act = "translate"
            if HEAD_RE.match(t):
                kind = "figcap"
            elif REF_HEAD_RE.match(t):
                kind = "head"

        col = ("span" if (b["x0"] < mid_x and b["x1"] > mid_x)
               else ("L" if b["x0"] < mid_x else "R"))
        recs.append({**b, "action": act, "kind": kind, "col": col})
    return recs


def mark_references(recs, verbose=True):
    """按**阅读顺序**标出参考文献区（坑：不能只看 y 坐标）

    为什么必须按阅读顺序
    -------------------
    双栏版面里，左栏的 "Literature Cited" 标题在 y=166，
    而右栏的参考文献条目在 y=89 —— **y 更小，但阅读顺序在后**。
    若按 "y 大于标题" 判断，右栏这一整段的参考文献会被漏判成正文，
    于是被当成待译内容送进翻译流程（实测漏了 4 条）。

    正确做法：先算出每页的阅读顺序，找到标题后，把**其后的所有块**标为 ref。
    """
    by_page = collections.defaultdict(list)
    for r in recs:
        by_page[r["page"]].append(r)
    in_refs, n = False, 0
    for p in sorted(by_page):
        for b in reading_order(by_page[p]):
            if b["action"] == "skip":
                continue                      # 页眉页脚保持 skip，不被 ref 覆盖
            if REF_HEAD_RE.match(b["text"].strip()):
                in_refs = True                # 标题本身仍做对照（kind=head）
                continue
            if in_refs:
                b["action"] = "ref"
                b["kind"] = "reference"
                n += 1
    if verbose:
        print(f"  按阅读顺序标出参考文献块 {n} 条")
    return recs


def reading_order(blocks):
    """双栏阅读顺序（坑 1）

    ❌ 直接按 y 排序 —— 会左右栏穿插，读起来全乱
    ✅ 按 y 找出「跨两栏的全宽块」当分隔，每两段之间：先读完左栏、再读右栏
    """
    spans = sorted([b for b in blocks if b["col"] == "span"], key=lambda b: b["y0"])
    others = [b for b in blocks if b["col"] != "span"]
    key = lambda b: (0 if b["col"] == "L" else 1, b["y0"])
    out, prev = [], -1e9
    for sp in spans:
        out.extend(sorted([b for b in others if prev <= b["y0"] < sp["y0"]], key=key))
        out.append(sp)
        prev = sp["y0"]
    out.extend(sorted([b for b in others if b["y0"] >= prev], key=key))
    return out


# ============================================================================
# 3. 图表区域自动识别与裁图
# ============================================================================

PAGE_RULE_W, PAGE_RULE_H = 0.75, 6.0
"""判定「页面装饰线」的阈值：宽度 > 页宽 × PAGE_RULE_W 且高度 < PAGE_RULE_H 的绘图，
视为页眉/页脚的分隔横线，不算图形内容。

实测教训：本论文的页眉线宽 494.1pt，页宽 595.3pt，比值 0.83。
最初阈值写 0.85 时它**没被排除**，导致：
  · 图形区合并外接框被撑成全页（把右栏正文框进图里）
  · 表格区域把 750pt 处的页脚线也算进去，表格高度虚增到整页
阈值改到 0.75 后两种情况都正常。"""


def _graphics_bbox(page):
    """本页所有矢量绘图的合并外接框（排除页面装饰线）。

    为什么不用「整页宽度」定框：实测 FIG. 6 只占左栏，
    用整页宽度会把右栏的 Discussion 正文一起框进图里。
    """
    import pymupdf
    W = float(page.rect.width)
    rects = []
    for d in page.get_drawings():
        r = d["rect"]
        if r.width > W * PAGE_RULE_W and r.height < PAGE_RULE_H:
            continue                                # 页眉/页脚分隔线
        rects.append(r)
    if not rects:
        return None
    return pymupdf.Rect(min(r.x0 for r in rects), min(r.y0 for r in rects),
                        max(r.x1 for r in rects), max(r.y1 for r in rects))


def detect_figures(pdf, recs, verbose=True):
    """自动找图表区域。两类版式的判断方式不同：

      · FIG 类 ：图的图形在上、图注在下
                 → 区域 = 「矢量绘图范围」∩「图注以上」
      · Table 类：表格的横线本身就是那几条绘图
                 → 区域 = 图注下方的绘线范围

    返回 [{key, page, rect:[x0,y0,x1,y1], caption_id}]
    """
    import pymupdf
    figs = []
    by_page = collections.defaultdict(list)
    for r in recs:
        by_page[r["page"]].append(r)

    for pno, page in enumerate(pdf, 1):
        caps = [r for r in by_page.get(pno, []) if HEAD_RE.match(r["text"].strip())]
        if not caps:
            continue
        ndraw = len(page.get_drawings())
        W = float(page.rect.width)
        g = _graphics_bbox(page)
        for r in sorted(caps, key=lambda x: x["y0"]):
            t = r["text"].strip()
            m = re.match(r"^((?:FIG|Fig|Figure|TABLE|Table)\.?\s*\d+)", t)
            key = m.group(1) if m else t[:12]
            is_table = bool(re.match(r"^\s*(TABLE|Table)\s*\d", t))
            if is_table:
                # 表格的横线就是那几条绘图。必须**排除页面装饰线**，
                # 否则会把 750pt 处的页脚线也算进来，表格高度虚增到整页、
                # 连带吞掉同页的正文块（实测曾因此少译 19 块）。
                rules = [d["rect"] for d in page.get_drawings()
                         if d["rect"].y0 >= r["y0"] - 2.0
                         and not (d["rect"].width > W * PAGE_RULE_W
                                  and d["rect"].height < PAGE_RULE_H)]
                if not rules:
                    continue
                x0 = min(x.x0 for x in rules)
                x1 = max(x.x1 for x in rules)
                y0, y1 = r["y0"] - 2.0, max(x.y1 for x in rules) + 4.0
            else:
                if ndraw < 150 or g is None:
                    continue
                x0, x1 = g.x0 - 3.0, g.x1 + 3.0
                y0 = max(60.0, g.y0 - 3.0)
                y1 = min(r["y0"] - 1.0, g.y1 + 3.0)
            rect = [round(max(35.0, x0), 1), round(max(55.0, y0), 1),
                    round(min(W - 35.0, x1), 1), round(y1, 1)]
            if rect[3] - rect[1] < 20 or rect[2] - rect[0] < 20:
                continue
            figs.append({"key": key, "page": pno, "rect": rect, "caption_id": r.get("id")})
            if verbose:
                print(f"  发现 {key}：第 {pno} 页，图形区 x {rect[0]:.0f}–{rect[2]:.0f} "
                      f"y {rect[1]:.0f}–{rect[3]:.0f}（矢量绘图 {ndraw} 个）")
    return figs


def detect_tables(figs, verbose=True):
    """从已识别的图表中挑出**表格**区域。

    用途：表格整张已由裁图呈现，落在其范围内的文本块（表格单元格碎片）
    必须跳过 —— 否则表格内容会在正文里再以碎片形式重复输出一遍。
    """
    out = []
    for f in figs:
        if re.match(r"^\s*(TABLE|Table)\s*\d", f["key"].strip()):
            x0, y0, x1, y1 = f["rect"]
            out.append({"page": f["page"], "x0": x0, "y0": y0, "x1": x1, "y1": y1})
    if verbose and out:
        print(f"  表格区域 {len(out)} 个（单元格碎片不再重复输出）")
    return out


def crop_figures(pdf, figs, outdir, dpi=300, verbose=True):
    """按识别出的区域裁图（默认 300 dpi），把路径写回 figs 的 image 字段"""
    import pymupdf
    os.makedirs(outdir, exist_ok=True)
    for f in figs:
        page = pdf[f["page"] - 1]
        pix = page.get_pixmap(clip=pymupdf.Rect(*f["rect"]), dpi=dpi)
        safe = re.sub(r"[^\w.\-]+", "_", f["key"])
        path = os.path.join(outdir, f"{safe}_EN.png")
        pix.save(path)
        f["image"] = path
        if verbose:
            print(f"  裁图 {f['key']} → {path}  {pix.width}×{pix.height}px  "
                  f"{os.path.getsize(path):,} 字节")
    return figs


# ============================================================================
# 4. 生成翻译工作包
# ============================================================================

GLOSSARY_TEMPLATE = """# 翻译术语基准表（强制统一，所有批次共用）

## 论文背景
- 标题：{title}
- 说明：{note}

## 强制术语对照（必须使用右列译法）
{table}

## 保持原文不译（硬规则）
1. 物种学名（拉丁文）原样保留；中文段落里显示为斜体。
2. 菌株编号、登录号、基因 ID、软件名、数据库名、TE 超家族名原样保留。
3. 文献引用（如 `(Hoff et al. 2016)`）原样保留，不译作者名、不改年份。
4. 数字、单位、百分比、p 值、坐标**原样照抄，不得换算**。
5. 图表编号（FIG. 1、supplementary fig. S6）编号必须一致。
6. 不确定的英文缩写保留英文缩写，不要臆造中文全称。

## 文体要求
简体中文、学术书面语；按中文习惯断句但**不增删信息、不总结、不省略**；
段落结构必须与英文原文一一对应，不合并段落。
"""


def cmd_worklist(workdir, batch_words=1500, title="(未填写)", note="(未填写)", term_pairs=None):
    """按词数把待译块拆成若干批次，并生成术语基准表"""
    model = json.load(open(os.path.join(workdir, "doc_model.json"), encoding="utf-8"))
    tr = [r for r in model if r["action"] == "translate"]
    os.makedirs(os.path.join(workdir, "src"), exist_ok=True)
    os.makedirs(os.path.join(workdir, "zh"), exist_ok=True)

    tbl = "\n".join(f"| {a} | {b} |" for a, b in (term_pairs or [])) or "| （按需补充） | |"
    with open(os.path.join(workdir, "glossary_seed.md"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write(GLOSSARY_TEMPLATE.format(title=title, note=note, table=tbl))

    batches, cur, curw = [], [], 0
    for r in tr:
        w = len(r["text"].split())
        if cur and curw + w > batch_words:
            batches.append(cur); cur, curw = [], 0
        cur.append(r); curw += w
    if cur:
        batches.append(cur)

    for i, bs in enumerate(batches, 1):
        out = []
        for r in bs:
            out.append("=== [%s] page=%d kind=%s ===" % (r["id"], r["page"], r["kind"]))
            out.append(r["text"])
            out.append("")
        with open(os.path.join(workdir, "src", f"batch_{i:02d}.txt"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out))

    total_w = sum(len(r["text"].split()) for r in tr)
    print(f"要翻译 {len(tr)} 块 / {total_w} 词 → 拆成 {len(batches)} 个批次")
    for i, bs in enumerate(batches, 1):
        print(f"   batch_{i:02d}.txt  {len(bs):3d} 块  "
              f"{sum(len(r['text'].split()) for r in bs):5d} 词  "
              f"页 {sorted(set(r['page'] for r in bs))}")
    print(f"\n译好后写成 {workdir}/zh/batch_01.json …（格式 {{\"B001\": \"译文\"}}）")
    print("⚠️ 单个批次文件建议不超过 ~6000 字，太大容易在一次性写文件时失败。")


# ============================================================================
# 5. 合成中英对照 PDF
# ============================================================================

def _setup_fonts():
    """注册中文字体 + 字体族（解决坑 7）

    注册字体族是必需的：中文字体没有斜体变体，不注册的话
    <i>Colletotrichum</i> 会**静默地**显示为直立，看不出错误。
    注册后 <i> 会改走 Helvetica-Oblique，拉丁学名才真的斜体。
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    fdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    plan = {"CJK": ["msyh.ttc", "simsun.ttc", "Deng.ttf", "simhei.ttf"],
            "CJKB": ["msyhbd.ttc", "simhei.ttf", "msyh.ttc"]}
    got = {}
    for name, cands in plan.items():
        for c in cands:
            p = os.path.join(fdir, c)
            if os.path.exists(p):
                try:
                    pdfmetrics.registerFont(TTFont(name, p, subfontIndex=0))
                    got[name] = c
                    break
                except Exception:
                    continue
    if "CJK" not in got:                     # 兜底：reportlab 内置简体中文 CID 字体
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        got["CJK"] = "STSong-Light(builtin)"
    got.setdefault("CJKB", got["CJK"])
    pdfmetrics.registerFontFamily("CJK", normal="CJK", bold="CJKB",
                                  italic="Helvetica-Oblique",
                                  boldItalic="Helvetica-BoldOblique")
    pdfmetrics.registerFontFamily("CJKB", normal="CJKB", bold="CJKB",
                                  italic="Helvetica-BoldOblique",
                                  boldItalic="Helvetica-BoldOblique")
    print("  字体:", got)
    return got


def _rich(text, species):
    """转义 XML + 拉丁学名斜体 + 上标指数还原"""
    t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for sp in sorted(species, key=len, reverse=True):
        t = re.sub(r"(?<![\w>])" + re.escape(sp) + r"(?![\w<])", "<i>" + sp + "</i>", t)
    t = re.sub(r"10⁻(\d+)", r"10<super>-\1</super>", t)
    t = t.replace("ml⁻¹", "ml<super>-1</super>")
    return t


# 不该被当作句末的缩写（点号属于缩写本身）
# ⚠️ 必须带 \b 词边界：否则 "st" 会匹配到 "test." 的尾巴、"al" 会匹配到 "total."，
#    结果整段被当成一句（实测踩过：本文 B065 因此少切 2 句）。
_ABBR = (r"(?:e\.g|i\.e|et\s+al|al|fig|figs|eq|ref|refs|no|vs|etc|approx|ca|tab"
         r"|suppl|resp|cf|dr|prof|st|jr|sr|inc|ltd|co|vol|pp)")


def _zh_same_as_en(a, b):
    """译文与英文几乎相同？—— 人名、拉丁学名、编号这类"原文照抄"的片段

    实测：作者行 "Susanne S. Renner1,*, , Mark D. Scherz2,*, …" 会被 S. 的句点
    切成 4 段人名碎片，逐句交替就印成 "Susanne S.，Susanne S. Renner1…" 重复。
    这里逐句判断：若这一句的中文和英文实质相同，就不要再印一遍中文。
    """
    import difflib
    x = re.sub(r"[\s。.,，、]", "", str(a)).lower()
    y = re.sub(r"[\s.,]", "", str(b)).lower()
    if not x or not y:
        return False
    return difflib.SequenceMatcher(None, x, y).ratio() > 0.85


def split_sentences(text):
    """把一段英文切成句子（逐句对照模式要用）

    判据必须同时满足才算「句子边界」：
      ① 句末标点（. ? !）之后必须有空白或已到结尾 ——
         这样 URL 里的点（www.ncbi.nlm.nih.gov）、10.3389/fonc.2025 里的点
         都不会被当成句号。
      ② 点号两侧不能同时是数字 —— 排除 0.65、2.3e−05、0.05 这类小数。
      ③ 点号前面不能是常见缩写 —— 排除 et al. / Fig. / e.g. / vs. 等（须有词边界）。
      ④ 下一句的开头要「像个句子」：大写字母、数字、( " 引号括号、星号，
         或者**小写开头的技术名**（qRT-PCR / eGFP / iPSC / pH）。

    实测（本文 131 块）：
      · 不带判据①→ 参考文献里的 URL 被切成 3–5 段碎句；
      · 缩写表不带 \\b → "test." 被当成 "st." 缩写，整段只切出 1 句；
      · 判据④ 只认大写 → "qRT-PCR and immunohistochemistry confirmed…"
        这句永远切不开，英中句数对不上。
    """
    s = (text or "").strip()
    if not s:
        return []
    out, start = [], 0
    for m in re.finditer(r"[.!?]['\")]?(?=\s|$)", s):
        pos = m.end()
        if pos <= start + 1:
            continue
        head, punct = s[start:pos], m.group(0)
        # ② 小数：点号紧邻两侧都是数字（"0.65" 的 0 和 6）
        i = pos - 1
        if punct[0] == "." and i >= 1 and s[i - 1].isdigit() \
                and i + 1 < len(s) and s[i + 1].isdigit():
            continue
        # ③ 缩写：看句末标点前最后一个词（必须整词相等，不能是尾巴）
        if punct[0] == "." and re.search(r"\b" + _ABBR + r"\.$", head, re.I):
            continue
        # ④ 下一句的开头要像个句子
        nxt = s[pos:].lstrip()
        c = nxt[:1]
        ok_start = (not c) or c.isupper() or c.isdigit() or c in "(\"'[*"
        if not ok_start:
            w = re.match(r"[A-Za-z]+", nxt)
            word = w.group(0) if w else ""
            ok_start = (
                # qRT-PCR / eGFP / iPSC / pH 这类「小写开头但含大写」的技术名
                bool(re.match(r"^[a-z]{1,3}[A-Z]", word))
                # ggplot2 / log2FC 这类「小写+数字」的软件名、变量名
                or bool(re.match(r"^[a-z]{2,}[0-9]", nxt))
                # p < 0.05 / p = 0.01 这类统计量小句
                or bool(re.match(r"^p\s*[<>=]", nxt))
            )
        if not ok_start:
            continue
        out.append(head.strip())
        start = pos
    tail = s[start:].strip()
    if tail:
        out.append(tail)
    return out


def cmd_build(workdir, out_pdf, glossary=None, figures=None, species=None,
              title_zh="", title_en="", source_line="", cover_note="", mid_x=MID_X):
    """把译文合成为中英对照 PDF"""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (BaseDocTemplate, Frame, HRFlowable,
                                    Image as RLImage, KeepTogether, PageBreak,
                                    PageTemplate, Paragraph, Spacer)
    from PIL import Image as PImage

    species = species or DEFAULT_SPECIES
    model = json.load(open(os.path.join(workdir, "doc_model.json"), encoding="utf-8"))
    ZH = {}
    for f in sorted(glob.glob(os.path.join(workdir, "zh", "batch_*.json"))):
        ZH.update(json.load(open(f, encoding="utf-8")))
    print(f"  文本块 {len(model)}，已加载译文 {len(ZH)}")

    _setup_fonts()
    W, H = A4
    LM = RM = 46
    TM, BM = 52, 46
    MAXW = W - LM - RM
    BLUE = colors.HexColor("#0B3D68")
    GREY = colors.HexColor("#C9D2D9")

    # ---- 中英同排版式（本工具的默认版式）------------------------------
    # 一个文本块 = 一段：英文原文 + 逗号 + 中文译文，同一行内续排。
    # 译文的「字号、颜色」完全沿用英文原文那套（8.2pt / #5B6670 灰），
    # 只把字体换成有汉字字形的中文字体 —— Helvetica 里没有汉字，
    # 中文会被渲染成空白，所以这一项不能照字面照搬。
    # 于是读起来像「原文后面紧跟一句注解」，而不是另起一个段落。
    #
    # ⚠️ wordWrap="CJK" 是必需的（2026-10-19 实测）：
    #    reportlab 默认**只按空格断行**，而中文句子里没有空格 ——
    #    「一整句中文」会被当成一个不可拆的词，剩余宽度放不下就整句挪到下一行，
    #    于是英文那行右边留一大块空白。
    #    实测（本文 B025 引言段）：最大段中留白 331.5pt（版心才 495pt），
    #    >100pt 的空洞 3 处；开 CJK 断行后 → 最大 41.3pt、>100pt 的空洞 0 处。
    #    另测：CJK 断行**不会**劈开英文单词（45 字母的超长词两次都完整）。
    #
    # TA_LEFT 是必需的（坑 6）：两端对齐靠拉伸空格实现，中文句内没有空格，
    # 结果会把句中的拉丁词（DNA、gene）单独挤到右边界。
    INK = colors.HexColor("#5B6670")
    S = {
        "inline_body": ParagraphStyle("inline_body", fontName="CJK", fontSize=8.2,
                                      leading=13.0, alignment=TA_LEFT, spaceAfter=6,
                                      wordWrap="CJK", textColor=INK),
        "inline_figcap": ParagraphStyle("inline_figcap", fontName="CJKB", fontSize=10.8,
                                        leading=15.5, alignment=TA_LEFT, spaceAfter=6,
                                        wordWrap="CJK",
                                        textColor=colors.HexColor("#2A333A")),
        "inline_head": ParagraphStyle("inline_head", fontName="CJKB", fontSize=12,
                                      leading=17, alignment=TA_LEFT,
                                      spaceBefore=7, spaceAfter=5,
                                      wordWrap="CJK",
                                      textColor=colors.HexColor("#14507F")),
        "h1": ParagraphStyle("h1", fontName="CJKB", fontSize=15, leading=21,
                             spaceBefore=8, spaceAfter=6, textColor=BLUE),
        "h2": ParagraphStyle("h2", fontName="CJKB", fontSize=12, leading=17,
                             spaceBefore=9, spaceAfter=4,
                             textColor=colors.HexColor("#14507F")),
        "h3": ParagraphStyle("h3", fontName="CJKB", fontSize=10.8, leading=15.5,
                             spaceBefore=7, spaceAfter=3),
        "term": ParagraphStyle("term", fontName="CJKB", fontSize=10.6, leading=14.4,
                               spaceBefore=7, spaceAfter=2, textColor=BLUE,
                               wordWrap="CJK"),
        "term_body": ParagraphStyle("term_body", fontName="CJK", fontSize=9.4, leading=14,
                                    leftIndent=11, spaceAfter=2.5, alignment=TA_LEFT,
                                    wordWrap="CJK"),
        "note": ParagraphStyle("note", fontName="CJK", fontSize=9, leading=14,
                               wordWrap="CJK",
                               textColor=colors.HexColor("#444444")),
        "meta": ParagraphStyle("meta", fontName="CJK", fontSize=9.2, leading=13.6,
                               alignment=TA_CENTER),
        "title_zh": ParagraphStyle("title_zh", fontName="CJKB", fontSize=19, leading=27,
                                   alignment=TA_CENTER, spaceAfter=8),
        "title_en": ParagraphStyle("title_en", fontName="Times-Italic", fontSize=11.5,
                                   leading=15, alignment=TA_CENTER, spaceAfter=10,
                                   textColor=colors.HexColor("#4A545C")),
        "figsrc": ParagraphStyle("figsrc", fontName="CJK", fontSize=8.0, leading=11,
                                 spaceAfter=5, textColor=colors.HexColor("#8A939B")),
        "ref": ParagraphStyle("ref", fontName="Helvetica", fontSize=7.4, leading=9.8,
                              alignment=TA_LEFT, spaceAfter=2.4,
                              textColor=colors.HexColor("#40484F")),
    }

    FRAME_H = H - TM - BM

    def img_flow(path, reserve=34):
        """按「宽度与高度都不超出可用空间」缩放。

        ⚠️ 只按宽度缩放会踩坑：竖长图（如本论文 FIG. 2）按宽度缩完后
        高度仍可能超过版面高度，reportlab 会直接抛
        LayoutError: Flowable <Image ...> too large on page N。
        reserve 是给同页的小标题/图注留的高度。
        """
        iw, ih = PImage.open(path).size
        sc = min(1.0, MAXW / iw, (FRAME_H - reserve) / ih)
        return RLImage(path, width=iw * sc, height=ih * sc)

    doc = BaseDocTemplate(out_pdf, pagesize=A4, leftMargin=LM, rightMargin=RM,
                          topMargin=TM, bottomMargin=BM,
                          title=title_zh or "中英对照版")

    def footer(canv, d):
        canv.saveState()
        canv.setStrokeColor(GREY); canv.setLineWidth(0.5)
        canv.line(LM, BM - 12, W - RM, BM - 12)
        canv.setFont("CJK", 7.6); canv.setFillColor(colors.HexColor("#7A858E"))
        canv.drawString(LM, BM - 22, source_line or "中英对照版")
        canv.drawRightString(W - RM, BM - 22, "第 %d 页" % d.page)
        canv.restoreState()

    doc.addPageTemplates([PageTemplate(id="main",
                                       frames=[Frame(LM, BM, MAXW, H - TM - BM)],
                                       onPage=footer)])

    story = [Spacer(1, 26)]
    if title_zh:
        story.append(Paragraph(_rich(title_zh, species), S["title_zh"]))
    if title_en:
        story.append(Paragraph(_rich(title_en, species), S["title_en"]))
    story.append(HRFlowable(width="100%", thickness=0.8, color=BLUE, spaceAfter=12))

    def _zh_text(v):
        """译文可能是字符串（整段）或列表（逐句），统一取成字符串"""
        return "".join(str(x) for x in v) if isinstance(v, list) else str(v or "")

    for b in model:
        if b["page"] == 1 and b["kind"] in ("authors", "affiliation"):
            z = _zh_text(ZH.get(b["id"], ""))
            if z:
                story.append(Paragraph(_rich(z, species), S["meta"]))
    if cover_note:
        story.append(Spacer(1, 12))
        story.append(HRFlowable(width="100%", thickness=0.4, color=GREY, spaceAfter=6))
        story.append(Paragraph(cover_note, S["note"]))
    story.append(PageBreak())

    bypage = collections.defaultdict(list)
    for r in model:
        bypage[r["page"]].append(r)

    main_pages, ref_pages = [], []
    for p in sorted(bypage):
        n_tr = sum(1 for x in bypage[p] if x["action"] == "translate")
        n_rf = sum(1 for x in bypage[p] if x["action"] == "ref")
        (ref_pages if n_rf > n_tr else main_pages).append(p)

    figpath = {}
    for f in (figures or {}).get("figures", []):
        if f.get("image") and os.path.exists(f["image"]):
            # 连同 caption_id 一起存：插图时优先按「块 ID == 图注 ID」精确命中。
            # ⚠️ 只按文字前缀匹配会重复插图 —— 实测 syad068 里
            #    "Table 1 provides the numbers…"（正文引用）与
            #    "Table 1. Species names…"（表注）都以 "Table 1" 开头，
            #    同一张表图被插了两次（清单 3 张，成品里 4 张）。
            figpath[f["key"]] = {"image": f["image"], "caption_id": f.get("caption_id")}

    # ---- 英文与它的中文之间的分隔符 -------------------------------------
    # 规则（用户 2026-10-19 定）：英文那半句**以句号收尾时不再加逗号** ——
    #   "…disease. 中文译文。"        句号本身就是分界
    # 而不是：                        "…disease.，中文译文。"
    # 例外：英文没有句末标点时（小标题 "Materials and methods"、
    # 被分页切断的半句 "…correlations with patient"）仍用逗号，
    # 否则英文和中文会连成一串，看不出分界。
    SEP_TAIL = " "        # 英文以 . ! ? 收尾 → 只留一个空格
    SEP_OPEN = "，"       # 英文没有句末标点 → 用逗号隔开
    SENT_END_RE = re.compile(r"[.!?]['\")\]]*$")

    def style_for(b):
        """块类型 → (版式名, 英文用哪个字体)

        head / figcap 是标题行：英文也跟着标题字号走，
        否则一行里中文 10.8pt、英文 8.2pt，看着像排版坏了。
        正文块：英文走 Helvetica（拉丁字形完整）、中文走 CJK（有汉字字形），
        但两者字号与颜色完全一样，看上去是一整段。
        """
        if b["kind"] == "head":
            return "inline_head", None
        if b["kind"] == "figcap":
            return "inline_figcap", None
        return "inline_body", "Helvetica"

    def bilingual_para(b, style, en_font):
        """一个块 → 一段，同一行内续排。支持两种译文形态：

          · ZH 是**字符串** → 整段对照：英文整段 + 分隔符 + 中文整段
          · ZH 是**列表**   → 逐句对照：英文第1句 + 分隔符 + 中文第1句 +
                              英文第2句 + 分隔符 + 中文第2句 …（一句英文一句中文）

        分隔符按「英文这半句有没有句号」动态选择：
        有句号 → 只留空格（句号已经是分界）；没句号 → 用逗号，免得连成一串。

        列表句数与英文句数对不上时**退回整段对照并打印警告** ——
        宁可版式回退，也不能把句子错位或静默吞掉。
        没有译文时只排英文原文，同样是为了不静默丢内容。
        """
        def wrap_en(e):
            e = _rich(e, species)
            return ('<font name="%s">%s</font>' % (en_font, e)) if en_font else e

        def sep_for(e):
            return SEP_TAIL if SENT_END_RE.search(e.rstrip()) else SEP_OPEN

        z = ZH.get(b["id"], "")
        if isinstance(z, list):
            ens = split_sentences(b["text"])
            zs = [str(x).strip() for x in z]
            # 护栏 1：平均"句长"太短 → 这不是散文，是名单/表格行。
            #   实测 syad068 作者行 "Susanne S. Renner1,*, , Mark D. Scherz2,*, …"
            #   被 S. 的句点切成 8 段人名碎片，逐句交替会印成
            #   "Susanne S.，Susanne S. Renner1…" 这种重复。→ 退回整段对照。
            prose = (len(ens) <= 2 or
                     sum(len(x) for x in ens) / max(1, len(ens)) >= 25)
            if len(ens) == len(zs) and prose:
                # 护栏 2：译文与英文几乎相同（人名、拉丁学名原样保留）→ 只印一次，
                #   否则会出现 "Xinping Sun，Xinping Sun"。
                if re.sub(r"[\s。.]", "", "".join(zs)).lower() == \
                        re.sub(r"[\s.]", "", b["text"]).lower():
                    return Paragraph(wrap_en(b["text"].rstrip()), S[style])
                parts = []
                for e, cz in zip(ens, zs):
                    parts.append(wrap_en(e))
                    # 逐句去重：这一句的中文与英文实质相同（人名/拉丁名/编号）
                    # 就不要再印一遍，否则会变成 "Susanne S.，Susanne S. Renner1…"
                    if cz and not _zh_same_as_en(e, cz):
                        parts.append(sep_for(e) + _rich(cz, species))
                    parts.append(" ")          # 句间空格：免得中文句号直接贴下一句英文
                return Paragraph("".join(parts).rstrip(), S[style])
            if len(ens) != len(zs):
                print("  ⚠ %s 句数不匹配（英文 %d 句 / 中文 %d 句），该块退回整段对照"
                      % (b["id"], len(ens), len(zs)))
            z = "".join(zs)
        z = (z or "").strip()
        en = b["text"].rstrip()
        return Paragraph(wrap_en(en) + (sep_for(en) + _rich(z, species) if z else ""),
                         S[style])

    def emit(b):
        """输出一个块（英文原文与中文译文同排一段）"""
        t = b["text"]
        for k, meta in figpath.items():          # 图注之前插入原图
            # 精确匹配优先：块 ID == 该图的 caption_id。
            # 没有 caption_id 时才退回「文字以 key 开头」的老办法，
            # 并且要求 key 后面紧跟的不是数字（否则 FIGURE 1 会误配 FIGURE 10）。
            kk = k.strip()
            cid = meta.get("caption_id")
            if cid:
                hit = (b.get("id") == cid)
            else:
                hit = bool(kk and re.match(re.escape(kk) + r"(?!\d)", t.strip()))
            if hit:
                story.append(Spacer(1, 4))
                try:
                    story.append(img_flow(meta["image"]))
                except Exception as e:
                    print("  插图失败:", meta["image"], e)
                story.append(Spacer(1, 4))
                break
        style, en_font = style_for(b)
        story.append(bilingual_para(b, style, en_font))

    def is_panel_label(t):
        return t.strip() in ("A", "B", "A B", "A  B", "C", "D")

    for pno in main_pages:
        story.append(Paragraph("原文第 %d 页" % pno, S["h1"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=BLUE, spaceAfter=7))
        for b in reading_order(bypage[pno]):
            # ⚠️ action == "skip" 必须在这里跳过。classify() 的语义是
            #    「skip（丢弃）」—— 页眉、页脚、期刊前置信息、版权声明都靠它丢掉。
            #    实测踩过：这里只判 ref 不判 skip，结果每页页脚
            #    "Frontiers in Oncology frontiersin.org 06" 和首页的
            #    OPEN ACCESS / EDITED BY / DOI 全被当成正文印了出来（19 页 18 处）。
            if b["action"] in ("skip", "ref") or is_panel_label(b["text"]):
                continue
            # 第 1 页的题目/作者/单位/数据声明已在封面呈现，正文不重复
            if pno == 1 and b["kind"] in ("title", "authors", "affiliation", "footnote"):
                continue
            emit(b)
        story.append(PageBreak())

    if ref_pages:
        story.append(Paragraph("参考文献（Literature Cited · 保留英文原文）", S["h1"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=BLUE, spaceAfter=7))
        for pno in ref_pages:
            for b in reading_order(bypage[pno]):
                if b["action"] == "translate":
                    # ⚠️ 参考文献页上仍可能有上一节的正文（如致谢结尾），
                    #    这些块必须照常做对照输出，否则会被静默漏掉。
                    if is_panel_label(b["text"]):
                        continue
                    style, en_font = style_for(b)
                    story.append(bilingual_para(b, style, en_font))
                elif b["action"] == "ref":
                    story.append(Paragraph(_rich(b["text"], species), S["ref"]))

    # 注：旧版这里还有一整节「图表中文对照」—— 英文原图 + 中文标注版并排 +
    # 图内英文标注的中文对照表。已按要求整节删除：成品里不再有任何图片翻译，
    # 正文只保留英文原图（图注照常翻译，走上面的 bilingual_para）。
    # figannot / labelboxes 两个附加子命令仍在，但它们不参与 build。

    if glossary and glossary.get("themes"):
        n = sum(len(t["terms"]) for t in glossary["themes"])
        story.append(PageBreak())
        story.append(Paragraph("术语解释 · 按主题分类", S["h1"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=BLUE, spaceAfter=7))
        story.append(Paragraph(
            "以下按主题列出本文出现的专业术语，共 %d 条。每条给出：英文原名、中文名、"
            "「一句话说清它是什么」，以及「它在这篇论文里具体指什么」。" % n, S["note"]))
        story.append(Spacer(1, 10))
        for th in glossary["themes"]:
            story.append(Paragraph(th["title"], S["h2"]))
            if th.get("en"):
                story.append(Paragraph(
                    '<font name="Helvetica-Oblique">%s</font>' % th["en"], S["figsrc"]))
            if th.get("intro"):
                story.append(Paragraph(th["intro"], S["note"]))
            story.append(Spacer(1, 5))
            for t in th["terms"]:
                suffix = "" if t["en"].lower() in t["zh"].lower() else \
                    '　<font size="8.6" color="#7A858E">%s</font>' % t["en"]
                story.append(KeepTogether([
                    Paragraph("<b>%s</b>%s" % (t["zh"], suffix), S["term"]),
                    Paragraph("<b>是什么：</b>" + t["plain"], S["term_body"]),
                    Paragraph("<b>在本文中：</b>" + t["inpaper"], S["term_body"]),
                ]))
            story.append(Spacer(1, 12))

    doc.build(story)
    print(f"  已生成 {out_pdf}  {os.path.getsize(out_pdf):,} 字节")


# ============================================================================
# 6. 图内标签换中文（解决坑 5）
# ============================================================================

def annotate_figure(pdf_path, page_no, clip_rect, labels, out_path,
                    dpi=300, center=None, font=None):
    """把图内英文标签原位换成中文。

    参数
    ----
    labels : [[x, y, w, h, "中文"], ...]
        x/y/w/h 是标签在 **PDF 页面坐标** 下的外接矩形。
        图内文字是矢量轮廓，get_text() 取不到，需先用
        `list_label_boxes()` 列出候选框、再人工核对文字。
    center : [cx, cy] 环形图的圆心（可选）。给出时按「方位角 + 90°」计算切向角度。

    两个必须注意的量（都踩过坑）
    ---------------------------
    · 角度必须是 **θ + 90°**。写成 −(θ+90) 会让全部标签上下颠倒。
      验证方法：把标签裁下来逐度旋转，找出文字变水平且正读的那个角度
      （−90 正读、+90 是水平的但倒读），再与公式对照。
    · **测量 dpi 必须等于画布 dpi**。曾因测量用 600、画布用 300，
      导致中文被放大一倍、全部互相压字。
    """
    import pymupdf
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np

    font = font or os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                "Fonts", "msyhbd.ttc")
    pdf = pymupdf.open(pdf_path)
    page = pdf[page_no - 1]
    clip = pymupdf.Rect(*clip_rect)
    SC = dpi / 72.0
    pix = page.get_pixmap(clip=clip, dpi=dpi)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    def to_px(r):
        return [(r[0] - clip.x0) * SC, (r[1] - clip.y0) * SC,
                (r[0] + r[2] - clip.x0) * SC, (r[1] + r[3] - clip.y0) * SC]

    def fit(text, mw, mh):
        for s in range(64, 4, -1):
            f = ImageFont.truetype(font, s)
            bb = f.getbbox(text)
            if bb[2] - bb[0] <= mw and bb[3] - bb[1] <= mh:
                return f
        return ImageFont.truetype(font, 5)

    def ink_size(rect, ang):
        """量出英文标签在「转正角」下的墨迹像素宽高 —— 中文按此尺寸排才不会溢出"""
        x, y, w, h = rect
        p = page.get_pixmap(clip=pymupdf.Rect(x - 0.6, y - 0.6, x + w + 0.6, y + h + 0.6),
                            dpi=dpi)
        base = Image.frombytes("RGB", (p.width, p.height), p.samples)
        rot = base.rotate(ang, expand=True, fillcolor=(255, 255, 255))
        a = np.asarray(rot.convert("L")) < 200
        if not a.any():
            return 0, 0
        rr = np.where(a.any(axis=1))[0]
        cc = np.where(a.any(axis=0))[0]
        return int(cc[-1] - cc[0]), int(rr[-1] - rr[0])

    for (x, y, w, h, zh) in labels:
        if center:
            ang = math.degrees(math.atan2((y + h / 2) - center[1],
                                          (x + w / 2) - center[0])) + 90.0
            ang = (ang + 180) % 360 - 180        # 归一化到 (-180, 180]
        else:
            ang = 0.0
        box = to_px((x - 2.2, y - 1.8, w + 4.4, h + 3.6))
        ImageDraw.Draw(img).rectangle(box, fill=(255, 255, 255))
        iw, ih = ink_size((x, y, w, h), ang)
        if iw:
            mw, mh = max(iw * 1.02, 14), max(ih * 1.42, 15)
        else:
            mw, mh = (w + 10) * SC, (h + 8) * SC
        f = fit(zh, mw, mh)
        bb = f.getbbox(zh)
        pad = 6
        layer = Image.new("RGBA", (bb[2] - bb[0] + 2 * pad, bb[3] - bb[1] + 2 * pad),
                          (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((pad - bb[0], pad - bb[1]), zh, font=f,
                                   fill=(0, 0, 0, 255))
        if abs(ang) > 0.5:
            layer = layer.rotate(-ang, expand=True, resample=Image.BICUBIC)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        img.paste(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)), layer)

    img.save(out_path)
    print(f"  已生成 {out_path}  {img.size}  {os.path.getsize(out_path):,} 字节")


def list_label_boxes(pdf_path, page_no, clip_rect, min_paths=40, max_dim=95,
                     max_area=5200, gap=0.6, annotate_png=None):
    """辅助：列出图内「疑似文字标签」的候选框。

    判据（实测得出）：图内文字是**填充矢量路径**，特征是
      · 该对象包含大量路径段（一个完整标签 = 一个对象，含上百段）
      · 外接框不大（标签本身很小）
    而图形主体（如染色体的弧形色块）外接框很大，会被滤掉。

    返回 [(rect, 合并的路径对象数, 总路径段数), ...]
    """
    import pymupdf
    pdf = pymupdf.open(pdf_path)
    page = pdf[page_no - 1]
    clip = pymupdf.Rect(*clip_rect)

    items = []
    for x in page.get_drawings():
        r = x["rect"]
        if clip.contains(r):
            items.append((r, len(x["items"])))
    n = len(items)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    # 并查集：把间距 <= gap 的路径合并成一组（字形合成一个标签）
    for i in range(n):
        ri = items[i][0]
        for j in range(i + 1, n):
            rj = items[j][0]
            if (ri.x0 - gap <= rj.x1 and rj.x0 - gap <= ri.x1
                    and ri.y0 - gap <= rj.y1 and rj.y0 - gap <= ri.y1):
                ra, rb = find(i), find(j)
                if ra != rb:
                    parent[rb] = ra
    groups = collections.defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    out = []
    for ids in groups.values():
        r = pymupdf.Rect(min(items[i][0].x0 for i in ids),
                         min(items[i][0].y0 for i in ids),
                         max(items[i][0].x1 for i in ids),
                         max(items[i][0].y1 for i in ids))
        npth = sum(items[i][1] for i in ids)
        if max(r.width, r.height) <= max_dim and r.width * r.height <= max_area \
                and npth >= min_paths:
            out.append((r, len(ids), npth))
    out.sort(key=lambda z: (z[0].y0, z[0].x0))

    if annotate_png:                        # 画编号图，便于人工核对每个框是什么标签
        from PIL import Image, ImageDraw, ImageFont
        dpi = 170
        sc = dpi / 72.0
        pix = page.get_pixmap(clip=clip, dpi=dpi)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        dr = ImageDraw.Draw(im)
        fnt = ImageFont.truetype(
            os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arialbd.ttf"), 18)
        for i, (r, _, _) in enumerate(out):
            dr.rectangle([(r.x0 - clip.x0) * sc, (r.y0 - clip.y0) * sc,
                          (r.x1 - clip.x0) * sc, (r.y1 - clip.y0) * sc],
                         outline=(255, 0, 0), width=2)
            dr.text(((r.x0 - clip.x0) * sc + 2, max(0, (r.y0 - clip.y0) * sc - 20)),
                    str(i), fill=(0, 90, 255), font=fnt)
        im.save(annotate_png)
        print(f"  编号图 → {annotate_png}")
    return out


# ============================================================================
# 7. 命令行
# ============================================================================

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="pdf_bilingual",
        description="把英文 PDF 论文做成中英对照 PDF（双栏阅读顺序、字符修复、"
                    "图表英文原样保留、中英同排、术语解释页）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("extract", help="第 1 步：PDF → 结构化文本块 + 自动找图表并裁图")
    p1.add_argument("pdf")
    p1.add_argument("--out", required=True, help="工作目录（不存在会自动创建）")
    p1.add_argument("--config", help="配置文件 JSON（可选）")

    p2 = sub.add_parser("worklist", help="第 2 步：拆翻译批次 + 生成术语基准表")
    p2.add_argument("workdir")
    p2.add_argument("--batch-words", type=int, default=1500, help="每批英文词数，默认 1500")
    p2.add_argument("--config", help="配置文件 JSON（可含 title/note/terms）")

    p3 = sub.add_parser("build", help="第 4 步：译文 + 图表 + 术语 → 中英对照 PDF")
    p3.add_argument("workdir")
    p3.add_argument("--out", required=True)
    p3.add_argument("--glossary", help="术语解释 JSON")
    p3.add_argument("--figures",
                    help="图表图片清单 JSON（只用于正文插图；图表中文对照章节已删除）")
    p3.add_argument("--config", help="配置文件 JSON")

    p4 = sub.add_parser("figannot", help="附加：把图内英文标签原位换成中文")
    p4.add_argument("pdf")
    p4.add_argument("--spec", required=True, help="标注规格 JSON")
    p4.add_argument("--out", required=True, help="输出目录")

    p5 = sub.add_parser("labelboxes", help="附加：列出某张图的候选标签框（供人工核对）")
    p5.add_argument("pdf")
    p5.add_argument("--page", type=int, required=True)
    p5.add_argument("--rect", required=True, help="x0,y0,x1,y1")
    p5.add_argument("--png", help="同时输出编号图，便于目视核对")
    p5.add_argument("--max-dim", type=float, default=95)

    a = ap.parse_args(argv)
    cfg = {}
    if getattr(a, "config", None) and os.path.exists(a.config):
        cfg = json.load(open(a.config, encoding="utf-8"))

    if a.cmd == "extract":
        os.makedirs(a.out, exist_ok=True)
        verbose = cfg.get("verbose", True)
        mid = cfg.get("mid_x", MID_X)

        print("[1/5] 提取文本块 …")
        blocks, pdf = extract_blocks(a.pdf, cfg.get("repair"))
        print(f"  共 {len(blocks)} 块")
        rep = detect_repeated_bands(blocks, cfg.get("min_repeat_pages", 3), verbose)

        def assign_ids(rs):
            for i, r in enumerate(rs):
                r["id"] = "B%03d" % (i + 1)
            return rs

        # 粗分类（此时还不知道表格范围）→ 识别图表 → 用表格范围重分类
        recs = assign_ids(classify(blocks, rep, cfg.get("tables", []), mid))

        print("[2/5] 识别图表 …")
        figs = detect_figures(pdf, recs, verbose)
        table_rects = cfg.get("tables") or detect_tables(figs, verbose)
        if table_rects:
            recs = assign_ids(classify(blocks, rep, table_rects, mid))

        print("[3/5] 标出参考文献（按阅读顺序）…")
        mark_references(recs, verbose)

        print("[4/5] 裁图 …")
        crop_figures(pdf, figs, os.path.join(a.out, "fig"), cfg.get("fig_dpi", 300), verbose)

        print("[5/5] 写出结果 …")
        with open(os.path.join(a.out, "doc_model.json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump(recs, fh, ensure_ascii=False, indent=1)
        with open(os.path.join(a.out, "figures_auto.json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump(figs, fh, ensure_ascii=False, indent=1)
        n_tr = sum(1 for r in recs if r["action"] == "translate")
        n_rf = sum(1 for r in recs if r["action"] == "ref")
        n_sk = sum(1 for r in recs if r["action"] == "skip")
        print(f"\n  要翻译 {n_tr} 块 / 保留英文 {n_rf} 块 / 丢弃 {n_sk} 块")
        print(f"  → {os.path.join(a.out, 'doc_model.json')}")

    elif a.cmd == "worklist":
        cmd_worklist(a.workdir, a.batch_words, cfg.get("title", "(未填写)"),
                     cfg.get("note", "(未填写)"),
                     [tuple(x) for x in cfg.get("terms", [])])

    elif a.cmd == "build":
        g = json.load(open(a.glossary, encoding="utf-8")) if a.glossary else None
        f = json.load(open(a.figures, encoding="utf-8")) if a.figures else None
        auto = os.path.join(a.workdir, "figures_auto.json")
        if os.path.exists(auto):
            auto_figs = json.load(open(auto, encoding="utf-8"))
            by_key = {x["key"]: x for x in auto_figs}
            if f is None:
                f = {"figures": []}
            f.setdefault("figures", [])
            have = {fg.get("key") for fg in f["figures"]}
            for fg in f["figures"]:                       # 补图片路径
                if not fg.get("image"):
                    fg["image"] = by_key.get(fg.get("key"), {}).get("image")
            for k, v in by_key.items():                   # 补用户清单里没写的图
                if k not in have:
                    print(f"  提示：图表清单里没有 {k}，已自动补入（仅用于正文插图）")
                    f["figures"].append({"key": k, "page": v["page"],
                                         "image": v.get("image"),
                                         "zh_title": k, "elements": []})
        cmd_build(a.workdir, a.out, glossary=g, figures=f,
                  species=cfg.get("species"), title_zh=cfg.get("title_zh", ""),
                  title_en=cfg.get("title_en", ""), source_line=cfg.get("source_line", ""),
                  cover_note=cfg.get("cover_note", ""), mid_x=cfg.get("mid_x", MID_X))

    elif a.cmd == "figannot":
        spec = json.load(open(a.spec, encoding="utf-8"))
        os.makedirs(a.out, exist_ok=True)
        for it in spec["figures"]:
            annotate_figure(a.pdf, it["page"], it["rect"], it["labels"],
                            os.path.join(a.out, it["name"]), center=it.get("center"))

    elif a.cmd == "labelboxes":
        r = [float(v) for v in a.rect.split(",")]
        boxes = list_label_boxes(a.pdf, a.page, r, max_dim=a.max_dim,
                                 annotate_png=a.png)
        print(f"\n共 {len(boxes)} 个候选标签框（请对照编号图逐一确认文字）：")
        for i, (bb, ng, npth) in enumerate(boxes):
            print(f"  [{i:2d}] x={bb.x0:6.1f} y={bb.y0:6.1f} "
                  f"w={bb.width:5.1f} h={bb.height:5.1f} 路径段={npth}")


if __name__ == "__main__":
    main()
