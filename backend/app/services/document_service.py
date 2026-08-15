"""文档解析与内容提取（BU-04）：.txt / .md / .pdf → 纯文本。

- txt/md：UTF-8 解码（含 BOM 兼容）；非 UTF-8 回退 gbk（中文场景常见）。
- pdf：pdfplumber 逐页抽文本（修 AegisDesk 的 PDF NotImplementedError 坑）；
  抽不到文本（扫描件）抛 ``UnsupportedFileError``，由导入标 failed 而非假成功。
- 文件大小上限由调用方（API 层）把关，本模块不做。
"""
from __future__ import annotations

import io


class UnsupportedFileError(Exception):
    """文件类型不支持或内容不可解析（导入应标记 failed 并给出可操作信息）。"""


#: 支持的文件扩展名 → 说明
SUPPORTED_EXTENSIONS = {".txt": "纯文本", ".md": "Markdown", ".pdf": "PDF"}


def extract_text(filename: str, content: bytes) -> str:
    """按扩展名抽取文本。扩展名不支持抛 UnsupportedFileError。"""
    ext = _ext(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileError(
            f"不支持的文件类型 {ext!r}，仅支持: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    if ext == ".pdf":
        return _extract_pdf(content)
    return _decode_text(content)


def _ext(filename: str) -> str:
    return "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _decode_text(content: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    # 全部失败：按 UTF-8 替换错误解码，保证导入不因单文件编码问题整体中断
    return content.decode("utf-8", errors="replace")


def _extract_pdf(content: bytes) -> str:
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover - 环境依赖
        raise UnsupportedFileError(
            "PDF 解析需要 pdfplumber：pip install pdfplumber"
            f"（缺失原始错误: {e}）"
        ) from e
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]
    except Exception as e:  # noqa: BLE001 - 损坏/加密 PDF 统一报错
        raise UnsupportedFileError(f"PDF 解析失败（文件损坏或加密）: {e}") from e
    text = "\n".join(pages).strip()
    if not text:
        raise UnsupportedFileError("PDF 未能抽取到文本（可能为扫描件，暂不支持 OCR）")
    return text
