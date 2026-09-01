import pytest

from mau_flow import OpenAIPlanner, PlannerDecision
from mau_flow.openai_planner import _HANDOFF_PROTOCOL, _PROTOCOL


def test_protocol_is_named_mau_isa_and_avoids_tool_call_vocabulary():
    assert "MAU-ISA" in _PROTOCOL
    assert "tool_call" not in _PROTOCOL
    assert "<read><path>relative/path</path><path>another/path</path></read>" in _PROTOCOL
    assert "<program>python</program>" in _PROTOCOL
    assert "<arg>-m</arg>" in _PROTOCOL
    assert "<read>" not in _HANDOFF_PROTOCOL
    assert "<shell>" not in _HANDOFF_PROTOCOL


def test_openai_planner_parses_instruction_without_network():
    decision = OpenAIPlanner._parse_response("prefix <read><path>a.txt</path></read> suffix")
    assert decision.parse_operation() == ("read", {"path": "a.txt"})


def test_openai_planner_parses_multi_file_read_without_network():
    decision = OpenAIPlanner._parse_response(
        "<read><path>a.txt</path><path>b.txt</path></read>"
    )
    assert decision.parse_operation() == ("read", {"paths": ["a.txt", "b.txt"]})


def test_planner_decision_builds_multi_file_read():
    decision = PlannerDecision.execute("read", paths=["a.txt", "b.txt"])
    assert decision.parse_operation() == ("read", {"paths": ["a.txt", "b.txt"]})


def test_openai_planner_parses_handoff_without_network():
    decision = OpenAIPlanner._parse_response(
        '<handoff status="SUCCESS"><summary>done</summary><decision>tested</decision></handoff>'
    )
    assert decision.final_handoff == {
        "summary": "done",
        "status": "SUCCESS",
        "decisions": ["tested"],
        "open_issues": [],
    }


def test_openai_planner_rejects_non_xml_response():
    with pytest.raises(ValueError, match="no supported XML"):
        OpenAIPlanner._parse_response("done")


def test_protocol_error_is_a_valid_internal_instruction():
    decision = OpenAIPlanner._protocol_error("broken")
    assert decision.parse_operation() == (
        "protocol_error",
        {"error": "Invalid XML-like response: broken"},
    )


def test_openai_planner_parses_structured_shell_instruction():
    decision = OpenAIPlanner._parse_response(
        "<shell><program>python</program><arg>-m</arg><arg>pytest</arg>"
        "<cwd>.</cwd><timeout_seconds>30</timeout_seconds>"
        "<reason>Run tests</reason></shell>"
    )
    assert decision.parse_operation() == (
        "shell",
        {
            "program": "python",
            "args": ["-m", "pytest"],
            "cwd": ".",
            "timeout_seconds": "30",
            "reason": "Run tests",
        },
    )


def test_openai_planner_normalizes_deepseek_dsml_prefixes():
    decision = OpenAIPlanner._parse_response(
        "<｜DSML｜shell><｜DSML｜program>python</｜DSML｜program>"
        "<｜DSML｜arg>-V</｜DSML｜arg>"
        "<｜DSML｜reason>Check Python</｜DSML｜reason>"
        "</｜DSML｜shell>"
    )
    assert decision.parse_operation() == (
        "shell",
        {"program": "python", "args": ["-V"], "reason": "Check Python"},
    )


def test_planner_decision_builds_repeated_shell_args():
    decision = PlannerDecision.execute(
        "shell", program="python", args=["-m", "pytest"], reason="Run tests"
    )
    assert decision.parse_operation() == (
        "shell",
        {"program": "python", "args": ["-m", "pytest"], "reason": "Run tests"},
    )


def test_planner_rejects_unstructured_shell_args_operand():
    decision = PlannerDecision(
        action="execute",
        operation=(
            "<shell><program>python</program><args>-m pytest</args>"
            "<reason>Run tests</reason></shell>"
        ),
    )
    with pytest.raises(ValueError, match="repeated <arg>"):
        decision.parse_operation()


def test_handoff_only_planner_accepts_handoff_and_rejects_opcode(monkeypatch):
    planner = object.__new__(OpenAIPlanner)
    monkeypatch.setattr(
        planner,
        "_complete",
        lambda _messages, _protocol: '<handoff status="PARTIAL"><summary>paths checked</summary></handoff>',
    )
    assert planner.plan_handoff([]).final_handoff["summary"] == "paths checked"

    monkeypatch.setattr(
        planner,
        "_complete",
        lambda _messages, _protocol: "<read><path>more.txt</path></read>",
    )
    decision = planner.plan_handoff([])
    assert decision.parse_operation()[0] == "protocol_error"
