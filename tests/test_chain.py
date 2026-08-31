import pytest

from mau_flow.chain import DynamicChainGenerator, MAUSpec, load_chain, save_chain


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
