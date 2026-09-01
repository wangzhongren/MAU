import pytest

from mau_flow import BaseHandoff, PlannerDecision, Status
from mau_flow.chain import (
    DynamicChainGenerator,
    MAUSpec,
    load_chain,
    materialize_chain,
    save_chain,
)
from mau_flow.sandbox import TransactionalExecutor


def test_dynamic_chain_parser_accepts_variable_lengths():
    three = """<mau_chain>
    <mau name="inspect"><purpose>Inspect</purpose><tools><tool>read</tool></tools></mau>
    <mau name="code"><purpose>Code</purpose><tools><tool>update</tool></tools></mau>
    <mau name="test"><purpose>Test</purpose><tools><tool>shell</tool></tools></mau>
    </mau_chain>"""
    assert len(DynamicChainGenerator.parse(three)) == 3
    five = three.replace("</mau_chain>", """
    <mau name="security"><purpose>Audit</purpose><tools><tool>read</tool></tools></mau>
    <mau name="final"><purpose>Verify</purpose><tools><tool>shell</tool></tools></mau>
    </mau_chain>""")
    assert len(DynamicChainGenerator.parse(five)) == 5


def test_dynamic_chain_parser_rejects_unknown_tool_and_duplicate_name():
    invalid = """<mau_chain>
    <mau name="one"><purpose>One</purpose><tools><tool>network</tool></tools></mau>
    </mau_chain>"""
    with pytest.raises(ValueError, match="invalid tools"):
        DynamicChainGenerator.parse(invalid)
    duplicate = """<mau_chain>
    <mau name="one"><purpose>One</purpose><tools><tool>read</tool></tools></mau>
    <mau name="one"><purpose>Again</purpose><tools><tool>shell</tool></tools></mau>
    </mau_chain>"""
    with pytest.raises(ValueError, match="duplicate"):
        DynamicChainGenerator.parse(duplicate)


def test_saved_chain_roundtrip(tmp_path):
    path = tmp_path / ".mau-flow-chain.xml"
    specs = [MAUSpec("code", "Implement it", frozenset({"read", "update"}))]
    save_chain(path, workspace=tmp_path, task="finish", target="todo.py", specs=specs)
    assert load_chain(path) == (tmp_path.resolve(), "finish", "todo.py", specs)


def test_materialized_chain_stops_after_non_success_handoff(tmp_path, monkeypatch):
    class OneDecisionPlanner:
        def __init__(self, decision):
            self.decision = decision
            self.calls = 0

        def plan(self, _messages):
            self.calls += 1
            return self.decision

    decisions = [
        PlannerDecision(
            action="done",
            final_handoff={"summary": "inspection incomplete", "status": "PARTIAL"},
        ),
        PlannerDecision(
            action="done",
            final_handoff={"summary": "must not run", "status": "SUCCESS"},
        ),
    ]
    planners = []

    def planner_factory():
        planner = OneDecisionPlanner(decisions[len(planners)])
        planners.append(planner)
        return planner

    monkeypatch.setattr("mau_flow.chain.OpenAIPlanner", planner_factory)
    specs = [
        MAUSpec("inspect", "Inspect", frozenset({"read"})),
        MAUSpec("implement", "Implement", frozenset({"read", "update"})),
    ]
    pipeline = materialize_chain(
        workspace=tmp_path,
        target=None,
        task="finish",
        specs=specs,
    )

    result = pipeline.execute(
        BaseHandoff(summary="start", status=Status.SUCCESS),
        start="inspect",
        max_rounds_per_agent=5,
    )

    assert result.status == Status.PARTIAL
    assert planners[0].calls == 1
    assert planners[1].calls == 0


def test_materialized_chain_uses_one_transaction_per_mau(tmp_path, monkeypatch):
    class UnusedPlanner:
        def plan(self, _messages):  # pragma: no cover - pipeline is not executed
            raise AssertionError("not called")

    monkeypatch.setattr("mau_flow.chain.OpenAIPlanner", UnusedPlanner)
    specs = [
        MAUSpec("inspect", "Inspect", frozenset({"read"})),
        MAUSpec("implement", "Implement", frozenset({"update"})),
    ]
    pipeline = materialize_chain(
        workspace=tmp_path,
        target=None,
        task="finish",
        specs=specs,
    )

    first = pipeline.nodes["inspect"].executor
    second = pipeline.nodes["implement"].executor
    assert isinstance(first, TransactionalExecutor)
    assert isinstance(second, TransactionalExecutor)
    assert first is not second

    host_pipeline = materialize_chain(
        workspace=tmp_path,
        target=None,
        task="finish",
        specs=specs,
        sandbox_mode="host",
    )
    assert host_pipeline.nodes["inspect"].executor is host_pipeline.nodes["implement"].executor
