import pytest

from mau_flow import (
    MAU,
    BaseHandoff,
    Pipeline,
    PipelineError,
    PlannerDecision,
    SharedExecutor,
    Status,
    ValidationError,
)


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


def done(summary):
    return PlannerDecision(action="done", final_handoff={"summary": summary, "status": "SUCCESS"})


def test_mau_executes_tool_then_hands_off(tmp_path):
    planner = QueuePlanner(
        PlannerDecision.execute("create", path="x", content="1"),
        done("written"),
    )
    mau = MAU(
        name="writer",
        system_prompt="write",
        handoff_schema=BaseHandoff,
        executor=SharedExecutor(tmp_path),
        planner=planner,
    )
    result = mau.run(BaseHandoff(summary="input", status=Status.SUCCESS), max_rounds=5)
    assert result.summary == "written"
    assert (tmp_path / "x").read_text() == "1"


def test_validation_failure_returns_to_planner(tmp_path):
    planner = RecordingPlanner(done("bad"), done("good"))
    mau = MAU(
        name="validator",
        system_prompt="validate",
        handoff_schema=BaseHandoff,
        executor=SharedExecutor(tmp_path),
        planner=planner,
        validators=[lambda handoff: (handoff.summary == "good", "summary must be good")],
    )
    assert mau.run(BaseHandoff(summary="input", status=Status.SUCCESS), max_rounds=5).summary == "good"
    assert planner.calls[1][-2]["role"] == "assistant"
    assert planner.calls[1][-1]["role"] == "system"


def test_empty_tool_allowlist_denies_every_tool(tmp_path):
    planner = RecordingPlanner(
        PlannerDecision.execute("create", path="forbidden", content="no"),
        done("finished"),
    )
    mau = MAU(
        name="no-tools",
        system_prompt="",
        handoff_schema=BaseHandoff,
        executor=SharedExecutor(tmp_path),
        planner=planner,
        allowed_tools=set(),
    )
    mau.run(BaseHandoff(summary="input", status=Status.SUCCESS), max_rounds=5)
    assert not (tmp_path / "forbidden").exists()
    assert planner.calls[1][-2]["content"]["error"] == "opcode is not allowed"
    assert planner.calls[1][-3]["content"].startswith("<create>")
    assert "remaining" in planner.calls[1][-1]["content"]


def test_pipeline_routes_deterministically(tmp_path):
    executor = SharedExecutor(tmp_path)
    first = MAU(name="first", system_prompt="", handoff_schema=BaseHandoff, executor=executor, planner=QueuePlanner(done("one")))
    second = MAU(name="second", system_prompt="", handoff_schema=BaseHandoff, executor=executor, planner=QueuePlanner(done("two")))
    pipeline = Pipeline().add_mau(first).add_mau(second).add_edge("first", "second")
    result = pipeline.execute(BaseHandoff(summary="start", status=Status.SUCCESS), start="first", max_rounds_per_agent=5)
    assert result.summary == "two"


def test_pipeline_rejects_invalid_visit_limit():
    with pytest.raises(ValueError, match="at least 1"):
        Pipeline(max_node_visits=0)


def test_mau_enforces_step_and_validation_retry_limits(tmp_path):
    executor = SharedExecutor(tmp_path)
    endless = QueuePlanner(
        PlannerDecision.execute("read", path="missing")
    )
    mau = MAU(
        name="limited",
        system_prompt="",
        handoff_schema=BaseHandoff,
        executor=executor,
        planner=endless,
    )
    limited_result = mau.run(BaseHandoff(summary="input", status=Status.SUCCESS), max_rounds=1)
    assert limited_result.status == Status.PARTIAL
    assert "round limit" in limited_result.summary

    invalid = MAU(
        name="invalid",
        system_prompt="",
        handoff_schema=BaseHandoff,
        executor=executor,
        planner=QueuePlanner(done("bad")),
        validators=[lambda _handoff: False],
        max_validation_retries=0,
    )
    with pytest.raises(ValidationError, match="exhausted"):
        invalid.run(BaseHandoff(summary="input", status=Status.SUCCESS), max_rounds=1)


