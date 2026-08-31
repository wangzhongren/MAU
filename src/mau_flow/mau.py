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
        max_steps: int = 20,
        max_validation_retries: int = 3,
    ):
        if max_steps < 1 or max_validation_retries < 0:
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
        self.max_steps = max_steps
        self.max_validation_retries = max_validation_retries

    def run(self, input_handoff: BaseHandoff) -> HandoffT:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": input_handoff.model_dump(mode="json")},
        ]
        validation_failures = 0

        for _step in range(self.max_steps):
            decision = self.planner.plan(messages)
            if decision.action == "execute":
                if decision.tool_name not in self.allowed_tools:
                    result = {"status": "error", "error": "tool is not allowed"}
                else:
                    try:
                        result = self.executor.execute(decision.tool_name, **decision.tool_args)
                    except ToolError as exc:
                        result = {"status": "error", "error": str(exc)}
                messages.append({"role": "assistant", "content": decision.model_dump(mode="json")})
                messages.append({"role": "tool", "name": decision.tool_name, "content": result})
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
            return output

        raise MaxStepsExceeded(f"{self.name} exceeded {self.max_steps} planner steps")

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
