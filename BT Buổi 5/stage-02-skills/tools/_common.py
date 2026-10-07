"""Shared path validation for tools that operate inside this stage's workspace."""

from __future__ import annotations

import ntpath
from pathlib import Path, PureWindowsPath


def failure(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def workspace_target(workspace: Path, user_path: str) -> tuple[Path | None, dict | None]:
    raw = user_path.strip() if isinstance(user_path, str) else ""
    if not raw:
        return None, failure("INVALID_PATH", "Path rỗng; hãy truyền đường dẫn tương đối workspace.")
    if raw.startswith("~") or ntpath.splitdrive(raw)[0] or Path(raw).is_absolute() or PureWindowsPath(raw).is_absolute():
        return None, failure("PATH_OUTSIDE_WORKSPACE", "Chỉ chấp nhận đường dẫn tương đối bên trong workspace.")
    root = workspace.resolve()
    candidate = root.joinpath(*raw.replace("\\", "/").split("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, failure("PATH_OUTSIDE_WORKSPACE", f"Đường dẫn vượt khỏi workspace: {raw}")
    return candidate, None


def json_text(value: dict) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