def test_pipeline_rejects_bad_graph_duplicate_and_ambiguous_route(tmp_path):
    executor = SharedExecutor(tmp_path)

    def node(name, summary):
        return MAU(
            name=name,
            system_prompt="",
            handoff_schema=BaseHandoff,
            executor=executor,
            planner=QueuePlanner(done(summary)),
        )

    pipeline = Pipeline().add_mau(node("one", "one"))
    with pytest.raises(PipelineError, match="Duplicate"):
        pipeline.add_mau(node("one", "duplicate"))
    with pytest.raises(PipelineError, match="Unknown start"):
        pipeline.execute(BaseHandoff(summary="start", status=Status.SUCCESS), start="missing", max_rounds_per_agent=5)

    bad_edge = Pipeline().add_mau(node("source", "source")).add_edge("source", "missing")
    with pytest.raises(PipelineError, match="unknown nodes"):
        bad_edge.execute(BaseHandoff(summary="start", status=Status.SUCCESS), start="source", max_rounds_per_agent=5)

    ambiguous = (
        Pipeline()
        .add_mau(node("start", "routed"))
        .add_mau(node("left", "left"))
        .add_mau(node("right", "right"))
        .add_edge("start", "left")
        .add_edge("start", "right")
    )
    with pytest.raises(PipelineError, match="Ambiguous"):
        ambiguous.execute(BaseHandoff(summary="input", status=Status.SUCCESS), start="start", max_rounds_per_agent=5)


def test_pipeline_stops_cycle_at_limit(tmp_path):
    executor = SharedExecutor(tmp_path)
    decisions = [done(str(index)) for index in range(4)]
    mau = MAU(
        name="loop",
        system_prompt="",
        handoff_schema=BaseHandoff,
        executor=executor,
        planner=QueuePlanner(*decisions),
    )
    pipeline = Pipeline(max_node_visits=3).add_mau(mau).add_edge("loop", "loop")
    with pytest.raises(PipelineError, match="possible cycle"):
        pipeline.execute(BaseHandoff(summary="start", status=Status.SUCCESS), start="loop", max_rounds_per_agent=5)


def test_round_limit_is_supplied_by_caller(tmp_path):
    mau = MAU(
        name="external-limit",
        system_prompt="",
        handoff_schema=BaseHandoff,
        executor=SharedExecutor(tmp_path),
        planner=QueuePlanner(done("ok")),
    )
    with pytest.raises(ValueError, match="max_rounds"):
        mau.run(BaseHandoff(summary="input", status=Status.SUCCESS), max_rounds=0)


def test_mau_reserves_handoff_rounds_and_rejects_late_opcode(tmp_path):
    planner = RecordingPlanner(
        PlannerDecision.execute("create", path="first.txt", content="one"),
        PlannerDecision.execute("create", path="second.txt", content="two"),
        PlannerDecision.execute("create", path="late.txt", content="must not run"),
        done("concrete handoff"),
    )
    mau = MAU(
        name="bounded",
        system_prompt="",
        handoff_schema=BaseHandoff,
        executor=SharedExecutor(tmp_path),
        planner=planner,
    )

    result = mau.run(BaseHandoff(summary="input", status=Status.SUCCESS), max_rounds=5)

    assert result.summary == "concrete handoff"
    assert (tmp_path / "first.txt").is_file()
    assert (tmp_path / "second.txt").is_file()
    assert not (tmp_path / "late.txt").exists()
    assert any(
        "HANDOFF-ONLY PHASE" in message["content"]
        for message in planner.calls[2]
        if message["role"] == "system"
    )
    assert "Opcode rejected" in planner.calls[3][-2]["content"]
