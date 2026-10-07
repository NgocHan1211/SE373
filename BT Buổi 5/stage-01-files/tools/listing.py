"""Directory discovery tool; it only returns immediate workspace children."""

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

    results = []
    for child in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
        # An outward symlink is not traversed or disclosed as a usable workspace entry.
        try:
            resolved_child = child.resolve()
            resolved_child.relative_to(root)
        except (OSError, ValueError):
            continue
        results.append({
            "name": child.name,
            "path": child.relative_to(root).as_posix(),
            "type": "directory" if resolved_child.is_dir() else "file",
        })
    return {"ok": True, "path": relative, "entries": results}


@tool
def list_files(path: str) -> str:
    """Liệt kê file và thư mục con trực tiếp trong workspace, không duyệt sâu.

    `path` là đường dẫn tương đối workspace, ví dụ `data/policies`.
    Mỗi mục trả về có `name`, `path`, `type`; kết quả được sắp theo tên.
    """
    return json_text(_list(paths.WORKSPACE_DIR, path))
