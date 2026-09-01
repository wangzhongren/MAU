from pathlib import Path

import pytest

from mau_flow import CommandRequest
from mau_flow.cli import _prompt_shell_approval, _resolve_scope, _start_parser, main


def test_cli_dispatches_start(monkeypatch):
    called = []
    monkeypatch.setattr("mau_flow.cli.start", lambda args: called.append(args) or 0)
    assert main(["start", "--max-rounds", "7"]) == 0
    assert called == [["--max-rounds", "7"]]


def test_cli_uses_snapshot_sandbox_by_default():
    assert _start_parser().parse_args([]).sandbox == "snapshot"
    assert _start_parser().parse_args(["--sandbox", "host"]).sandbox == "host"


def test_cli_dispatches_generation(monkeypatch):
    called = []
    monkeypatch.setattr("mau_flow.cli.generate", lambda args: called.append(args) or 0)
    assert main(["todo.py", "--task", "finish"]) == 0
    assert called == [["todo.py", "--task", "finish"]]


def test_cli_allows_new_file_with_existing_parent(tmp_path):
    workspace, target = _resolve_scope(str(tmp_path / "new.py"))
    assert workspace == tmp_path.resolve()
    assert target == "new.py"


def test_cli_rejects_directory_as_target(tmp_path):
    with pytest.raises(ValueError, match="not a directory"):
        _resolve_scope(str(tmp_path))


def test_cli_scope_without_file_uses_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _resolve_scope(None) == (Path.cwd().resolve(), None)


def test_cli_shell_approval_denies_noninteractive_input(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    request = CommandRequest(
        program="python",
        executable=Path("/usr/bin/python"),
        args=("-m", "pytest"),
        cwd=tmp_path,
        timeout_seconds=30,
        reason="Run tests",
    )
    assert _prompt_shell_approval(request) is False
    error = capsys.readouterr().err
    assert "Shell approval required" in error
    assert "Run tests" in error
    assert "Denied" in error


def test_cli_shell_approval_accepts_explicit_yes(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    request = CommandRequest(
        program="python",
        executable=Path("/usr/bin/python"),
        args=("-V",),
        cwd=tmp_path,
        timeout_seconds=30,
        reason="Check version",
    )
    assert _prompt_shell_approval(request) is True
