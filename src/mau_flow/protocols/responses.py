"""MAU handoff parsing; action XML-like decoding is provided by ActUnit."""

from xml.etree import ElementTree

from actunit import XmlActionCodec

from ..planner import PlannerDecision


class XmlResponseCodec:
    @staticmethod
    def _parse_response(content: str) -> PlannerDecision:
        tag, xml = XmlActionCodec.extract_element(
            content, ("create", "read", "update", "delete", "shell", "handoff")
        )
        if tag != "handoff":
            call = XmlActionCodec().decode(xml)
            return PlannerDecision(
                action="execute", tool_call=call, operation=XmlActionCodec().encode(call)
            )
        root = ElementTree.fromstring(xml)
        if set(root.attrib) != {"status"}:
            raise ValueError("handoff requires exactly one status attribute")
        summary = root.findtext("summary", "").strip()
        if not summary:
            raise ValueError("handoff requires a non-empty summary")
        return PlannerDecision(
            action="done",
            final_handoff={
                "summary": summary,
                "status": root.attrib["status"],
                "decisions": [item.text or "" for item in root.findall("decision")],
                "open_issues": [item.text or "" for item in root.findall("open_issue")],
            },
        )

    _normalize_dsml = staticmethod(XmlActionCodec.normalize_dsml)

    @staticmethod
    def _normalize_operation(xml: str, opcode: str) -> PlannerDecision:
        operation = XmlActionCodec.normalize_operation(xml, opcode)
        return PlannerDecision(action="execute", operation=operation)
