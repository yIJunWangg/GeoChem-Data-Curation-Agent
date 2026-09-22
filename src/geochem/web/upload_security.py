"""Deterministic validation for files accepted by the Web API."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException


HEADER_EXTENSIONS = frozenset({".csv", ".xls", ".xlsx"})
PDF_EXTENSIONS = frozenset({".pdf"})
ARTICLE_EXTENSIONS = frozenset({
    ".pdf", ".csv", ".xls", ".xlsx", ".doc", ".docx", ".zip",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff",
})

_GENERIC_MIME_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_MIME_TYPES = {
    ".pdf": {"application/pdf", "application/x-pdf"},
    ".csv": {"text/csv", "text/plain", "application/csv", "application/vnd.ms-excel"},
    ".xls": {"application/vnd.ms-excel", "application/x-ole-storage"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip"},
    ".doc": {"application/msword", "application/x-ole-storage"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip"},
    ".zip": {"application/zip", "application/x-zip-compressed"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".tif": {"image/tiff"},
    ".tiff": {"image/tiff"},
}


def validate_uploaded_file(
    path: Path,
    filename: str,
    content_type: str | None,
    *,
    allowed_extensions: frozenset[str],
) -> str:
    """Validate extension, declared MIME type, and a small file signature."""

    suffix = Path(filename).suffix.lower()
    if suffix not in allowed_extensions:
        allowed = ", ".join(sorted(allowed_extensions))
        raise HTTPException(415, f"不支持的文件类型 {suffix or '(无扩展名)'}；允许：{allowed}。")
    mime = str(content_type or "").split(";", 1)[0].strip().lower()
    if mime not in _GENERIC_MIME_TYPES and mime not in _MIME_TYPES.get(suffix, set()):
        raise HTTPException(415, f"文件扩展名 {suffix} 与 MIME 类型 {mime} 不匹配。")

    with path.open("rb") as handle:
        head = handle.read(8192)
    valid = _signature_matches(suffix, head)
    if not valid:
        raise HTTPException(415, f"{suffix} 文件签名无效或内容与扩展名不匹配。")
    return suffix


def _signature_matches(suffix: str, content: bytes) -> bool:
    if suffix == ".pdf":
        return content.startswith(b"%PDF-")
    if suffix in {".xlsx", ".docx", ".zip"}:
        return content.startswith(b"PK\x03\x04") or content.startswith(b"PK\x05\x06")
    if suffix in {".xls", ".doc"}:
        return content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if suffix == ".png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return content.startswith(b"\xff\xd8\xff")
    if suffix in {".tif", ".tiff"}:
        return content.startswith((b"II*\x00", b"MM\x00*"))
    if suffix == ".csv":
        if not content or b"\x00" in content:
            return False
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                content.decode(encoding)
                return True
            except UnicodeDecodeError:
                continue
        return False
    return False
