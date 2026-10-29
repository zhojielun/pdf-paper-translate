#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_translate.py  ——  版式保留的 PDF 译文重建器（通用版）
========================================================

把「原文 PDF + 逐块译文」重建成「与原文逐页 1:1 对齐的译文 PDF」：
原文只当画布——逐块 redact 掉被翻译过的文字（images / graphics 设 NONE，
只删文字），图、公式、表格、参考文献原样保留；再在同一个 bbox、同一条
行网格上用 insert_htmlbox 重排译文。

三个子命令
----------
    diagnose  只做版面诊断（逐页 CJK 占比 / block 数）并打印结论
    extract   导出可翻译块清单（含 key/page/bbox/size/bold/text/提示），
              供人工或模型翻译；同时给出 fig_regions / table_bands 等建议
    render    读入 translations.json，重建并写出译文 PDF

translations.json 结构
----------------------
{
  "params": { "body_ratio":0.98, "l0":1.28, "lmax":1.72, "indent_em":2.0,
              "head_size":9.6, "title_size":16.5, "hmin":10.5, "alpha":0.35 },
  "layout": {
     "keep_ids":["p0:b1"],            # 不翻译、不 redact（作者、水印、宽公式）
     "no_indent":["p0:b11"],          # 跨块/跨页续行 → 不首行缩进
     "widen":{"p2:b40":298.2},        # 单行短块右边无内容 → 放宽右边界
     "affil_ids":["p0:b2","p0:b3"],   # 作者单位块（按行居中替换）
     "table_bands":{"4":[[112,336]]}, # 1-based 页号 → 保留原表的 y 带
     "fig_regions":{"2":[0,266], "3":[325,547,437,582]},
                                      # 1-based 页号 → [y_max] 或 [x0,x1,y0,y1]
     "references_pages":[5],          # 原样保留的参考文献页
     "references_heading_ids":["p4:b0"],
     "title_ids":["p0:b0"],
     "et_al_pages":"auto",            # 保留表格/图内 et al.→等人（默认 auto；[] 关闭）
     "caption_regex":"^(?:Fig\\\\.|Figure|Table)\\\\s*\\\\d+(?:[-–]\\\\d+)?[.:]\\\\s",
     "heading_regex":"^\\\\d+(?:\\\\.\\\\d+)*\\\\.?\\\\s+\\\\S",
     "abstract_headings":["ABSTRACT"],
     "keywords_prefixes":["Index Terms"]
  },
  "blocks":        { "p0:b0": "译文（可含 <b>/<sub>/<sup>）", ... },
  "figure_labels": { "xx": "xx", ... },
  "affiliations":  { "xx": "xx" }
}

用法
----
    python3 pdf_translate.py diagnose SRC.pdf
    python3 pdf_translate.py extract  SRC.pdf --out work/blocks.json
    python3 pdf_translate.py render   SRC.pdf work/translations.json --out OUT-zh.pdf
