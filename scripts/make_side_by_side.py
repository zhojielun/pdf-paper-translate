#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_side_by_side.py  ——  生成左原文 / 右译文的对照版 PDF
========================================================

把原文 PDF 与译文 PDF 逐页并排：
* 每页尺寸 (2W + G) × H，左半放原文第 i 页，右半放译文第 i 页；
* 中缝 x = W + G/2 处画一条细灰竖线做分隔；
* 页顺序与原文一一对应，页数保持 N 不变。

用法
----
    python3 make_side_by_side.py SRC.pdf OUT-zh.pdf --out OUT-dual.pdf
    python3 make_side_by_side.py SRC.pdf OUT-zh.pdf --gutter 36
"""
from __future__ import annotations

import argparse
import os
import sys

import pymupdf

GUTTER = 24.0          # 中缝宽度 G
LINE_W = 0.6           # 分隔线宽
LINE_COLOR = (0.55, 0.55, 0.55)   # 细灰色


def main():
    ap = argparse.ArgumentParser(description="左原文 / 右译文 对照版")
    ap.add_argument("src", help="原文 PDF")
    ap.add_argument("zh", help="译文 PDF")
    ap.add_argument("--out", default=None, help="输出（默认 SRC-dual.pdf）")
    ap.add_argument("--gutter", type=float, default=GUTTER,
                    help="中缝宽度（pt），默认 %(default)s")
    args = ap.parse_args()

    if not os.path.exists(args.zh):
        sys.exit(f"缺少译文版：{args.zh}")

    src = pymupdf.open(args.src)
    zh = pymupdf.open(args.zh)
    out = args.out or (os.path.splitext(args.src)[0] + "-dual.pdf")

    if src.page_count != zh.page_count:
        print(f"  !! 页数不一致：原文 {src.page_count} 页，译文 {zh.page_count} 页")

    n = min(src.page_count, zh.page_count)
    w0, h0 = src[0].rect.width, src[0].rect.height
    g = args.gutter
    mid = w0 + g / 2.0
    out_w, out_h = 2 * w0 + g, h0

    print("=" * 78)
    print("对照版：左原文 / 右译文")
    print("=" * 78)
    print(f"  单页 {w0:.0f}×{h0:.0f}  →  对照页 {out_w:.0f}×{out_h:.0f}  "
          f"(中缝 G={g:.0f}，分隔线 x={mid:.1f})")

    dst = pymupdf.open()
    for i in range(n):
        sp, zp = src[i], zh[i]
        w, h = sp.rect.width, sp.rect.height
        if abs(w - w0) > 0.5 or abs(h - h0) > 0.5:
            print(f"  !! p{i + 1} 尺寸 {w:.0f}×{h:.0f} 与首页不一致，按首页画布摆放")
        page = dst.new_page(width=out_w, height=out_h)
        page.show_pdf_page(pymupdf.Rect(0, 0, w, h), src, i, keep_proportion=True)
        page.show_pdf_page(pymupdf.Rect(w + g, 0, w + g + w, h), zh, i,
                           keep_proportion=True)
        page.draw_line(pymupdf.Point(mid, 0), pymupdf.Point(mid, out_h),
                       color=LINE_COLOR, width=LINE_W)
        print(f"  p{i + 1}: 原文 {w:.0f}×{h:.0f}  ←→  译文 {zp.rect.width:.0f}×"
              f"{zp.rect.height:.0f}")

    dst.save(out, garbage=4, deflate=True, clean=True)
    print(f"\n已写出：{out}  ({os.path.getsize(out) / 1e6:.2f} MB，{dst.page_count} 页，"
          f"{dst[0].rect.width:.0f}×{dst[0].rect.height:.0f})")


if __name__ == "__main__":
    main()
