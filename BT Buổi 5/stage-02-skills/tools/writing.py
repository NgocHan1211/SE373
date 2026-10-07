"""Write UTF-8 files under workspace/output only."""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

import paths
from tools._common import failure, json_text, workspace_target


def _write(workspace: Path, path: str, content: str) -> dict:
    destination, error = workspace_target(workspace, path)
    if error:
        return error
    root = workspace.resolve()
    output = (root / paths.OUTPUT_SUBDIR).resolve()
    if destination == output or not destination.is_relative_to(output):
        return failure("PATH_OUTSIDE_OUTPUT", f"Chỉ được ghi dưới {paths.OUTPUT_SUBDIR}/.")
    if destination.is_dir():
        return failure("IS_A_DIRECTORY", f"Đường dẫn đích là thư mục: {destination.relative_to(root).as_posix()}")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        safe_parent = destination.parent.resolve()
        safe_parent.relative_to(output)
        safe_destination = destination.resolve()
        safe_destination.relative_to(output)
        if safe_destination == output or safe_destination.is_dir():
            return failure("IS_A_DIRECTORY", "Đường dẫn đích không phải file.")
        status = "updated" if safe_destination.exists() else "created"
        data = content.encode("utf-8")
        safe_destination.write_bytes(data)
    except ValueError:
        return failure("PATH_OUTSIDE_OUTPUT", "Đường dẫn đích trỏ ra ngoài output/.")
    except OSError as exc:
        return failure("WRITE_FAILED", f"Không ghi được file: {exc.strerror or 'lỗi hệ thống file'}")
    return {"ok": True, "path": safe_destination.relative_to(root).as_posix(), "bytes": len(data), "status": status}


@tool
def write_file(path: str, content: str) -> str:
    """Ghi nội dung UTF-8 vào workspace/output; tự tạo thư mục cha khi cần."""
    return json_text(_write(paths.WORKSPACE_DIR, path, content))
