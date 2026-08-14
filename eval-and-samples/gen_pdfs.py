#!/usr/bin/env python
# 生成真实 PDF 夹具，用于验证 W3 知识库导入的 PDF 解析路径（修 AegisDesk 的 NotImplementedError 坑）。
# 读取 kb/ 下对应 .md/.txt，用 reportlab CID 中文字体渲染为 kb-pdf/*.pdf。
import os
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb")
OUT = os.path.join(BASE, "kb-pdf")
os.makedirs(OUT, exist_ok=True)

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
styles = getSampleStyleSheet()
body = ParagraphStyle("body", parent=styles["Normal"], fontName="STSong-Light",
                      fontSize=11, leading=18, spaceAfter=4)
title = ParagraphStyle("title", parent=body, fontSize=15, leading=24, spaceAfter=8)

# (源文件, 输出 pdf 名)
JOBS = [
    ("退换货政策.md", "退换货政策.pdf"),
    ("商品保修条款.txt", "商品保修条款.pdf"),
    ("隐私政策.md", "隐私政策.pdf"),
]


def strip_md(text: str) -> str:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append("")
            continue
        # 标题
        m = re.match(r"^#{1,6}\s+(.*)", s)
        if m:
            out.append(f"<b>{m.group(1)}</b>")
            continue
        # 列表项
        s2 = re.sub(r"^[-*]\s+", "• ", s)
        s2 = re.sub(r"^\d+\.\s+", "", s2)
        # 基本转义，避免 reportlab XML 解析报错
        s2 = s2.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        out.append(s2)
    return "\n".join(out)


def build(src_name, dst_name):
    with open(os.path.join(KB, src_name), encoding="utf-8") as f:
        raw = f.read()
    doc = SimpleDocTemplate(os.path.join(OUT, dst_name), pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title=dst_name)
    flow = []
    first = True
    for para in strip_md(raw).split("\n"):
        if not para.strip():
            flow.append(Spacer(1, 6))
            continue
        style = title if (first and not para.startswith("•")) else body
        flow.append(Paragraph(para, style))
        if first:
            first = False
    doc.build(flow)
    print(f"generated {dst_name}")


for src, dst in JOBS:
    build(src, dst)
print("ALL_PDFS_DONE")
