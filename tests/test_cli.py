from pathlib import Path

import pytest

from mau_flow.cli import _resolve_scope, main


def test_cli_dispatches_start(monkeypatch):
    called = []
    monkeypatch.setattr("mau_flow.cli.start", lambda args: called.append(args) or 0)
    assert main(["start", "--max-rounds", "7"]) == 0
    assert called == [["--max-rounds", "7"]]


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
