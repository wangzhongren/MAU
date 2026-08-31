"""Dynamic MAU-chain generation and materialization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from xml.etree import ElementTree

from .contracts import BaseHandoff
from .executor import SharedExecutor
from .mau import MAU
from .openai_planner import OpenAIPlanner
from .orchestrator import Pipeline

BUILTIN_TOOLS = frozenset({"create", "read", "update", "delete", "shell"})


@dataclass(frozen=True)
class MAUSpec:
    name: str
    purpose: str
    tools: frozenset[str]


class DynamicChainGenerator:
    def __init__(self, planner: OpenAIPlanner | None = None):
        self.planner = planner or OpenAIPlanner()

    def generate(
        self,
        *,
        workspace: Path,
        target: str | None,
        task: str,
        max_agents: int,
        context_chars: int,
    ) -> list[MAUSpec]:
        if max_agents < 1:
            raise ValueError("max_agents must be at least 1")
        context = self._context(workspace, target, context_chars)
        prompt = f"""
Design the smallest useful sequential MAU chain for the task below. The chain length is
dynamic: choose it from 1 through {max_agents} based on actual complexity. Do not always
choose the same length. Each MAU must own one clear responsibility.

Return exactly this XML shape and no prose or Markdown:
<mau_chain>
  <mau name="inspect"><purpose>Inspect the target and identify required work.</purpose><tools><tool>read</tool><tool>shell</tool></tools></mau>
  <mau name="implement"><purpose>Implement the required changes.</purpose><tools><tool>create</tool><tool>read</tool><tool>update</tool><tool>shell</tool></tools></mau>
  <mau name="verify"><purpose>Run independent verification and fix defects.</purpose><tools><tool>read</tool><tool>update</tool><tool>shell</tool></tools></mau>
</mau_chain>

The example has three MAUs only because that example needs three. A complex task may need
five specialized MAUs; a trivial task may need one or two. Names must be unique lowercase
identifiers. Tools may only be create, read, update, delete, shell. Give write tools only to
MAUs that must modify files. Every chain that changes files must end with a verification MAU.

Task: {task}
Target: {target or '(workspace task)'}
Workspace context:
{context}
""".strip()
        response = self.planner.client.chat.completions.create(
            model=self.planner.settings.model,
            messages=[{"role": "system", "content": prompt}],
            max_tokens=self.planner.settings.max_output_tokens,
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("model returned an empty MAU chain")
        specs = self.parse(content)
        if len(specs) > max_agents:
            raise ValueError(f"model generated {len(specs)} MAUs; limit is {max_agents}")
        return specs

    @staticmethod
    def parse(content: str) -> list[MAUSpec]:
        start = content.find("<mau_chain>")
        end = content.find("</mau_chain>")
        if start < 0 or end < 0:
            raise ValueError("model returned no complete <mau_chain>")
        root = ElementTree.fromstring(content[start : end + len("</mau_chain>")])
        specs: list[MAUSpec] = []
        names: set[str] = set()
        for element in root.findall("mau"):
            name = element.attrib.get("name", "").strip()
            purpose = element.findtext("purpose", "").strip()
            tools = frozenset(item.text.strip() for item in element.findall("tools/tool") if item.text)
            if not name or not name.replace("_", "").replace("-", "").isalnum():
                raise ValueError(f"invalid MAU name: {name!r}")
            if name in names:
                raise ValueError(f"duplicate MAU name: {name}")
            if not purpose:
                raise ValueError(f"MAU {name} has no purpose")
            if not tools or not tools <= BUILTIN_TOOLS:
                raise ValueError(f"MAU {name} has invalid tools: {sorted(tools)}")
            names.add(name)
            specs.append(MAUSpec(name=name, purpose=purpose, tools=tools))
        if not specs:
            raise ValueError("MAU chain is empty")
        return specs

    @staticmethod
    def _context(workspace: Path, target: str | None, limit: int) -> str:
        if limit < 0:
            raise ValueError("context_chars must not be negative")
        if target:
            path = workspace / target
            if not path.exists():
                return "Target file does not exist yet."
            text = path.read_text(encoding="utf-8", errors="replace")
            return text[:limit] + ("\n...[truncated]" if len(text) > limit else "")
        names = [path.relative_to(workspace).as_posix() for path in workspace.rglob("*") if path.is_file()]
        return "Workspace files:\n" + "\n".join(names[:200])


def save_chain(
    path: Path, *, workspace: Path, task: str, target: str | None, specs: list[MAUSpec]
) -> None:
    root = ElementTree.Element("mau_chain")
    workspace_element = ElementTree.SubElement(root, "workspace")
    workspace_element.text = str(workspace.resolve())
    task_element = ElementTree.SubElement(root, "task")
    task_element.text = task
    if target:
        target_element = ElementTree.SubElement(root, "target")
        target_element.text = target
    for spec in specs:
        mau_element = ElementTree.SubElement(root, "mau", {"name": spec.name})
        purpose = ElementTree.SubElement(mau_element, "purpose")
        purpose.text = spec.purpose
        tools = ElementTree.SubElement(mau_element, "tools")
        for name in sorted(spec.tools):
            tool = ElementTree.SubElement(tools, "tool")
            tool.text = name
    ElementTree.indent(root, space="  ")
    path.write_text(ElementTree.tostring(root, encoding="unicode") + "\n", encoding="utf-8")


def load_chain(path: Path) -> tuple[Path, str, str | None, list[MAUSpec]]:
    content = path.read_text(encoding="utf-8")
    root = ElementTree.fromstring(content)
    if root.tag != "mau_chain":
        raise ValueError("chain file root must be <mau_chain>")
    workspace_text = root.findtext("workspace", "").strip()
    if not workspace_text:
        raise ValueError("chain file has no workspace")
    workspace = Path(workspace_text).resolve()
    if not workspace.is_dir():
        raise ValueError(f"chain workspace does not exist: {workspace}")
    task = root.findtext("task", "").strip()
    if not task:
        raise ValueError("chain file has no task")
    target = root.findtext("target", "").strip() or None
    return workspace, task, target, DynamicChainGenerator.parse(content)


def materialize_chain(
    *,
    workspace: Path,
    target: str | None,
    task: str,
    specs: list[MAUSpec],
    on_round: Callable[[str, int, str, str], None] | None = None,
) -> Pipeline:
    executor = SharedExecutor(workspace)
    pipeline = Pipeline(max_node_visits=len(specs))
    target_text = target or "the workspace task"
    for spec in specs:
        pipeline.add_mau(
            MAU(
                name=spec.name,
                system_prompt=(
                    f"User task: {task}\nTarget: {target_text}\nYour responsibility: {spec.purpose}\n"
                    "Use the previous MAU handoff as context. Work only inside the workspace, "
                    "perform your responsibility, verify your own claims, and return a concise handoff."
                ),
                handoff_schema=BaseHandoff,
                executor=executor,
                planner=OpenAIPlanner(),
                allowed_tools=set(spec.tools),
                on_round=on_round,
            )
        )
    for source, target_name in pairwise(specs):
        pipeline.add_edge(source.name, target_name.name)
    return pipeline
