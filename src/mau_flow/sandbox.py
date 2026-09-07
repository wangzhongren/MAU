"""Per-MAU transactional workspaces and capability-aware diff gates."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from actunit import ActionCall, ActionResult

from .contracts import Status
from .executor import SharedExecutor, ShellApprover, ToolError

FileKind = Literal["file", "symlink"]

DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".angular",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "coverage",
        "dist",
        "htmlcov",
        "node_modules",
    }
)
DEFAULT_PROTECTED_NAMES = frozenset({".env", ".npmrc", ".pypirc"})


class SandboxViolation(ToolError):
    """A transactional workspace diff violated the MAU capability policy."""


@dataclass(frozen=True)
class FileState:
    kind: FileKind
    digest: str
    size_bytes: int


@dataclass(frozen=True)
class WorkspaceDiff:
    created: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    bytes_changed: int = 0
    merged: bool = False

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return (*self.created, *self.updated, *self.deleted)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "created": list(self.created),
            "updated": list(self.updated),
            "deleted": list(self.deleted),
            "bytes_changed": self.bytes_changed,
            "merged": self.merged,
        }


@dataclass(frozen=True)
class DiffPolicy:
    allowed_tools: frozenset[str]
    max_changed_files: int = 200
    max_changed_bytes: int = 10_000_000
    protected_names: frozenset[str] = field(default_factory=lambda: DEFAULT_PROTECTED_NAMES)

    def validate(
        self,
        diff: WorkspaceDiff,
        before: dict[str, FileState],
        after: dict[str, FileState],
    ) -> None:
        if len(diff.changed_paths) > self.max_changed_files:
            raise SandboxViolation(
                f"Diff changes {len(diff.changed_paths)} files; limit is {self.max_changed_files}"
            )
        if diff.bytes_changed > self.max_changed_bytes:
            raise SandboxViolation(
                f"Diff changes {diff.bytes_changed} bytes; limit is {self.max_changed_bytes}"
            )
        required = {
            "create": diff.created,
            "update": diff.updated,
            "delete": diff.deleted,
        }
        for capability, paths in required.items():
            if paths and capability not in self.allowed_tools:
                raise SandboxViolation(
                    f"Diff requires disallowed {capability} capability: {', '.join(paths[:5])}"
                )
        for path in diff.changed_paths:
            if Path(path).name in self.protected_names:
                raise SandboxViolation(f"Diff changes protected file: {path}")
            states = (before.get(path), after.get(path))
            if any(state is not None and state.kind != "file" for state in states):
                raise SandboxViolation(f"Diff changes a symbolic link: {path}")


class TransactionalExecutor(SharedExecutor):
    """Run one MAU in a disposable copy and merge only an authorized successful diff."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        allowed_tools: set[str] | frozenset[str],
        shell_approver: ShellApprover | None = None,
        excluded_dirs: frozenset[str] = DEFAULT_EXCLUDED_DIRS,
        max_changed_files: int = 200,
        max_changed_bytes: int = 10_000_000,
    ):
        self.canonical_dir = Path(workspace).resolve()
        self.excluded_dirs = excluded_dirs
        self.policy = DiffPolicy(
            allowed_tools=frozenset(allowed_tools),
            max_changed_files=max_changed_files,
            max_changed_bytes=max_changed_bytes,
        )
        self._temporary_root: Path | None = None
        self._baseline: dict[str, FileState] | None = None
        self.last_diff: WorkspaceDiff | None = None
        super().__init__(
            self.canonical_dir,
            shell_approver=shell_approver,
        )

    def execute(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        self._ensure_started()
        return super().execute(tool_name, **kwargs)

    def execute_call(
        self, call: ActionCall, *, allowed_tools: frozenset[str], agent_id: str = ""
    ) -> ActionResult:
        if call.name in allowed_tools:
            self._ensure_started()
        return super().execute_call(call, allowed_tools=allowed_tools, agent_id=agent_id)

    def finalize(self, status: Any) -> WorkspaceDiff | None:
        if self._temporary_root is None or self._baseline is None:
            return None
        before = self._baseline
        after = self._manifest(self.sandbox_dir)
        diff = self._calculate_diff(before, after)
        try:
            should_merge = Status(status) == Status.SUCCESS
            if should_merge:
                self.policy.validate(diff, before, after)
                self._check_conflicts(diff, before)
                self._merge(diff)
                diff = WorkspaceDiff(
                    created=diff.created,
                    updated=diff.updated,
                    deleted=diff.deleted,
                    bytes_changed=diff.bytes_changed,
                    merged=True,
                )
            self.last_diff = diff
            return diff
        finally:
            self._cleanup()

    def _ensure_started(self) -> None:
        if self._temporary_root is not None:
            return
        temporary_root = Path(tempfile.mkdtemp(prefix="mau-flow-"))
        snapshot = temporary_root / "workspace"
        try:
            self._copy_workspace(snapshot)
        except (OSError, shutil.Error):
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise
        self._temporary_root = temporary_root
        self.sandbox_dir = snapshot.resolve()
        self._baseline = self._manifest(self.sandbox_dir)

    def _copy_workspace(self, snapshot: Path) -> None:
        if sys.platform == "darwin" and Path("/bin/cp").is_file():
            completed = subprocess.run(
                ["/bin/cp", "-cR", str(self.canonical_dir), str(snapshot)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=300,
                check=False,
            )
            if completed.returncode == 0:
                return
            if snapshot.exists():
                shutil.rmtree(snapshot, ignore_errors=True)
        shutil.copytree(
            self.canonical_dir,
            snapshot,
            symlinks=True,
            ignore_dangling_symlinks=True,
        )

    def _manifest(self, root: Path) -> dict[str, FileState]:
        manifest: dict[str, FileState] = {}
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            current_path = Path(current)
            retained_dirs: list[str] = []
            for name in dirnames:
                path = current_path / name
                if name in self.excluded_dirs:
                    continue
                if path.is_symlink():
                    relative = path.relative_to(root).as_posix()
                    target = os.readlink(path)
                    manifest[relative] = FileState(
                        kind="symlink",
                        digest=hashlib.sha256(target.encode()).hexdigest(),
                        size_bytes=len(target.encode()),
                    )
                    continue
                retained_dirs.append(name)
            dirnames[:] = retained_dirs
            for name in filenames:
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                if path.is_symlink():
                    target = os.readlink(path)
                    manifest[relative] = FileState(
                        kind="symlink",
                        digest=hashlib.sha256(target.encode()).hexdigest(),
                        size_bytes=len(target.encode()),
                    )
                    continue
                if not path.is_file():
                    continue
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1_048_576), b""):
                        digest.update(chunk)
                manifest[relative] = FileState(
                    kind="file",
                    digest=digest.hexdigest(),
                    size_bytes=path.stat().st_size,
                )
        return manifest

    @staticmethod
    def _calculate_diff(
        before: dict[str, FileState], after: dict[str, FileState]
    ) -> WorkspaceDiff:
        created = tuple(sorted(after.keys() - before.keys()))
        deleted = tuple(sorted(before.keys() - after.keys()))
        updated = tuple(
            sorted(path for path in before.keys() & after.keys() if before[path] != after[path])
        )
        bytes_changed = sum(after[path].size_bytes for path in (*created, *updated))
        return WorkspaceDiff(
            created=created,
            updated=updated,
            deleted=deleted,
            bytes_changed=bytes_changed,
        )

    def _merge(self, diff: WorkspaceDiff) -> None:
        for relative in sorted(diff.deleted, key=lambda item: item.count("/"), reverse=True):
            target = self._safe_canonical_target(relative)
            if target.is_file():
                target.unlink()
        for relative in (*diff.created, *diff.updated):
            source = self.sandbox_dir / relative
            target = self._safe_canonical_target(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=".mau-", dir=target.parent)
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                shutil.copy2(source, temporary, follow_symlinks=False)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)

    def _check_conflicts(
        self, diff: WorkspaceDiff, baseline: dict[str, FileState]
    ) -> None:
        canonical_now = self._manifest(self.canonical_dir)
        conflicts = [
            path
            for path in diff.changed_paths
            if baseline.get(path) != canonical_now.get(path)
        ]
        if conflicts:
            raise SandboxViolation(
                f"Canonical workspace changed concurrently: {', '.join(conflicts[:5])}"
            )

    def _safe_canonical_target(self, relative: str) -> Path:
        target = (self.canonical_dir / relative).resolve()
        if target != self.canonical_dir and self.canonical_dir not in target.parents:
            raise SandboxViolation(f"Diff target escapes canonical workspace: {relative}")
        return target

    def _cleanup(self) -> None:
        temporary_root = self._temporary_root
        self._temporary_root = None
        self._baseline = None
        self.sandbox_dir = self.canonical_dir
        if temporary_root is not None:
            shutil.rmtree(temporary_root, ignore_errors=True)
