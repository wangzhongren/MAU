import pytest

from mau_flow import SharedExecutor, ToolError


def test_executor_read_write(tmp_path):
    executor = SharedExecutor(tmp_path)
    result = executor.execute("write_file", path="nested/a.txt", content="hello")
    assert result["status"] == "ok"
    assert executor.execute("read_file", path="nested/a.txt")["content"] == "hello"


def test_executor_rejects_escape_and_unknown_tool(tmp_path):
    executor = SharedExecutor(tmp_path)
    with pytest.raises(ToolError, match="escapes"):
        executor.execute("write_file", path="../bad.txt", content="no")
    with pytest.raises(ToolError, match="Unknown"):
        executor.execute("shell", command="whoami")


def test_executor_register_replace_and_wrap_error(tmp_path):
    executor = SharedExecutor(tmp_path)
    with pytest.raises(ValueError, match="already registered"):
        executor.register("read_file", dict)
    executor.register("custom", lambda value: {"value": value})
    assert executor.execute("custom", value=3) == {"value": 3}

    def broken():
        raise RuntimeError("boom")

    executor.register("broken", broken)
    with pytest.raises(ToolError, match="boom") as caught:
        executor.execute("broken")
    assert isinstance(caught.value.__cause__, RuntimeError)