"""
from __future__ import annotations

import argparse
import json
import os
import re
import string
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field

import pymupdf

# ---------------------------------------------------------------- 常量
LANG_PUNCT = "，。、；：“”‘’（）《》？！—…·×→≤≥∈∥⊙φψτℓ–★"
MATH_CHARS = set("ˆ⊙ϕψτ∥∈×−≤≥∑ℓ√∂")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
ET_AL_RE = re.compile(r"et\s+al\.?", re.I)
CJK_RANGE = "\u2e80-\u9fff\uf900-\ufaff\ufe30-\ufe4f\u3000-\u303f\uff00-\uffef"

DEFAULTS = {
    "params": {
        "body_ratio": 0.98, "l0": 1.28, "lmax": 1.72, "indent_em": 2.0,
        "head_size": 9.6, "title_size": 16.5, "hmin": 10.5, "alpha": 0.35,
        "min_head_size": 6.8,
    },
    "layout": {
        "keep_ids": [], "no_indent": [], "widen": {}, "affil_ids": [],
        "table_bands": {}, "fig_regions": {}, "references_pages": [],
        "references_heading_ids": [], "title_ids": [],
        "et_al_pages": "auto",
        "caption_regex": r"^(?:Fig\.|Figure|Table)\s*\d+(?:[-–]\d+)?[.:]\s",
        "heading_regex": r"^\d+(?:\.\d+)*\.?\s+\S",
        "abstract_headings": ["ABSTRACT"],
        "keywords_prefixes": ["Index Terms"],
    },
}

CFG: dict = {}
BLOCKS: dict[str, str] = {}
FIG: dict[str, str] = {}
AFFIL: dict[str, str] = {}
_PW = 612.0        # 当前页宽，供 col_bounds 使用


def P(key: str):
    return CFG["params"][key]


def LAY(key: str, default=None):
    return CFG["layout"].get(key, default)


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    cfg = {"params": dict(DEFAULTS["params"]), "layout": dict(DEFAULTS["layout"])}
    cfg["params"].update(data.get("params", {}))
    cfg["layout"].update(data.get("layout", {}))
    return cfg, data


# ---------------------------------------------------------------- 文本工具
def cjk_ratio(text: str) -> float:
    t = re.sub(r"\s+", "", text)
    if not t:
        return 0.0
    return len(CJK_RE.findall(t)) / len(t)


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def line_text(line: dict) -> str:
    return "".join(s["text"] for s in line["spans"])


def block_text(block: dict) -> str:
    """把按行折断的原文接回来，并处理行末连字符。"""
    out = ""
    for line in block["lines"]:
        t = line_text(line).rstrip()
        if not out:
            out = t
        elif t[:1].islower():
            out = (out[:-1] if out.endswith("-") else out + " ") + t
        else:
            out = (out[:-1] if out.endswith("-") else out + " ") + t
    return norm_ws(out)


def span_bold(span: dict) -> bool:
    if span.get("flags", 0) & 16:
        return True
    f = span.get("font", "")
    return ("Bold" in f) or ("Medi" in f)


def block_bold(block: dict) -> bool:
    return span_bold(block["lines"][0]["spans"][0])


# ---------------------------------------------------------------- 引用清洗
def clean_citations(text: str) -> str:
    """按顺序清洗引用痕迹（NFC → et al → 括号引用 → 悬挂碎片 → 句首年份）。"""
    t = unicodedata.normalize("NFC", text)

    t = re.sub(
        r"[A-Z][A-Za-z\-']+(?:\s+(?:and|&)\s+[A-Z][A-Za-z\-']+)?\s+et\s+al\.?\s*"
        r"[（(]\s*(?:19|20)\d{2}[a-z]?\s*[)）]", "该文献", t)
    t = re.sub(r"[A-Z][A-Za-z\-']*(?:\s+[A-Z][A-Za-z\-']*)?\s+et\s+al\.?\s*[,，;；]?\s*",
               "", t)

    def _drop(m):
        inner = m.group(1)
        if re.search(r"et\s+al", inner) or re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", inner):
            return " "
        return m.group(0)

    t = re.sub(r"[（(]([^（）()]{1,120})[)）]", _drop, t)
    t = re.sub(r"[（(]\s*[)）]", " ", t)
    t = re.sub(r"(?<=[。；！？\s])[（(]\s*$", "", t)
    t = re.sub(r"^\s*(?:19|20)\d{2}[a-z]?\s*[)）]\s*", "", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


def tighten(text: str) -> str:
    """去掉中西文之间的空格：两端对齐只会拉伸空格，一个空格能被拉到几个汉字宽。"""
    t = re.sub(rf"(?<=[{CJK_RANGE}])\s+|(?<!•)(?<!·)\s+(?=[{CJK_RANGE}])", "", text)
    return re.sub(r"\s+(?=[\[（(])|(?<=[\]）)])\s+", "", t)


# ---------------------------------------------------------------- 字体
def _cmap(path: str) -> set[int]:
    from fontTools.ttLib import TTFont
    f = TTFont(path, lazy=True)
    out: set[int] = set()
    for t in f["cmap"].tables:
        out |= set(t.cmap.keys())
    return out


def _strip_locl(path: str) -> None:
    """去掉 GSUB 的 locl 特性：MuPDF 会把紧跟拉丁字母的数字换成无 ToUnicode
    映射的本地化字形，复制/检索得到乱码；去掉后才用普通数字字形。"""
    from fontTools.ttLib import TTFont
    f = TTFont(path)
    try:
        gsub = f["GSUB"].table
    except KeyError:
        return
    idx = {i for i, fr in enumerate(gsub.FeatureList.FeatureRecord)
           if fr.FeatureTag == "locl"}
    if not idx:
        return
    for fr in gsub.FeatureList.FeatureRecord:
        if fr.FeatureTag == "locl":
            fr.Feature.LookupListIndex = []
            fr.Feature.LookupCount = 0
    for sr in gsub.ScriptList.ScriptRecord:
        systems = [sr.Script.DefaultLangSys] if sr.Script.DefaultLangSys else []
        systems += [lr.LangSys for lr in sr.Script.LangSysRecord]
        for ls in systems:
            if ls is None:
                continue
            ls.FeatureIndex = [i for i in ls.FeatureIndex if i not in idx]
            ls.FeatureCount = len(ls.FeatureIndex)
    f.save(path)


def build_subset_fonts(chars: set[str], paths: dict, verbose: bool = True) -> None:
    """pyftsubset 子集化（CFF/OTF）→ otf2ttf 转 TTF。"""
    build = paths["build"]
    os.makedirs(build, exist_ok=True)
    cov = _cmap(paths["reg_otf"])
    missing = sorted(c for c in chars if ord(c) not in cov)
    if missing:
        sys.exit("字体缺字（会渲染成方块）：" + " ".join(f"{c!r}(U+{ord(c):04X})"
                                                   for c in missing))
    txt = os.path.join(build, "chars.txt")
    with open(txt, "w", encoding="utf-8") as fh:
        fh.write("".join(sorted(chars)))
    for otf, ttf in ((paths["reg_otf"], paths["reg_ttf"]),
                     (paths["bold_otf"], paths["bold_ttf"])):
        if not os.path.exists(otf):
            sys.exit(f"缺少字体文件：{otf}\n请先准备 Noto Serif CJK SC（见 SKILL.md）")
        sub = ttf.replace(".ttf", ".otf")
        if (not os.path.exists(ttf)
                or os.path.getmtime(ttf) < max(os.path.getmtime(otf), os.path.getmtime(txt))):
            subprocess.run(
                ["pyftsubset", otf, f"--text-file={txt}", f"--output-file={sub}",
                 "--layout-features=*", "--no-hinting", "--desubroutinize",
                 "--drop-tables+=DSIG"], check=True)
            subprocess.run(["otf2ttf", sub, "-o", ttf], check=True)
            _strip_locl(ttf)
            if verbose:
                print(f"  [font] {os.path.basename(otf)} → 子集 OTF "
                      f"{os.path.getsize(sub) // 1024}KB → TTF {os.path.getsize(ttf) // 1024}KB")


# ---------------------------------------------------------------- 诊断
@dataclass
class PageInfo:
    no: int
    text: str
    ratio: float
    nblocks: int
    role: str = ""


def diagnose(doc: pymupdf.Document, verbose: bool = True) -> list[PageInfo]:
    infos = []
    for p in doc:
        t = p.get_text()
        infos.append(PageInfo(p.number, t, cjk_ratio(t), len(p.get_text("blocks"))))
    for i in infos:
        i.role = "译文页" if i.ratio > 0.25 else "原文页"

    if verbose:
        print("=" * 78)
        print("诊断：逐页 CJK 占比 / text block 数量 / 版面")
        print("=" * 78)
        for i, p in zip(infos, doc):
            print(f"  p{i.no + 1}: {p.rect.width:.0f}×{p.rect.height:.0f}  "
                  f"CJK {i.ratio * 100:5.1f}%  blocks {i.nblocks:3d}  → {i.role}")

        roles = [i.role for i in infos]
        alternating = len(set(roles)) > 1 and all(
            roles[i] != roles[i + 1] for i in range(len(roles) - 1))
        print(f"  页序：{' / '.join(roles)}")
        print(f"  「原文页 / 译文页」交替：{'是' if alternating else '否'}")
        if alternating:
            print("  → 双语交替：可用 align_blocks 做块级配对（本脚本按单语重建）")
        else:
            print("  → 单语原文：按 1:1 逐页翻译，块坐标直接取自原文页")
        print()
    return infos


def align_blocks(pa: pymupdf.Page, pb: pymupdf.Page, verbose: bool = False):
    """原文页 ↔ 译文页的块级配对（块数相等按 (round(y0/k), x0) 顺序配对；
    否则单调 DP，代价 = |Δy0| + α|Δ高度|，跳过惩罚略大于行高）。"""
    A = [b for b in pa.get_text("dict")["blocks"] if b["type"] == 0]
    B = [b for b in pb.get_text("dict")["blocks"] if b["type"] == 0]
    pairs = []
    if len(A) == len(B):
        k = 2
        key = lambda b: (round(b["bbox"][1] / k), b["bbox"][0])
        pairs = list(zip(sorted(A, key=key), sorted(B, key=key)))
        mode = "(round(y0/k), x0) 顺序配对"
    else:
        n, m = len(A), len(B)
        line_h = min((b["bbox"][3] - b["bbox"][1]) for b in A + B) or 10.0
        skip = 1.2 * line_h
        INF = float("inf")
        cost = [[INF] * (m + 1) for _ in range(n + 1)]
        back = [[None] * (m + 1) for _ in range(n + 1)]
        cost[0][0] = 0.0
        for i in range(n + 1):
            for j in range(m + 1):
                c = cost[i][j]
                if c == INF:
                    continue
                if i < n and c + skip < cost[i + 1][j]:
                    cost[i + 1][j] = c + skip
                    back[i + 1][j] = (i, j, "skipA")
                if j < m and c + skip < cost[i][j + 1]:
                    cost[i][j + 1] = c + skip
                    back[i][j + 1] = (i, j, "skipB")
                if i < n and j < m:
                    ay0, ah = A[i]["bbox"][1], A[i]["bbox"][3] - A[i]["bbox"][1]
                    by0, bh = B[j]["bbox"][1], B[j]["bbox"][3] - B[j]["bbox"][1]
                    d = abs(ay0 - by0) + P("alpha") * abs(ah - bh)
                    if c + d < cost[i + 1][j + 1]:
                        cost[i + 1][j + 1] = c + d
                        back[i + 1][j + 1] = (i, j, "pair")
        i, j = n, m
        while i or j:
            pi, pj, act = back[i][j]
            if act == "pair":
                pairs.append((A[pi], B[pj]))
            i, j = pi, pj
        pairs.reverse()
        mode = "单调 DP（|Δy0| + α|Δ高|，skip = 1.2×行高）"
    if verbose:
        print(f"    配对 p{pa.number + 1}↔p{pb.number + 1}: {len(A)}/{len(B)} 块，"
              f"{mode} → {len(pairs)} 对")
    return pairs


# ---------------------------------------------------------------- 分类
@dataclass
class Item:
    key: str
    kind: str                 # body / heading / title / caption / affil
    rect: pymupdf.Rect
    src: str
    size: float
    bold: bool
    ids: list[str] = field(default_factory=list)
    align: int = 0
    base: float = 0.0
    x_first: float = 0.0
    hang: float = 0.0
    extra: dict = field(default_factory=dict)


def col_bounds(rect: pymupdf.Rect):
    """两栏版面：左栏 [54.4, 298.2]、右栏 [315.2, 559.0]（以 612pt 页宽为基准等比缩放）。"""
    s = _PW / 612.0
    if rect.x1 <= 306.5 * s:
        return 54.4 * s, 298.2 * s
    if rect.x0 >= 306.5 * s:
        return 315.2 * s, 559.0 * s
    return 54.4 * s, 559.0 * s


def centered(rect: pymupdf.Rect) -> int:
    """按原始块的水平中心判断标题是否居中（顶格编号标题也照此保留）。"""
    l, r = col_bounds(rect)
    return 1 if abs((rect.x0 + rect.x1) / 2 - (l + r) / 2) <= 2.5 else 0


def _fig_region(b, pno):
    reg = LAY("fig_regions").get(str(pno + 1))
    if not reg:
        return False
    if len(reg) == 1:
        return b["bbox"][3] <= reg[0]
    if len(reg) == 2:
        return b["bbox"][3] <= reg[1]
    x0, x1, y0, y1 = reg
    cx = (b["bbox"][0] + b["bbox"][2]) / 2
    return x0 <= cx <= x1 and y0 <= b["bbox"][1] <= y1


def _in_table_band(b, pno):
    cy = (b["bbox"][1] + b["bbox"][3]) / 2
    return any(y0 <= cy <= y1 for y0, y1 in LAY("table_bands").get(str(pno + 1), []))


def _find_formulas(blocks, col_left):
    core = []
    for b in blocks:
        txt = block_text(b)
        h = b["bbox"][3] - b["bbox"][1]
        has_math = any(c in MATH_CHARS for c in txt) or any(
            ("CMMI" in s["font"] or "CMSY" in s["font"])
            for l in b["lines"] for s in l["spans"])
        words = re.findall(r"\b[a-z]{3,}\b", txt)
        if (b["bbox"][0] > col_left + 20 and h < 14 and len(words) <= 3
                and (has_math or "=" in txt or len(txt) <= 3)):
            core.append(b)
    return core


def _is_formula(b, core):
    if b in core:
        return True
    y0, y1 = b["bbox"][1], b["bbox"][3]
    x0, x1 = b["bbox"][0], b["bbox"][2]
    h = max(y1 - y0, 1e-6)
    for f in core:
        hx = min(x1, f["bbox"][2]) - max(x0, f["bbox"][0])
        if hx <= 0:
            continue
        ov = max(0.0, min(y1, f["bbox"][3]) - max(y0, f["bbox"][1]))
        if ov / h > 0.5:
            return True
    return False


def _hint(b, pno, blocks, core, col_left, regions=None):
    """给 extract 用的粗分类提示。"""
    txt = block_text(b)
    rect = pymupdf.Rect(b["bbox"])
    size = max(s["size"] for l in b["lines"] for s in l["spans"])
    bold = block_bold(b)
    if _fig_region(b, pno) or (regions and _in_region(b["bbox"], regions)):
        return "figure"
    if (pno + 1) in LAY("references_pages"):
        return "reference"
    if _in_table_band(b, pno):
        return "table"
    if re.match(LAY("caption_regex"), txt):
        return "caption"
    if _is_formula(b, core):
        return "formula"
    if f"p{pno}:b{b['number']}" in LAY("title_ids"):
        return "title"
    italic = ("Ital" in b["lines"][0]["spans"][0]["font"])
    if (len(b["lines"]) == 1 and rect.height <= 20 and (bold or italic)
            and (re.match(LAY("heading_regex"), txt)
                 or txt in LAY("abstract_headings")
                 or any(txt.startswith(p) for p in LAY("keywords_prefixes")))):
        return "heading"
    return "body"


def classify_page(page: pymupdf.Page):
    global _PW
    pno = page.number
    _PW = page.rect.width
    all_blocks = page.get_text("dict")["blocks"]
    blocks = [b for b in all_blocks if b["type"] == 0]
    figs: list[dict] = []
    items: list[Item] = []
    col_left = min((b["bbox"][0] for b in blocks if b["bbox"][0] > 40), default=54.4)
    core_formula = _find_formulas([b for b in blocks
                                   if not _fig_region(b, pno)], col_left)

    for b in blocks:
        key = f"p{pno}:b{b['number']}"
        txt = block_text(b)
        rect = pymupdf.Rect(b["bbox"])
        size = max(s["size"] for l in b["lines"] for s in l["spans"])
        bold = block_bold(b)

        if _fig_region(b, pno):                 # 图内标签：逐行
            for line in b["lines"]:
                t = line_text(line).strip()
                if t in FIG:
                    figs.append({"text": t, "line": line,
                                 "rect": pymupdf.Rect(line["bbox"])})
            continue
        if (pno + 1) in LAY("references_pages"):
            if key in LAY("references_heading_ids"):
                items.append(Item(key, "heading", rect, txt, size, bold, [key], align=1))
            continue                            # 参考文献原样保留
        if _in_table_band(b, pno):
            continue                            # 表格原样保留
        if key in LAY("keep_ids"):
            continue
        if key in LAY("affil_ids"):
            lines = []
            for line in b["lines"]:
                t = line_text(line).strip()
                if t in AFFIL:
                    sp = line["spans"][0]
                    lines.append({"text": t, "oy": sp["origin"][1],
                                  "cx": (line["bbox"][0] + line["bbox"][2]) / 2,
                                  "size": max(s["size"] for s in line["spans"])})
            items.append(Item(key, "affil", rect, txt, size, bold, [key],
                              extra={"lines": lines}))
            continue
        if re.match(LAY("caption_regex"), txt):
            items.append(Item(key, "caption", rect, txt, size, bold, [key],
                              base=b["lines"][0]["spans"][0]["origin"][1]))
            continue
        if _is_formula(b, core_formula):
            continue                            # 公式原样保留
        if key in LAY("title_ids"):
            items.append(Item(key, "title", rect, txt, size, bold, [key], align=1))
            continue
        italic = ("Ital" in b["lines"][0]["spans"][0]["font"])
        if (len(b["lines"]) == 1 and rect.height <= 20 and (bold or italic)
                and (re.match(LAY("heading_regex"), txt)
                     or txt in LAY("abstract_headings")
                     or any(txt.startswith(p) for p in LAY("keywords_prefixes")))):
            items.append(Item(key, "heading", rect, txt, size, bold, [key],
                              align=centered(rect)))
            continue

        items.append(Item(key, "body", rect, txt, size, bold, [key],
                          base=b["lines"][0]["spans"][0]["origin"][1],
                          x_first=b["lines"][0]["bbox"][0]))

    merged: list[Item] = []
    for it in sorted(items, key=lambda x: (x.rect.y0, x.rect.x0)):
        if it.kind == "body":
            for m in merged:
                if m.kind != "body":
                    continue
                hx = min(it.rect.x1, m.rect.x1) - max(it.rect.x0, m.rect.x0)
                if hx <= 0:
                    continue
                ov = min(it.rect.y1, m.rect.y1) - max(it.rect.y0, m.rect.y0)
                if ov > 0.3 * min(it.rect.height, m.rect.height):
                    m.rect |= it.rect
                    m.ids += it.ids
                    m.src += " " + it.src
                    break
            else:
                merged.append(it)
        else:
            merged.append(it)
    uniq, seen = [], set()
    for f in figs:
        b = f["line"]["bbox"]
        k = (f["text"], round((b[0] + b[2]) / 2, 1), round(b[3], 1))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(f)
    return merged, uniq


# ---------------------------------------------------------------- 排版
CSS_TMPL = """@font-face {{font-family: "NSSC"; src: url({reg});}}
@font-face {{font-family: "NSSC"; font-weight: bold; src: url({bold});}}
body {{font-family: "NSSC"; font-size: {size:.2f}pt; line-height: {lh:.3f};
       text-align: {align}; margin: 0; hyphens: none;}}
