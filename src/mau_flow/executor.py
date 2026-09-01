"""Shared, auditable execution boundary."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandRequest:
    """A fully resolved command presented to policy and approval callbacks."""

    program: str
    executable: Path
    args: tuple[str, ...]
    cwd: Path
    timeout_seconds: float
    reason: str

    @property
    def argv(self) -> tuple[str, ...]:
        return (str(self.executable), *self.args)


@dataclass(frozen=True)
class CommandRule:
    """Pre-authorize one executable with an optional required argument prefix."""

    program: str
    args_prefix: tuple[str, ...] = ()

    def allows(self, request: CommandRequest) -> bool:
        rule_path = Path(self.program)
        if rule_path.is_absolute() or rule_path.parent != Path("."):
            if self.program != request.program:
                return False
            prefix_length = len(self.args_prefix)
            return request.args[:prefix_length] == self.args_prefix
        requested_name = Path(request.program).name
        executable_name = request.executable.name
        if self.program not in {request.program, requested_name, executable_name}:
            return False
        prefix_length = len(self.args_prefix)
        return request.args[:prefix_length] == self.args_prefix


ShellApprover = Callable[[CommandRequest], bool]


class SharedExecutor:
    """Workspace-scoped file operations and policy-gated structured commands.

    Shell commands are argv operations, never shell source text. A command must match
    a pre-authorized rule or be accepted by ``shell_approver``. With neither, command
    execution is denied by default.
    """

    DEFAULT_OUTPUT_LIMIT = 1_000_000
    _ENV_ALLOWLIST = frozenset(
        {
            "PATH",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "SYSTEMROOT",
            "WINDIR",
            "PATHEXT",
            "COMSPEC",
            "TMPDIR",
            "TEMP",
            "TMP",
        }
    )

    def __init__(
        self,
        sandbox_dir: str | Path,
        *,
        shell_rules: Iterable[CommandRule] = (),
        shell_approver: ShellApprover | None = None,
        output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT,
    ):
        if output_limit_bytes < 1:
            raise ValueError("output_limit_bytes must be at least 1")
        self.sandbox_dir = Path(sandbox_dir).resolve()
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self.shell_rules = tuple(shell_rules)
        self.shell_approver = shell_approver
        self.output_limit_bytes = output_limit_bytes
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

    def finalize(self, status: Any) -> Any:
        """Finalize a MAU execution session. The host executor has no transaction."""

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
        return {
            "status": "ok",
            "path": target.relative_to(self.sandbox_dir).as_posix(),
            "bytes_written": len(content.encode()),
        }

    def _read(
        self, path: str | None = None, paths: Sequence[str] | None = None
    ) -> dict[str, Any]:
        if path is not None and paths is not None:
            raise ToolError("read accepts either path or paths, not both")
        if paths is not None:
            if (
                isinstance(paths, (str, bytes))
                or not isinstance(paths, Sequence)
                or not paths
                or not all(isinstance(item, str) and item for item in paths)
            ):
                raise ToolError("paths must be a non-empty sequence of strings")
            files: list[dict[str, Any]] = []
            failed = False
            for item in paths:
                try:
                    files.append(self._read_one(item))
                except (OSError, ToolError, UnicodeError) as exc:
                    failed = True
                    files.append({"status": "error", "path": item, "error": str(exc)})
            return {"status": "partial" if failed else "ok", "files": files}
        if path is None or not path:
            raise ToolError("read requires path or paths")
        return self._read_one(path)

    def _read_one(self, path: str) -> dict[str, Any]:
        target = self._resolve(path)
        content = target.read_text(encoding="utf-8")
        relative = target.relative_to(self.sandbox_dir).as_posix()
        return {"status": "ok", "path": relative, "content": content}

    def _update(self, path: str, content: str) -> dict[str, Any]:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"File does not exist: {path}")
        target.write_text(content, encoding="utf-8")
        return {
            "status": "ok",
            "path": target.relative_to(self.sandbox_dir).as_posix(),
            "bytes_written": len(content.encode()),
        }

    def _delete(self, path: str) -> dict[str, Any]:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"File does not exist: {path}")
        target.unlink()
        return {"status": "ok", "path": target.relative_to(self.sandbox_dir).as_posix()}

    def _shell(
        self,
        program: str,
        args: Sequence[str] | None = None,
        cwd: str = ".",
        timeout_seconds: float | str = 30,
        reason: str = "",
    ) -> dict[str, Any]:
        if not program or not program.strip():
            raise ToolError("program must not be empty")
        if args is None:
            command_args: tuple[str, ...] = ()
        elif (
            not isinstance(args, Sequence)
            or isinstance(args, (str, bytes))
            or not all(isinstance(arg, str) for arg in args)
        ):
            raise ToolError("args must be a sequence of strings")
        else:
            command_args = tuple(args)
        if any("\x00" in item for item in (program, *command_args)):
            raise ToolError("program and args must not contain null bytes")
        if not reason.strip():
            raise ToolError("reason must not be empty")
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise ToolError("timeout_seconds must be a number") from exc
        if timeout <= 0 or timeout > 300:
            raise ToolError("timeout_seconds must be between 0 and 300")

        working_directory = self._resolve(cwd)
        if not working_directory.is_dir():
            raise ToolError(f"Working directory does not exist: {cwd}")
        executable = self._resolve_executable(program)
        request = CommandRequest(
            program=program,
            executable=executable,
            args=command_args,
            cwd=working_directory,
            timeout_seconds=timeout,
            reason=reason.strip(),
        )
        if not self._is_command_allowed(request):
            raise ToolError("Shell command was not approved")
        return self._run_command(request)

    def _resolve_executable(self, program: str) -> Path:
        candidate = Path(program).expanduser()
        has_path_separator = os.sep in program or bool(os.altsep and os.altsep in program)
        if candidate.is_absolute() or has_path_separator:
            path = candidate if candidate.is_absolute() else self.sandbox_dir / candidate
            executable = path.resolve()
            if not executable.is_file():
                raise ToolError(f"Executable does not exist: {program}")
            return executable
        resolved = shutil.which(program, path=os.environ.get("PATH", os.defpath))
        if resolved is None:
            raise ToolError(f"Executable was not found on PATH: {program}")
        return Path(resolved).resolve()

    def _is_command_allowed(self, request: CommandRequest) -> bool:
        if any(rule.allows(request) for rule in self.shell_rules):
            return True
        return bool(self.shell_approver and self.shell_approver(request))

    def _safe_environment(self) -> dict[str, str]:
        environment = {
            name: value for name, value in os.environ.items() if name in self._ENV_ALLOWLIST
        }
        inherited_path = environment.get("PATH", os.defpath)
        trusted_entries = [entry for entry in inherited_path.split(os.pathsep) if Path(entry).is_absolute()]
        environment["PATH"] = os.pathsep.join(trusted_entries) or os.defpath
        return environment

    def _run_command(self, request: CommandRequest) -> dict[str, Any]:
        result = self._run_process(
            argv=request.argv,
            cwd=request.cwd,
            env=self._safe_environment(),
            timeout=request.timeout_seconds,
        )
        return self._command_result(request, result)

    def _run_process(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        popen_kwargs: dict[str, Any] = {
            "cwd": cwd,
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        elif os.name == "nt":  # pragma: no cover - exercised on Windows
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        process = subprocess.Popen(argv, **popen_kwargs)
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stdout_truncated = [False]
        stderr_truncated = [False]
        threads = [
            threading.Thread(
                target=self._drain_stream,
                args=(process.stdout, stdout_chunks, stdout_truncated),
                daemon=True,
            ),
            threading.Thread(
                target=self._drain_stream,
                args=(process.stderr, stderr_chunks, stderr_truncated),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self._kill_process_group(process)
            process.wait()
            raise ToolError(f"Command timed out after {timeout:g} seconds") from exc
        finally:
            for thread in threads:
                thread.join()

        return {
            "status": "ok" if exit_code == 0 else "error",
            "exit_code": exit_code,
            "stdout": b"".join(stdout_chunks).decode("utf-8", errors="replace"),
            "stderr": b"".join(stderr_chunks).decode("utf-8", errors="replace"),
            "stdout_truncated": stdout_truncated[0],
            "stderr_truncated": stderr_truncated[0],
        }

    def _command_result(self, request: CommandRequest, result: dict[str, Any]) -> dict[str, Any]:
        relative_cwd = request.cwd.relative_to(self.sandbox_dir).as_posix()
        return {
            **result,
            "executable": str(request.executable),
            "argv": list(request.argv),
            "cwd": relative_cwd,
            "reason": request.reason,
            "timeout_seconds": request.timeout_seconds,
        }

    def _drain_stream(
        self,
        stream: BinaryIO | None,
        chunks: list[bytes],
        truncated: list[bool],
    ) -> None:
        if stream is None:
            return
        captured = 0
        try:
            while chunk := stream.read(65_536):
                remaining = self.output_limit_bytes - captured
                if remaining > 0:
                    chunks.append(chunk[:remaining])
                    captured += min(len(chunk), remaining)
                if len(chunk) > max(remaining, 0):
                    truncated[0] = True
        finally:
            stream.close()

    @staticmethod
    def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
                return
            except ProcessLookupError:
                return
        process.kill()  # pragma: no cover - exercised on Windows
