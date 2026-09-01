import sys
from pathlib import Path

import pytest

from mau_flow import CommandRule, SharedExecutor, ToolError


def test_executor_read_write(tmp_path):
    executor = SharedExecutor(tmp_path)
    assert executor.tool_names == frozenset({"create", "read", "update", "delete", "shell"})
    result = executor.execute("create", path="nested/a.txt", content="hello")
    assert result["status"] == "ok"
    assert executor.execute("read", path="nested/a.txt")["content"] == "hello"
    executor.execute("update", path="nested/a.txt", content="updated")
    assert executor.execute("read", path="nested/a.txt")["content"] == "updated"
    executor.execute("delete", path="nested/a.txt")
    assert not (tmp_path / "nested/a.txt").exists()


def test_executor_multi_read_labels_each_file(tmp_path):
    executor = SharedExecutor(tmp_path)
    executor.execute("create", path="a.txt", content="alpha")
    executor.execute("create", path="b.txt", content="beta")

    result = executor.execute("read", paths=["a.txt", "b.txt"])

    assert [Path(item["path"]).name for item in result["files"]] == ["a.txt", "b.txt"]
    assert [item["content"] for item in result["files"]] == ["alpha", "beta"]
    partial = executor.execute("read", paths=["a.txt", "missing.txt", "b.txt"])
    assert partial["status"] == "partial"
    assert [item["status"] for item in partial["files"]] == ["ok", "error", "ok"]
    assert partial["files"][2]["content"] == "beta"
    with pytest.raises(ToolError, match="either path or paths"):
        executor.execute("read", path="a.txt", paths=["b.txt"])


def test_executor_rejects_escape_and_unknown_tool(tmp_path):
    executor = SharedExecutor(tmp_path)
    with pytest.raises(ToolError, match="escapes"):
        executor.execute("create", path="../bad.txt", content="no")
    with pytest.raises(ToolError, match="Unknown"):
        executor.execute("unknown")


def test_executor_create_update_rules_and_wrap_error(tmp_path):
    executor = SharedExecutor(tmp_path)
    executor.execute("create", path="a.txt", content="one")
    with pytest.raises(ToolError, match="already exists"):
        executor.execute("create", path="a.txt", content="two")
    with pytest.raises(ToolError, match="does not exist"):
        executor.execute("update", path="missing.txt", content="two")


def test_executor_shell_uses_structured_argv_and_reports_exit_code(tmp_path):
    result = SharedExecutor(tmp_path, shell_approver=lambda _request: True).execute(
        "shell",
        program=sys.executable,
        args=["-c", "print('ok')"],
        reason="test structured execution",
    )
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "ok"
    assert result["cwd"] == "."


def test_executor_denies_shell_by_default_and_exposes_request_to_approver(tmp_path):
    arguments = {
        "program": sys.executable,
        "args": ["-c", "print('no')"],
        "reason": "exercise approval",
    }
    with pytest.raises(ToolError, match="not approved"):
        SharedExecutor(tmp_path).execute("shell", **arguments)

    requests = []
    executor = SharedExecutor(tmp_path, shell_approver=lambda request: requests.append(request) or False)
    with pytest.raises(ToolError, match="not approved"):
        executor.execute("shell", **arguments)
    assert requests[0].args == ("-c", "print('no')")
    assert requests[0].reason == "exercise approval"


def test_executor_command_rule_requires_argument_prefix(tmp_path):
    executor = SharedExecutor(
        tmp_path,
        shell_rules=[CommandRule(Path(sys.executable).name, ("-c", "print('allowed')"))],
    )
    result = executor.execute(
        "shell",
        program=sys.executable,
        args=["-c", "print('allowed')"],
        reason="pre-authorized test",
    )
    assert result["stdout"].strip() == "allowed"
    with pytest.raises(ToolError, match="not approved"):
        executor.execute(
            "shell",
            program=sys.executable,
            args=["-c", "print('different')"],
            reason="not covered by the rule",
        )


def test_executor_command_rule_with_path_does_not_match_by_basename(tmp_path):
    fake_python = tmp_path / Path(sys.executable).name
    fake_python.write_text("not executable", encoding="utf-8")
    request_was_seen = []
    executor = SharedExecutor(
        tmp_path,
        shell_rules=[CommandRule(sys.executable, ("-V",))],
        shell_approver=lambda request: request_was_seen.append(request) or False,
    )
    with pytest.raises(ToolError, match="not approved"):
        executor.execute(
            "shell",
            program=f"./{fake_python.name}",
            args=["-V"],
            reason="must not match the absolute executable rule",
        )
    assert request_was_seen


def test_executor_shell_does_not_interpret_shell_metacharacters(tmp_path):
    marker = tmp_path / "marker"
    result = SharedExecutor(tmp_path, shell_approver=lambda _request: True).execute(
        "shell",
        program=sys.executable,
        args=["-c", "import sys; print(sys.argv[1])", f"; touch {marker}"],
        reason="verify literal argv",
    )
    assert result["stdout"].strip() == f"; touch {marker}"
    assert not marker.exists()


def test_executor_shell_hides_unapproved_environment_and_limits_output(tmp_path, monkeypatch):
    monkeypatch.setenv("MAU_SECRET", "must-not-leak")
    executor = SharedExecutor(
        tmp_path,
        shell_approver=lambda _request: True,
        output_limit_bytes=8,
    )
    result = executor.execute(
        "shell",
        program=sys.executable,
        args=[
            "-c",
            "import os; print(os.environ.get('MAU_SECRET', 'hidden')); print('x' * 100)",
        ],
        reason="verify environment and output boundaries",
    )
    assert "must-not-leak" not in result["stdout"]
    assert len(result["stdout"].encode()) <= 8
    assert result["stdout_truncated"] is True


def test_executor_shell_rejects_bad_operands_and_cwd_escape(tmp_path):
    executor = SharedExecutor(tmp_path, shell_approver=lambda _request: True)
    with pytest.raises(ToolError, match="reason"):
        executor.execute("shell", program=sys.executable)
    with pytest.raises(ToolError, match="args"):
        executor.execute(
            "shell", program=sys.executable, args="-V", reason="invalid unstructured args"
        )
    with pytest.raises(ToolError, match="escapes"):
        executor.execute(
            "shell", program=sys.executable, cwd="..", reason="invalid working directory"
        )
