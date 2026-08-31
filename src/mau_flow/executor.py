"""Shared, auditable execution boundary."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


class ToolError(RuntimeError):
    pass


Tool = Callable[..., dict[str, Any]]


class SharedExecutor:
    """A registry-based executor. It stores no per-agent conversation state."""

    def __init__(self, sandbox_dir: str | Path):
        self.sandbox_dir = Path(sandbox_dir).resolve()
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self._tools: dict[str, Tool] = {}
        self.register("write_file", self._write_file)
        self.register("read_file", self._read_file)

    @property
    def tool_names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def register(self, name: str, tool: Tool, *, replace: bool = False) -> None:
        if not name or (name in self._tools and not replace):
            raise ValueError(f"Tool already registered or invalid: {name!r}")
        self._tools[name] = tool

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

    def _write_file(self, path: str, content: str) -> dict[str, Any]:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"status": "ok", "path": str(target), "bytes_written": len(content.encode())}

    def _read_file(self, path: str) -> dict[str, Any]:
        target = self._resolve(path)
        content = target.read_text(encoding="utf-8")
        return {"status": "ok", "path": str(target), "content": content}

