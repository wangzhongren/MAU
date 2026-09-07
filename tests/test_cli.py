from pathlib import Path

import pytest

from mau_flow import CommandRequest
from mau_flow.cli import (
    _prompt_shell_approval,
    _resolve_scope,
    _run_parser,
    _start_parser,
    main,
    run,
)


def test_cli_dispatches_start(monkeypatch):
    called = []
    monkeypatch.setattr("mau_flow.cli.start", lambda args: called.append(args) or 0)
    assert main(["start", "--max-rounds", "7"]) == 0
    assert called == [["--max-rounds", "7"]]


def test_cli_dispatches_run(monkeypatch):
    called = []
    monkeypatch.setattr("mau_flow.cli.run", lambda args: called.append(args) or 0)
    assert main(["run", "todo.py", "--task", "finish"]) == 0
    assert called == [["todo.py", "--task", "finish"]]


def test_cli_uses_snapshot_sandbox_by_default():
    assert _start_parser().parse_args([]).sandbox == "snapshot"
    assert _start_parser().parse_args(["--sandbox", "host"]).sandbox == "host"
    run_args = _run_parser().parse_args(["todo.py", "--task", "finish"])
    assert run_args.sandbox == "snapshot"
    assert run_args.max_rounds == 10
    assert run_args.max_agents == 8


def test_cli_dispatches_generation(monkeypatch):
    called = []
    monkeypatch.setattr("mau_flow.cli.generate", lambda args: called.append(args) or 0)
    assert main(["todo.py", "--task", "finish"]) == 0
    assert called == [["todo.py", "--task", "finish"]]


def test_run_generates_then_executes_same_chain(tmp_path, monkeypatch, capsys):
    chain_path = tmp_path / ".mau-flow-chain.xml"
    calls = []

    def fake_generate(args, _parser, *, show_start_hint):
        calls.append(("generate", args.file, args.task, show_start_hint))
        chain_path.write_text("<chain />", encoding="utf-8")
        return chain_path

    def fake_execute(path, **kwargs):
        calls.append(("execute", path, kwargs))
        return 7

    monkeypatch.setattr("mau_flow.cli._generate_chain", fake_generate)
    monkeypatch.setattr("mau_flow.cli._execute_chain", fake_execute)

    result = run(
        [
            "todo.py",
            "--task",
            "finish",
            "--max-agents",
            "5",
            "--max-rounds",
            "100",
            "--approval-mode",
            "never",
            "--sandbox",
            "host",
        ]
    )

    assert result == 7
    assert calls[0] == ("generate", "todo.py", "finish", False)
    assert calls[1][0:2] == ("execute", chain_path)
    assert calls[1][2]["max_rounds"] == 100
    assert calls[1][2]["approval_mode"] == "never"
    assert calls[1][2]["sandbox"] == "host"
    assert "Starting generated chain" in capsys.readouterr().out


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
