import pytest

from mau_flow import OpenAIPlanner
from mau_flow.openai_planner import _PROTOCOL


def test_protocol_is_named_mau_isa_and_avoids_tool_call_vocabulary():
    assert "MAU-ISA" in _PROTOCOL
    assert "tool_call" not in _PROTOCOL
    assert "<read><path>relative/path</path></read>" in _PROTOCOL


def test_openai_planner_parses_instruction_without_network():
    decision = OpenAIPlanner._parse_response("prefix <read><path>a.txt</path></read> suffix")
    assert decision.parse_operation() == ("read", {"path": "a.txt"})


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


def test_openai_planner_normalizes_raw_instruction_characters():
    decision = OpenAIPlanner._parse_response(
        "<shell><command>python a.py && echo x < input.txt</command>"
        "<timeout_seconds>30</timeout_seconds></shell>"
    )
    assert decision.parse_operation() == (
        "shell",
        {"command": "python a.py && echo x < input.txt", "timeout_seconds": "30"},
    )


def test_openai_planner_normalizes_deepseek_dsml_prefixes():
    decision = OpenAIPlanner._parse_response(
        "<｜DSML｜shell><｜DSML｜command><｜DSML｜CDATA[echo a && echo b]]>"
        "</｜DSML｜command><｜DSML｜timeout_seconds>30</｜DSML｜timeout_seconds>"
        "</｜DSML｜shell>"
    )
    assert decision.parse_operation() == (
        "shell",
        {"command": "echo a && echo b", "timeout_seconds": "30"},
    )
