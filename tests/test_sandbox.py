import sys

import pytest

from mau_flow import MAU, BaseHandoff, Pipeline, PlannerDecision, Status
from mau_flow.sandbox import SandboxViolation, TransactionalExecutor


class QueuePlanner:
    def __init__(self, *decisions):
        self.decisions = iter(decisions)

    def plan(self, _messages):
        return next(self.decisions)


class RecordingPlanner(QueuePlanner):
    def __init__(self, *decisions):
        super().__init__(*decisions)
        self.calls = []

    def plan(self, messages):
        self.calls.append(list(messages))
        return super().plan(messages)


def done(summary="done", status="SUCCESS"):
    return PlannerDecision(
        action="done",
        final_handoff={"summary": summary, "status": status},
    )


def test_transaction_merges_authorized_successful_diff(tmp_path):
    (tmp_path / "existing.txt").write_text("before", encoding="utf-8")
    executor = TransactionalExecutor(
        tmp_path,
        allowed_tools={"create", "update"},
    )

    executor.execute("update", path="existing.txt", content="after")
    executor.execute("create", path="new.txt", content="new")

    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "before"
    assert not (tmp_path / "new.txt").exists()

    diff = executor.finalize(Status.SUCCESS)

    assert diff is not None and diff.merged is True
    assert diff.created == ("new.txt",)
    assert diff.updated == ("existing.txt",)
    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "after"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "new"


def test_transaction_discards_non_successful_diff(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("before", encoding="utf-8")
    executor = TransactionalExecutor(tmp_path, allowed_tools={"update"})

    executor.execute("update", path="existing.txt", content="after")
    diff = executor.finalize(Status.PARTIAL)

    assert diff is not None and diff.merged is False
    assert diff.updated == ("existing.txt",)
    assert target.read_text(encoding="utf-8") == "before"


def test_transaction_rejects_concurrent_canonical_change(tmp_path):
    target = tmp_path / "shared.txt"
    target.write_text("baseline", encoding="utf-8")
    executor = TransactionalExecutor(tmp_path, allowed_tools={"update"})
    executor.execute("update", path="shared.txt", content="agent")
    target.write_text("user", encoding="utf-8")

    with pytest.raises(SandboxViolation, match="concurrently"):
        executor.finalize(Status.SUCCESS)
    assert target.read_text(encoding="utf-8") == "user"


def test_diff_gate_blocks_shell_delete_without_delete_capability(tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("keep", encoding="utf-8")
    executor = TransactionalExecutor(
        tmp_path,
        allowed_tools={"shell"},
        shell_approver=lambda _request: True,
    )

    result = executor.execute(
        "shell",
        program=sys.executable,
        args=["-c", "from pathlib import Path; Path('keep.txt').unlink()"],
        reason="simulate an rm bypass",
    )
    assert result["exit_code"] == 0

    with pytest.raises(SandboxViolation, match="disallowed delete"):
        executor.finalize(Status.SUCCESS)
    assert target.read_text(encoding="utf-8") == "keep"


def test_diff_gate_rejects_protected_files_and_symbolic_links(tmp_path):
    (tmp_path / ".env").write_text("SECRET=original", encoding="utf-8")
    executor = TransactionalExecutor(tmp_path, allowed_tools={"update"})
    executor.execute("update", path=".env", content="SECRET=changed")
    with pytest.raises(SandboxViolation, match="protected"):
        executor.finalize(Status.SUCCESS)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SECRET=original"

    executor = TransactionalExecutor(
        tmp_path,
        allowed_tools={"create", "shell"},
        shell_approver=lambda _request: True,
    )
    executor.execute(
        "shell",
        program=sys.executable,
        args=["-c", "from pathlib import Path; Path('link').symlink_to('target')"],
        reason="create a symbolic link",
    )
    with pytest.raises(SandboxViolation, match="symbolic link"):
        executor.finalize(Status.SUCCESS)
    assert not (tmp_path / "link").exists()


def test_mau_turns_rejected_snapshot_diff_into_partial_handoff(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("before", encoding="utf-8")
    executor = TransactionalExecutor(tmp_path, allowed_tools=set())
    mau = MAU(
        name="writer",
        system_prompt="write",
        handoff_schema=BaseHandoff,
        executor=executor,
        planner=QueuePlanner(
            PlannerDecision.execute("update", path="file.txt", content="after"),
            done("claimed success"),
        ),
        allowed_tools={"update"},
    )

    result = mau.run(BaseHandoff(summary="start", status=Status.SUCCESS), max_rounds=5)

    assert result.status == Status.PARTIAL
    assert "Sandbox diff was rejected" in result.open_issues[-1]
    assert target.read_text(encoding="utf-8") == "before"


def test_sequential_maus_snapshot_after_upstream_merge(tmp_path):
    first_planner = QueuePlanner(
        PlannerDecision.execute("create", path="handoff.txt", content="from first"),
        done("created"),
    )
    second_planner = RecordingPlanner(
        PlannerDecision.execute("read", path="handoff.txt"),
        done("observed"),
    )
    first = MAU(
        name="first",
        system_prompt="create",
        handoff_schema=BaseHandoff,
        executor=TransactionalExecutor(tmp_path, allowed_tools={"create"}),
        planner=first_planner,
        allowed_tools={"create"},
    )
    second = MAU(
        name="second",
        system_prompt="read",
        handoff_schema=BaseHandoff,
        executor=TransactionalExecutor(tmp_path, allowed_tools={"read"}),
        planner=second_planner,
        allowed_tools={"read"},
    )
    pipeline = Pipeline().add_mau(first).add_mau(second).add_edge("first", "second")

    result = pipeline.execute(
        BaseHandoff(summary="start", status=Status.SUCCESS),
        start="first",
        max_rounds_per_agent=5,
    )

    assert result.summary == "observed"
    tool_messages = [message for message in second_planner.calls[1] if message["role"] == "tool"]
    assert tool_messages[0]["content"]["content"] == "from first"
