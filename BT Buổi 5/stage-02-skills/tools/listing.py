"""Directory discovery tool; only immediate workspace children are exposed."""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

import paths
from tools._common import failure, json_text, workspace_target


def _list(workspace: Path, path: str) -> dict:
    folder, error = workspace_target(workspace, path)
    if error:
        return error
    root = workspace.resolve()
    relative = folder.relative_to(root).as_posix() or "."
    if not folder.exists():
        return failure("DIRECTORY_NOT_FOUND", f"Không tìm thấy thư mục: {relative}")
    if not folder.is_dir():
        return failure("NOT_A_DIRECTORY", f"Đây là file, không phải thư mục: {relative}")
    entries = []
    for child in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
        try:
            resolved = child.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        entries.append({"name": child.name, "path": child.relative_to(root).as_posix(), "type": "directory" if resolved.is_dir() else "file"})
    return {"ok": True, "path": relative, "entries": entries}


@tool
def list_files(path: str) -> str:
    """Liệt kê các mục trực tiếp trong workspace, không duyệt đệ quy.

    `path` là đường dẫn tương đối workspace, ví dụ `data/policies`.
    Mỗi mục có `name`, `path`, `type`; các mục được sắp theo tên.
    """
    return json_text(_list(paths.WORKSPACE_DIR, path))
