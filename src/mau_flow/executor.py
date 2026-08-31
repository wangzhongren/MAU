"""Shared, auditable execution boundary."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any


class ToolError(RuntimeError):
    pass


class SharedExecutor:
    """Fixed five-tool execution boundary with a workspace-scoped file API."""

    def __init__(self, sandbox_dir: str | Path):
        self.sandbox_dir = Path(sandbox_dir).resolve()
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self._tools: dict[str, Callable[..., dict[str, Any]]] = {
            "create": self._create,
            "read": self._read,
            "update": self._update,
            "delete": self._delete,
            "shell": self._shell,
        }

    @property
    def tool_names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def execute(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ToolError(f"Unknown tool: {tool_name}")
        try:
            return tool(**kwargs)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Tool {tool_name!r} failed: {exc}") from exc

    def _resolve(self, relative_path: str) -> Path:
        path = (self.sandbox_dir / relative_path).resolve()
        if path != self.sandbox_dir and self.sandbox_dir not in path.parents:
            raise ToolError(f"Path escapes sandbox: {relative_path}")
        return path

    def _create(self, path: str, content: str) -> dict[str, Any]:
        target = self._resolve(path)
        if target.exists():
            raise ToolError(f"Path already exists: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"status": "ok", "path": str(target), "bytes_written": len(content.encode())}

    def _read(self, path: str) -> dict[str, Any]:
        target = self._resolve(path)
        content = target.read_text(encoding="utf-8")
        return {"status": "ok", "path": str(target), "content": content}

    def _update(self, path: str, content: str) -> dict[str, Any]:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"File does not exist: {path}")
        target.write_text(content, encoding="utf-8")
        return {"status": "ok", "path": str(target), "bytes_written": len(content.encode())}

    def _delete(self, path: str) -> dict[str, Any]:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"File does not exist: {path}")
        target.unlink()
        return {"status": "ok", "path": str(target)}

    def _shell(self, command: str, timeout_seconds: float | str = 30) -> dict[str, Any]:
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise ToolError("timeout_seconds must be a number") from exc
        if timeout <= 0 or timeout > 300:
            raise ToolError("timeout_seconds must be between 0 and 300")
        completed = subprocess.run(
            command,
            cwd=self.sandbox_dir,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "status": "ok" if completed.returncode == 0 else "error",
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
