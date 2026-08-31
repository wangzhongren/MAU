"""Minimal Agent Unit state machine."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Generic, TypeVar

from pydantic import ValidationError as PydanticValidationError

from .contracts import BaseHandoff
from .executor import SharedExecutor, ToolError
from .planner import PlannerProtocol

HandoffT = TypeVar("HandoffT", bound=BaseHandoff)
Validator = Callable[[BaseHandoff], None | bool | tuple[bool, str]]
RoundObserver = Callable[[str, int, str, str], None]


class MAUError(RuntimeError):
    pass


class MaxStepsExceeded(MAUError):
    pass


class ValidationError(MAUError):
    pass


class MAU(Generic[HandoffT]):
    def __init__(
        self,
        *,
        name: str,
        system_prompt: str,
        handoff_schema: type[HandoffT],
        executor: SharedExecutor,
        planner: PlannerProtocol,
        validators: list[Validator] | None = None,
        allowed_tools: set[str] | None = None,
        max_validation_retries: int = 3,
        on_round: RoundObserver | None = None,
    ):
        if max_validation_retries < 0:
            raise ValueError("Invalid state-machine limits")
        self.name = name
        self.system_prompt = system_prompt
        self.handoff_schema = handoff_schema
        self.executor = executor
        self.planner = planner
        self.validators = validators or []
        self.allowed_tools = frozenset(
            executor.tool_names if allowed_tools is None else allowed_tools
        )
        self.max_validation_retries = max_validation_retries
        self.on_round = on_round

    def run(self, input_handoff: BaseHandoff, *, max_rounds: int) -> HandoffT:
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "system",
                "content": (
                    f"Runtime budget: at most {max_rounds} planner rounds. Reserve the final "
                    "round for a handoff and do not issue an opcode on the final round."
                ),
            },
            {"role": "user", "content": input_handoff.model_dump(mode="json")},
        ]
        validation_failures = 0

        for _round in range(max_rounds):
            decision = self.planner.plan(messages)
            if decision.action == "execute":
                try:
                    tool_name, tool_args = decision.parse_operation()
                except ValueError as exc:
                    tool_name, tool_args = "invalid", {}
                    result = {"status": "error", "error": str(exc)}
                else:
                    result = {}
                if tool_name == "invalid":
                    pass
                elif tool_name not in self.allowed_tools:
                    result = {"status": "error", "error": "opcode is not allowed"}
                else:
                    try:
                        result = self.executor.execute(tool_name, **tool_args)
                    except ToolError as exc:
                        result = {"status": "error", "error": str(exc)}
                messages.append({"role": "assistant", "content": decision.operation})
                messages.append({"role": "tool", "name": tool_name, "content": result})
                remaining = max_rounds - (_round + 1)
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"Runtime budget remaining: {remaining} round(s). "
                            + (
                                "The next response MUST be a handoff; do not issue another opcode."
                                if remaining == 1
                                else "Finish your bounded responsibility and hand off promptly."
                            )
                        ),
                    }
                )
                if self.on_round:
                    action = f"action:{tool_name}"
                    if tool_name == "protocol_error":
                        reason = str(tool_args.get("error", "unknown protocol error"))
                        action = f"protocol-error:{reason[:160]}"
                    self.on_round(self.name, _round + 1, action, str(result.get("status")))
                if remaining == 0:
                    fallback = self.handoff_schema.model_validate(
                        {
                            "summary": (
                                f"{self.name} reached its externally supplied round limit after "
                                f"calling {tool_name}."
                            ),
                            "status": "PARTIAL",
                            "open_issues": [
                                "The MAU did not emit a final handoff before its round budget expired."
                            ],
                        }
                    )
                    if self.on_round:
                        self.on_round(self.name, _round + 1, "forced-handoff", "PARTIAL")
                    return fallback
                continue

            try:
                output = self.handoff_schema.model_validate(decision.final_handoff)
                self._validate(output)
            except (PydanticValidationError, ValidationError) as exc:
                validation_failures += 1
                if validation_failures > self.max_validation_retries:
                    raise ValidationError(f"{self.name} exhausted validation retries: {exc}") from exc
                messages.append(
                    {"role": "assistant", "content": decision.model_dump(mode="json")}
                )
                messages.append({"role": "system", "content": f"Validation failed: {exc}"})
                continue
            if self.on_round:
                self.on_round(self.name, _round + 1, "handoff", output.status.value)
            return output

        raise MaxStepsExceeded(f"{self.name} exceeded {max_rounds} planner rounds")

    def _validate(self, handoff: BaseHandoff) -> None:
        for validator in self.validators:
            try:
                result = validator(handoff)
            except Exception as exc:
                raise ValidationError(str(exc)) from exc
            if result is False:
                raise ValidationError(f"Validator {validator.__name__} failed")
            if isinstance(result, tuple) and not result[0]:
                raise ValidationError(result[1])