p {{margin: 0; text-indent: {indent:.2f}em;}}
sub, sup {{font-size: 62%;}}
"""


def make_css(paths, size, lh, indent, align="justify", hang=0.0) -> str:
    css = CSS_TMPL.format(reg=paths["reg_ttf"], bold=paths["bold_ttf"], size=size,
                          lh=lh, indent=indent, align=align)
    if hang:
        css += f"p{{padding-left:{hang:.2f}em; text-indent:-{hang:.2f}em;}}\n"
    return css


class Renderer:
    def __init__(self, paths: dict):
        self.paths = paths
        self.scratch = pymupdf.open()
        self.sp = self.scratch.new_page(width=1200, height=1200)
        self.freg = pymupdf.Font(fontfile=paths["reg_ttf"])
        self.fbold = pymupdf.Font(fontfile=paths["bold_ttf"])
        self.stats = []

    def measure(self, rect, html, css):
        return self.sp.insert_htmlbox(rect, html, css=css, scale_low=1)

    def fit(self, rect, html, size, indent, hang=0.0, align="justify"):
        """L0 试排 → 由 spare 反推行数 → 把块高填满（≤ LMAX）。"""
        spare, _ = self.measure(rect, html, make_css(self.paths, size, P("l0"), indent,
                                                     hang=hang, align=align))
        if spare < 0:                                    # L0 放不下 → 缩字号
            while spare < 0 and size > P("min_head_size"):
                size = round(size - 0.15, 2)
                spare, _ = self.measure(rect, html, make_css(self.paths, size, P("l0"),
                                                             indent, hang=hang, align=align))
        lh = P("l0")
        if spare >= 0:
            used = rect.height - spare
            n = max(1, int(round(used / (size * P("l0")))))
            lh = min(P("lmax"), max(P("l0"), rect.height / (n * size)))
            for _ in range(3):
                spare2, _ = self.measure(rect, html, make_css(self.paths, size, lh, indent,
                                                              hang=hang, align=align))
                if spare2 < 0:
                    lh = max(P("l0"), lh * 0.97)
                    break
                n2 = max(1, int(round((rect.height - spare2) / (size * lh))))
                lh2 = min(P("lmax"), max(P("l0"), rect.height / (n2 * size)))
                if abs(lh2 - lh) < 0.004:
                    lh = lh2
                    break
                lh = lh2
        return size, lh

    def draw_para(self, page, rect, html, size, indent, hang=0.0, base=None,
                  align="justify"):
        if rect.height < size * 1.75:            # 单行块：补足行盒高度
            rect = pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + size * 1.75)
        size, lh = self.fit(rect, html, size, indent, hang, align)
        if base:
            # htmlbox 首行基线 = y0 + size×(0.3 + lh/2)；平移矩形让基线回到原文位置
            dy = base - (rect.y0 + size * (0.3 + lh / 2))
            rect = pymupdf.Rect(rect.x0, rect.y0 + dy, rect.x1, rect.y1 + dy)
        spare, scale = page.insert_htmlbox(
            rect, html, css=make_css(self.paths, size, lh, indent, hang=hang, align=align),
            scale_low=1)
        fill = 1.0 - max(spare, 0) / rect.height
        self.stats.append({"page": page.number + 1, "rect": rect, "size": size,
                           "lh": lh, "spare": spare, "fill": fill})
        if spare < 0:
            print(f"     !! 溢出 p{page.number + 1} {rect} spare={spare}")
        return spare

    def draw_heading(self, page, text, rect, size, align, col):
        x0, x1 = col
        baseline = rect.y1 - 1.2
        s = size
        while s > 6:
            # insert_textbox: 首行基线 = rect.y0 + 1.151×字号；rect 高 ≈ 1.95×字号
            r = pymupdf.Rect(x0, baseline - 1.151 * s, x1,
                             baseline - 1.151 * s + 1.95 * s)
            rc = page.insert_textbox(r, text, fontsize=s, fontname="zhB",
                                     fontfile=self.paths["bold_ttf"], align=align,
                                     color=(0, 0, 0))
            if rc >= 0:
                return s
            s -= 0.4
        print(f"     !! 标题放不下：{text}")
        return s

    def draw_fig(self, page, rec):
        line, zh = rec["line"], rec["zh"]
        s0 = max(sp["size"] for sp in line["spans"])
        color = line["spans"][0]["color"]
        rgb = (((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255)
        bold = any(span_bold(sp) for sp in line["spans"])
        fnt = self.fbold if bold else self.freg
        name, ffile = ("zhB", self.paths["bold_ttf"]) if bold else ("zhR",
                                                                    self.paths["reg_ttf"])
        _, dy = line["dir"]
        ox, oy = line["spans"][0]["origin"]
        n = max(len(zh), 1)
        if abs(dy) > 0.5:                                  # 竖排标签
            s = max(1.6, min(s0 * 0.95, (line["bbox"][3] - line["bbox"][1]) / n))
            page.insert_text((ox, oy), zh, fontsize=s, fontname=name, fontfile=ffile,
                             color=rgb, rotate=90 if dy < 0 else 270)
            return
        w = line["bbox"][2] - line["bbox"][0]
        s = max(1.6, min(s0 * 0.98, w / n))
        tw = fnt.text_length(zh, fontsize=s)
        cx = (line["bbox"][0] + line["bbox"][2]) / 2
        page.insert_text((cx - tw / 2, oy), zh, fontsize=s, fontname=name,
                         fontfile=ffile, color=rgb)


# ---------------------------------------------------------------- 字体路径
def replace_et_al(doc, paths: dict, verbose: bool = True) -> int:
    """Replace residual `et al.` outside references pages in place, preserving layout.

    This catches citations left inside kept tables/figure regions (and any that escaped
    clean_citations), so no per-page text-replace patch is needed.
    """
    setting = LAY("et_al_pages", "auto")
    if setting in (None, False, "", []):
        return 0
    refs = {int(p) for p in LAY("references_pages", [])}
    only = None if setting == "auto" else {int(p) for p in setting}
    total = 0
    for page in doc:
        pno = page.number + 1
        if pno in refs or (only is not None and pno not in only):
            continue
        spans = []
        for b in page.get_text("dict").get("blocks", []):
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    spans.append((span, line.get("dir", (1, 0))))
        rects = page.search_for("et al.")
        if not rects:
            rects = page.search_for("et al")
        hits = []
        for r in rects:
            # The phrase may span multiple spans (e.g. italic "al."), so pick the
            # span whose box overlaps the hit and is closest vertically.
            best, best_dy = None, 1e9
            for span, d in spans:
                sb = pymupdf.Rect(span["bbox"])
                if sb.x1 < r.x0 - 2 or sb.x0 > r.x1 + 2:
                    continue
                if sb.y1 < r.y0 - 3 or sb.y0 > r.y1 + 3:
                    continue
                dy = abs((sb.y0 + sb.y1) / 2 - (r.y0 + r.y1) / 2)
                if dy < best_dy:
                    best, best_dy = (span, d), dy
            if best is None:
                continue
            hits.append((r, best[0], best[1]))
        if not hits:
            continue
        uniq, seen = [], set()
        for r, span, d in hits:
            k = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
            if k in seen:
                continue
            seen.add(k)
            uniq.append((r, span, d))
        for r, _, _ in uniq:
            page.add_redact_annot(
                pymupdf.Rect(r.x0, r.y0 - 1.0, r.x1 + 0.8, r.y1 + 1.0),
                fill=None, cross_out=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        for r, span, d in uniq:
            size = float(span["size"])
            color = int(span.get("color", 0))
            rgb = (((color >> 16) & 255) / 255,
                   ((color >> 8) & 255) / 255, (color & 255) / 255)
            bold = span_bold(span)
            name = "zhB" if bold else "zhR"
            ffile = paths["bold_ttf"] if bold else paths["reg_ttf"]
            _, dy = d if len(d) == 2 else (1, 0)
            if abs(dy) > 0.5:
                page.insert_text((r.x0, r.y1), "等人", fontsize=size, fontname=name,
                                 fontfile=ffile, color=rgb,
                                 rotate=90 if dy < 0 else 270)
            else:
                page.insert_text((r.x0, span["origin"][1]), "等人", fontsize=size,
                                 fontname=name, fontfile=ffile, color=rgb)
            total += 1
    if total and verbose:
        print(f"  et al → 等人：{total} 处")
    return total


def font_paths(args) -> dict:
    fonts = args.fonts or os.path.join(os.path.dirname(os.path.abspath(args.src)), "fonts")
    build = args.work or os.path.join(os.path.dirname(os.path.abspath(args.out or args.src)),
                                      "build")
    return {
        "reg_otf": os.path.join(fonts, "NotoSerifCJKsc-Regular.otf"),
        "bold_otf": os.path.join(fonts, "NotoSerifCJKsc-Bold.otf"),
        "reg_ttf": os.path.join(build, "NotoSerifSC-sub.ttf"),
        "bold_ttf": os.path.join(build, "NotoSerifSC-Bold-sub.ttf"),
        "build": build,
    }


# ---------------------------------------------------------------- extract
def _merge_boxes(boxes, pad=6.0):
    """把碎片化的 image bbox 合并成若干不相交的整块区域。"""
    rects = [pymupdf.Rect(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        out = []
        while rects:
            r = rects.pop()
            for i, o in enumerate(out):
                rr = pymupdf.Rect(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad)
                if rr.intersects(o):
                    out[i] = o | r
                    merged = True
                    break
            else:
                out.append(r)
        rects = out
    return [r for r in rects if r.width > 8 and r.height > 8]


def _compact_payload(data: dict) -> dict:
    """Remove coordinates and kept/translated-irrelevant blocks for low-token reading."""
    layout = data["suggested_layout"]
    refs = set(layout.get("references_pages", []))
    title_ids = set(layout.get("title_ids", []))
    pages: dict[str, list] = {}
    for b in data["blocks"]:
        p = b["page"]
        if p in refs or b["hint"] == "formula":
            continue
        x0, y0, x1, y1 = b["bbox"]
        cy = (y0 + y1) / 2
        if any(y0_ <= cy <= y1_ for y0_, y1_ in
               layout.get("table_bands", {}).get(str(p), [])):
            continue
        if any(r[0] <= (x0 + x1) / 2 <= r[2] and r[1] <= cy <= r[3]
               for r in layout.get("fig_regions", {}).get(str(p), [])):
            continue
        pages.setdefault(str(p), []).append({
            "key": b["key"], "size": b["size"], "bold": b["bold"],
            "hint": "title" if b["key"] in title_ids else b["hint"],
            "text": b["text"],
        })
    figs: dict[str, list] = {}
    seen: set[tuple[str, int]] = set()
    for c in data["figure_label_candidates"]:
        k = (c["text"], c["page"])
        if k in seen:
            continue
        seen.add(k)
        figs.setdefault(str(c["page"]), []).append(
            {"text": c["text"], "size": c["size"]})
    return {"pages": pages, "figure_labels": figs, "suggested_layout": layout}


def cmd_extract(args):
    doc = pymupdf.open(args.src)
    data = {
        "pages": [], "blocks": [], "figure_label_candidates": [],
        "suggested_layout": {"fig_regions": {}, "table_bands": {},
                             "references_pages": [], "title_ids": []},
    }
    ref_re = re.compile(r"^\s*(\d+\.?\s*)?(References|参考文献)\s*$", re.I)
    for p in doc:
        global _PW
        _PW = p.rect.width
        ab = p.get_text("dict")["blocks"]
        blocks = [b for b in ab if b["type"] == 0]
        imgs = [b["bbox"] for b in ab if b["type"] == 1]
        data["pages"].append({"page": p.number + 1, "width": round(p.rect.width, 1),
                              "height": round(p.rect.height, 1),
                              "cjk": round(cjk_ratio(p.get_text()), 3),
                              "nblocks": len(blocks)})

        regions = _merge_boxes(imgs)
        if regions:
            data["suggested_layout"]["fig_regions"][str(p.number + 1)] = [
                [round(v, 1) for v in r] for r in regions]

        col_left = min((b["bbox"][0] for b in blocks if b["bbox"][0] > 40), default=54.4)
        core = _find_formulas([b for b in blocks if not _in_region(b["bbox"], regions)],
                              col_left)

        caps = []
        for b in blocks:
            txt = block_text(b)
            if re.match(LAY("caption_regex"), txt) or re.match(r"^(Table|表)\s*\d", txt):
                caps.append((b["bbox"][1], txt))
            if ref_re.match(txt):
                pg = p.number + 1
                if pg not in data["suggested_layout"]["references_pages"]:
                    data["suggested_layout"]["references_pages"].append(pg)

        for b in blocks:
            txt = block_text(b)
            key = f"p{p.number}:b{b['number']}"
            size = max(s["size"] for l in b["lines"] for s in l["spans"])
            data["blocks"].append({
                "key": key, "page": p.number + 1,
                "bbox": [round(v, 1) for v in b["bbox"]],
                "size": round(size, 2), "bold": block_bold(b),
                "hint": _hint(b, p.number, blocks, core, col_left, regions),
                "text": txt,
            })
            for line in b["lines"]:
                t = line_text(line).strip()
                if t and _in_region(line["bbox"], regions):
                    data["figure_label_candidates"].append(
                        {"page": p.number + 1, "text": t,
                         "bbox": [round(v, 1) for v in line["bbox"]],
                         "size": round(max(s["size"] for s in line["spans"]), 2)})

        # 表带建议：Table 题注 y1 → 下一个题注 y0（或页底）
        caps.sort()
        for i, (y, txt) in enumerate(caps):
            if re.match(r"^(Table|表)\s*\d", txt):
                y1 = caps[i + 1][0] - 2 if i + 1 < len(caps) else p.rect.height - 40
                band = [round(y + 10, 1), round(y1, 1)]
                data["suggested_layout"]["table_bands"].setdefault(
                    str(p.number + 1), []).append(band)

    # 标题建议：首页宽度足够、字号最大的块（排除竖排水印等窄条）
    first = [b for b in data["blocks"]
             if b["page"] == 1 and (b["bbox"][2] - b["bbox"][0]) > 150
             and b["hint"] not in ("figure", "formula")]
    if first:
        big = max(first, key=lambda b: b["size"])
        data["suggested_layout"]["title_ids"] = [big["key"]]

    out = args.out or "blocks.json"
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    payload = _compact_payload(data) if args.compact else data
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    if args.compact:
        n = sum(len(v) for v in payload["pages"].values())
        print(f"已写出 compact {out}: {n} 块待翻译 / "
              f"{len(payload['figure_labels'])} 页图内标签，{os.path.getsize(out)} 字节")
    else:
        n_body = sum(1 for b in data["blocks"] if b["hint"] == "body")
        print(f"已写出 {out}: {len(data['blocks'])} 块（其中 body {n_body} 块待翻译），"
              f"图内标签候选 {len(data['figure_label_candidates'])} 行")
        print(f"  建议 layout: {json.dumps(data['suggested_layout'], ensure_ascii=False)[:400]}")
        print("  → 请翻译 blocks 中 hint 为 body/heading/title/caption 的块，"
              "并整理 figure_labels / affiliations / layout，生成 translations.json")


def _in_region(bbox, regions):
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    for r in regions:
        if r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1:
            return True
    return False


# ---------------------------------------------------------------- render
def cmd_render(args):
    global CFG, BLOCKS, FIG, AFFIL
    quiet = getattr(args, "quiet", False)
    cfg, data = load_config(args.translations)
    CFG = cfg
    BLOCKS = data.get("blocks", {})
    FIG = data.get("figure_labels", {})
    AFFIL = data.get("affiliations", {})
    paths = font_paths(args)

    doc = pymupdf.open(args.src)
    diagnose(doc, verbose=not quiet)

    chars = set(string.printable) | set(LANG_PUNCT) | set(cfg["params"].get("extra_chars", ""))
    for zh in BLOCKS.values():
        chars |= set(re.sub(r"<[^>]+>", "", zh))
    for zh in list(FIG.values()) + list(AFFIL.values()):
        chars |= set(zh)
    chars = {c for c in chars if c.isprintable()}
    if not quiet:
        print("字体子集化：")
    build_subset_fonts(chars, paths, verbose=not quiet)

    rnd = Renderer(paths)
    if not quiet:
        print("\n" + "=" * 78)
        print("重建：redact 文字 → 同一 bbox 重排译文")
        print("=" * 78)
    for page in doc:
        items, figs = classify_page(page)
        todo = []
        for it in items:
            if it.kind == "affil" or it.key in BLOCKS:
                todo.append(it)
            else:
                print(f"  !! 缺少译文 {it.key}  ({it.src[:48]}…)")
        items = todo
        redacts = [it.rect for it in items] + [f["rect"] for f in figs]
        if redacts:
            for r in redacts:
                page.add_redact_annot(r, fill=None, cross_out=False)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                  text=pymupdf.PDF_REDACT_TEXT_REMOVE)

        counts = {"body": 0, "heading": 0, "caption": 0, "affil": 0, "fig": len(figs)}
        for it in items:
            if it.kind == "affil":
                for ld in it.extra["lines"]:
                    zh = AFFIL[ld["text"]]
                    s = ld["size"] * 0.98
                    tw = rnd.freg.text_length(zh, fontsize=s)
                    page.insert_text((ld["cx"] - tw / 2, ld["oy"]), zh, fontsize=s,
                                     fontname="zhR", fontfile=paths["reg_ttf"],
                                     color=(0, 0, 0))
                counts["affil"] += 1
                continue
            text = BLOCKS.get(it.key)
            if text is None:
                print(f"  !! 缺少译文 {it.key}  ({it.src[:48]}…)")
                continue
            text = clean_citations(text)
            # 图/表题号与正文之间留一个空格：把空格放进 <b> 内，避免被 tighten 粘连
            text = re.sub(r"(<b>[图表]\s*\d+\.)</b>\s+", r"\1 </b>", text)
            if it.kind in ("body", "caption"):
                size = round(it.size * P("body_ratio"), 2)
                if it.key in LAY("widen"):
                    it.rect.x1 = LAY("widen")[it.key]
                indent = (P("indent_em") if (it.kind == "body" and not it.hang
                                             and it.key not in LAY("no_indent")
                                             and it.x_first - it.rect.x0 > 8) else 0.0)
                paras = [p for p in text.split("\n") if p.strip()]
                html = "".join(f"<p>{tighten(p)}</p>" for p in paras)
                rnd.draw_para(page, it.rect, html, size, indent, it.hang, base=it.base,
                              align="left" if (it.kind == "caption"
                                               or it.src.startswith("•")) else "justify")
                counts[it.kind] += 1
            elif it.kind in ("heading", "title"):
                size = P("title_size") if it.kind == "title" else P("head_size")
                rnd.draw_heading(page, text, it.rect, size, it.align, col_bounds(it.rect))
                counts["heading"] += 1

        for f in figs:
            f["zh"] = FIG[f["text"]]
            rnd.draw_fig(page, f)

        if not quiet:
            print(f"  p{page.number + 1}: 正文 {counts['body']}，标题 {counts['heading']}，"
                  f"表/图题 {counts['caption']}，单位 {counts['affil']}，"
                  f"图内标签 {counts['fig']} 行")

    replace_et_al(doc, paths, verbose=not quiet)
    out = args.out or (os.path.splitext(args.src)[0] + "-zh.pdf")
    doc.save(out, garbage=4, deflate=True, clean=True)
    print(f"已写出：{out}  ({os.path.getsize(out) / 1e6:.2f} MB)")
    report(doc, quiet=quiet)
    if not quiet:
        dump_stats(rnd)


def report(doc, quiet: bool = False):
    if not quiet:
        print("\n" + "=" * 78)
        print("验收：字体 / 残留 et al / 缺字方块")
        print("=" * 78)
    issues = set()
    refpages = set(LAY("references_pages"))
    for page in doc:
        pno = page.number + 1
        txt = page.get_text()
        n_etal = 0 if pno in refpages else len(re.findall(r"et\s+al", txt))
        if quiet:
            if n_etal:
                print(f"  !! p{pno}: 残留 et al ×{n_etal}")
        else:
            fonts = sorted({f[3] for f in page.get_fonts()})
            flag = "" if n_etal == 0 else f"  <-- et al ×{n_etal}"
            print(f"  p{pno}: blocks {len(page.get_text('blocks')):3d}  "
                  f"字体 {fonts}{flag}")
        if n_etal:
            issues.add(pno)
        for ch in ("\ufffd", "\u25a1"):
            if ch in txt:
                print(f"      !! 缺字 p{pno}：{ch!r}")
                issues.add(pno)
    print(f"  问题页数：{len(issues)}")


def dump_stats(rnd):
    print("\n" + "=" * 78)
    print("逐块填充率（spare<0 表示溢出；fill=1 表示填满）")
    print("=" * 78)
    for s in rnd.stats:
        r = s["rect"]
        print(f"  p{s['page']} y={r.y0:6.1f}-{r.y1:6.1f}  S={s['size']:4.2f}  "
              f"L={s['lh']:4.2f}  fill={s['fill'] * 100:5.1f}%")


# ---------------------------------------------------------------- CLI
def build_parser():
    ap = argparse.ArgumentParser(description="版式保留的 PDF 译文重建器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("diagnose", help="只做版面诊断")
    d.add_argument("src")

    e = sub.add_parser("extract", help="导出可翻译块清单")
    e.add_argument("src")
    e.add_argument("--out", default=None)
    e.add_argument("--compact", action="store_true",
                   help="只输出待翻译块（无 bbox/按页分组），显著减小文件")
    e.add_argument("--fonts", default=None)
    e.add_argument("--work", default=None)

    r = sub.add_parser("render", help="读入 translations.json 重建译文 PDF")
    r.add_argument("src")
    r.add_argument("translations")
    r.add_argument("--out", default=None)
    r.add_argument("--quiet", action="store_true",
                   help="仅输出 !! 问题行和验收汇总，适合自动化/省上下文")
    r.add_argument("--fonts", default=None)
    r.add_argument("--work", default=None)
    return ap


def main():
    args = build_parser().parse_args()
    if args.cmd == "diagnose":
        diagnose(pymupdf.open(args.src))
        return
    if args.cmd == "extract":
        # extract 也需要一个 CFG（默认值即可）
        global CFG
        CFG = {"params": dict(DEFAULTS["params"]), "layout": dict(DEFAULTS["layout"])}
        cmd_extract(args)
        return
    if args.cmd == "render":
        cmd_render(args)
        return


if __name__ == "__main__":
    main()
