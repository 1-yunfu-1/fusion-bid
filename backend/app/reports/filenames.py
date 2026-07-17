"""报告文件名：{用户原始问题}_{yyyyMMddHHmm}.docx"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

# Windows + Linux 非法字符
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_SPACE = re.compile(r"\s+")


def sanitize_query_for_filename(query: str, *, max_query_len: int = 80) -> str:
    """尽可能保留原始问题，剔除非法字符并限制长度."""
    text = (query or "未命名查询").strip()
    # 防止路径穿越
    text = text.replace("..", "").replace("/", " ").replace("\\", " ")
    text = _INVALID.sub("", text)
    text = _MULTI_SPACE.sub(" ", text).strip(" .")
    if not text:
        text = "未命名查询"
    if len(text) > max_query_len:
        text = text[:max_query_len].rstrip()
    return text


def build_report_filename(
    original_query: str,
    *,
    when: datetime | None = None,
    reports_dir: Path,
) -> Path:
    """生成唯一报告路径；同分钟重名时追加序号."""
    stamp = (when or datetime.now()).strftime("%Y%m%d%H%M")
    base = sanitize_query_for_filename(original_query)
    name = f"{base}_{stamp}.docx"
    # 总长度限制
    if len(name) > 180:
        keep = 180 - len(stamp) - 6  # _ + stamp + .docx
        base = base[: max(keep, 20)]
        name = f"{base}_{stamp}.docx"

    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / name
    if not path.exists():
        return path

    # 同分钟重名增加序号
    idx = 2
    while True:
        candidate = reports_dir / f"{base}_{stamp}_{idx}.docx"
        if not candidate.exists():
            return candidate
        idx += 1
        if idx > 999:
            raise RuntimeError("报告文件名冲突过多")
