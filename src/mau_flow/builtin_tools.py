"""Schemas and registration for the existing filesystem/process backend."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from actunit import ActionDefinition, ActionRegistry


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathArgs(Arguments):
    path: str = Field(min_length=1)


class WriteArgs(PathArgs):
    content: str


class ReadArgs(Arguments):
    path: str | None = None
    paths: list[str] | None = None

    @model_validator(mode="after")
    def check_paths(self) -> "ReadArgs":
        if (self.path is None) == (self.paths is None):
            raise ValueError("read accepts either path or paths, not both")
        if (
            self.path == ""
            or self.paths == []
            or (self.paths is not None and any(not p for p in self.paths))
        ):
            raise ValueError("read requires non-empty paths")
        return self


class ShellArgs(Arguments):
    program: str = Field(min_length=1)
    args: list[str] | None = None
    cwd: str = "."
    timeout_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    reason: str = Field(min_length=1)


def builtin_registry(backend: Any) -> ActionRegistry:
    registry = ActionRegistry()
    specs: list[tuple[str, str, type[BaseModel]]] = [
        ("create", "Create a workspace file", WriteArgs),
        ("read", "Read labeled workspace files", ReadArgs),
        ("update", "Replace an existing file", WriteArgs),
        ("delete", "Delete an existing file", PathArgs),
        ("shell", "Execute approved structured argv", ShellArgs),
    ]
    for name, description, schema in specs:

        def handler(args, context, tool_name=name):
            # Context is supplied by the host, never decoded from model arguments.
            if context.workspace.resolve() != backend.sandbox_dir.resolve():
                raise ValueError("Execution workspace does not match backend")
            return getattr(backend, f"_{tool_name}")(**args)

        registry.register(ActionDefinition(name, description, schema, handler))
    return registry
