"""OpenAI-compatible planner using the MAU-ISA wire format."""

from __future__ import annotations

import json
import re
from typing import Any
from xml.etree import ElementTree

from .config import OpenAISettings
from .planner import PlannerDecision

_PROTOCOL = """
You execute the MAU-ISA instruction set. Every response MUST be exactly ONE of the six
forms below. Output the element only: no prose, no Markdown fence, and no JSON.

1. <create><path>relative/path</path><content><![CDATA[file content]]></content></create>
2. <read><path>relative/path</path></read>
3. <update><path>relative/path</path><content><![CDATA[new file content]]></content></update>
4. <delete><path>relative/path</path></delete>
5. <shell><command><![CDATA[command text]]></command><timeout_seconds>30</timeout_seconds></shell>
6. <handoff status="SUCCESS"><summary>verified result</summary><decision>optional decision</decision><open_issue>optional issue</open_issue></handoff>

Rules:
- The executable opcodes are exactly create, read, update, delete, and shell.
- Use the exact child elements shown for the selected opcode and omit all others.
- A path is relative to the execution workspace.
- Always wrap content and command values in CDATA.
- timeout_seconds is optional for shell and must be between 0 and 300.
- Handoff status is exactly SUCCESS, PARTIAL, or FAILED. decision and open_issue may repeat.
- Do not claim SUCCESS until execution output has verified the result.
""".strip()


class OpenAIPlanner:
    """Synchronous planner for OpenAI-compatible chat-completions endpoints."""

    def __init__(self, settings: OpenAISettings | None = None, *, timeout: float = 60):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError('Install the OpenAI adapter with: pip install -e ".[openai]"') from exc

        self.settings = settings or OpenAISettings.from_environment()
        self.client = OpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=timeout,
        )

    def plan(self, messages: list[dict[str, Any]]) -> PlannerDecision:
        request_messages: list[dict[str, str]] = [
            {"role": "system", "content": _PROTOCOL}
        ]
        for message in messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            if role == "tool":
                name = message.get("name", "unknown")
                request_messages.append(
                    {"role": "user", "content": f"MAU-ISA result for opcode {name}:\n{content}"}
                )
            else:
                request_messages.append(
                    {"role": role if role in {"system", "user", "assistant"} else "user", "content": content}
                )

        response = self.client.chat.completions.create(
            model=self.settings.model,
            messages=request_messages,  # type: ignore[arg-type]
            max_tokens=self.settings.max_output_tokens,
        )
        content = response.choices[0].message.content
        if not content:
            return self._protocol_error("model returned empty output")
        try:
            return self._parse_response(content)
        except (ValueError, ElementTree.ParseError) as exc:
            return self._protocol_error(str(exc))

    @staticmethod
    def _protocol_error(reason: str) -> PlannerDecision:
        return PlannerDecision.execute(
            "protocol_error", error=f"Invalid XML-like response: {reason}"
        )

    @staticmethod
    def _parse_response(content: str) -> PlannerDecision:
        text = OpenAIPlanner._normalize_dsml(content.strip())
        tags = ("create", "read", "update", "delete", "shell", "handoff")
        start_candidates = [(text.find(f"<{tag}"), tag) for tag in tags]
        start_candidates = [(index, tag) for index, tag in start_candidates if index >= 0]
        if not start_candidates:
            escaped = text[:200].encode("unicode_escape").decode("ascii")
            raise ValueError(f"model returned no supported XML element: {escaped}")
        start, tag = min(start_candidates)
        text = text[start:]
        closing = f"</{tag}>"
        end = text.find(closing)
        if end < 0:
            raise ValueError("model returned incomplete XML")
        xml = text[: end + len(closing)]
        if tag != "handoff":
            decision = PlannerDecision(action="execute", operation=xml)
            try:
                decision.parse_operation()
            except ValueError:
                decision = OpenAIPlanner._normalize_operation(xml, tag)
            decision.parse_operation()
            return decision
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

    @staticmethod
    def _normalize_dsml(text: str) -> str:
        """Remove DeepSeek V4 DSML prefixes leaked into XML-like message content."""

        text = text.replace("<｜DSML｜CDATA[", "<![CDATA[")
        return text.replace("<｜DSML｜", "<").replace("</｜DSML｜", "</")

    @staticmethod
    def _normalize_operation(xml: str, opcode: str) -> PlannerDecision:
        """Normalize model-authored MAU-ISA values containing raw XML characters."""

        root_match = re.fullmatch(
            rf"\s*<{opcode}\s*>(?P<body>.*)</{opcode}>\s*", xml, flags=re.DOTALL
        )
        if not root_match:
            raise ValueError(f"invalid MAU-ISA opcode root: {opcode}")
        body = root_match.group("body")
        argument_pattern = re.compile(
            r"\s*<(?P<tag>[A-Za-z_][A-Za-z0-9_]*)>(?P<value>.*?)</(?P=tag)>",
            flags=re.DOTALL,
        )
        arguments: dict[str, str] = {}
        position = 0
        while position < len(body):
            match = argument_pattern.match(body, position)
            if not match:
                if body[position:].strip():
                    raise ValueError("invalid XML-like tool arguments")
                break
            tag = match.group("tag")
            if tag in arguments:
                raise ValueError(f"duplicate tool argument: {tag}")
            value = match.group("value")
            if value.startswith("<![CDATA[") and value.endswith("]]>"):
                value = value[9:-3]
            arguments[tag] = value
            position = match.end()
        return PlannerDecision.execute(opcode, **arguments)
