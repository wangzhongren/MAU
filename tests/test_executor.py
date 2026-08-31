import pytest

from mau_flow import SharedExecutor, ToolError


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


def test_executor_shell_uses_workspace_and_reports_exit_code(tmp_path):
    result = SharedExecutor(tmp_path).execute("shell", command="python -c \"print('ok')\"")
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "ok"
