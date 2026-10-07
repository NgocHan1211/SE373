"""Read bounded UTF-8 text files from the active workspace."""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

import paths
from tools._common import failure, json_text, workspace_target

MAX_READ_BYTES = 200_000


def _read(workspace: Path, path: str) -> dict:
    source, error = workspace_target(workspace, path)
    if error:
        return error
    relative = source.relative_to(workspace.resolve()).as_posix()
    if not source.exists():
        return failure("FILE_NOT_FOUND", f"Không tìm thấy file: {relative}")
    if source.is_dir():
        return failure("IS_A_DIRECTORY", f"Đây là thư mục, không phải file: {relative}")
    try:
        if source.stat().st_size > MAX_READ_BYTES:
            return failure("FILE_TOO_LARGE", f"File vượt giới hạn {MAX_READ_BYTES} bytes: {relative}")
        content = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return failure("NOT_UTF8_TEXT", f"File không phải văn bản UTF-8: {relative}")
    except OSError as exc:
        return failure("READ_FAILED", f"Không đọc được {relative}: {exc.strerror or 'lỗi hệ thống file'}")
    return {"ok": True, "path": relative, "content": content}


@tool
def read_file(path: str) -> str:
    """Đọc file UTF-8 trong workspace theo đường dẫn tương đối và trả nội dung JSON."""
    return json_text(_read(paths.WORKSPACE_DIR, path))
