"""Generate, inspect, and execute dynamic MAU chains."""

from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path

from .chain import DynamicChainGenerator, SandboxMode, load_chain, materialize_chain, save_chain
from .contracts import BaseHandoff, Status
from .executor import CommandRequest

DEFAULT_CHAIN_FILE = ".mau-flow-chain.xml"


def _add_generation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", nargs="?", help="Optional target file, existing or new")
    parser.add_argument("--task", "-t", help="Task instructions; prompted if omitted")
    parser.add_argument("--output", "-o", help=f"Chain file (default: {DEFAULT_CHAIN_FILE})")
    parser.add_argument("--max-agents", type=int, default=8, help="Maximum generated MAUs")
    parser.add_argument(
        "--context-chars",
        type=int,
        default=50000,
        help="Maximum target content sent to the generator",
    )


def _add_execution_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-rounds", type=int, default=10, help="Maximum model rounds per MAU")
    parser.add_argument(
        "--approval-mode",
        choices=("prompt", "never"),
        default="prompt",
        help="How to handle shell commands that require approval (default: prompt)",
    )
    parser.add_argument(
        "--sandbox",
        choices=("snapshot", "host"),
        default="snapshot",
        help="Per-MAU workspace isolation (default: snapshot)",
    )


def _generate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mau-flow", description="Generate a dynamic MAU chain.")
    _add_generation_arguments(parser)
    return parser


def _start_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mau-flow start", description="Run a saved MAU chain.")
    parser.add_argument("--chain", "-c", default=DEFAULT_CHAIN_FILE, help="Saved chain XML file")
    _add_execution_arguments(parser)
    return parser


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mau-flow run",
        description="Generate a dynamic MAU chain and execute it immediately.",
    )
    _add_generation_arguments(parser)
    _add_execution_arguments(parser)
    return parser


def _prompt_shell_approval(request: CommandRequest) -> bool:
    print("\nShell approval required", file=sys.stderr)
    print(f"  Reason: {request.reason}", file=sys.stderr)
    print(f"  Command: {shlex.join(request.argv)}", file=sys.stderr)
    print(f"  Working directory: {request.cwd}", file=sys.stderr)
    print(f"  Timeout: {request.timeout_seconds:g}s", file=sys.stderr)
    if not sys.stdin.isatty():
        print("  Denied: input is not interactive.", file=sys.stderr)
        return False
    return input("Allow once? [y/N] ").strip().lower() in {"y", "yes"}


def _resolve_scope(file: str | None) -> tuple[Path, str | None]:
    if file is None:
        return Path.cwd().resolve(), None
    target = Path(file).expanduser().resolve()
    if target.is_dir():
        raise ValueError(f"target must be a file, not a directory: {target}")
    if not target.parent.is_dir():
        raise ValueError(f"target parent directory does not exist: {target.parent}")
    return target.parent, target.name


def _print_specs(specs) -> None:
    print(f"MAU chain ({len(specs)}): {' -> '.join(spec.name for spec in specs)}")
    for index, spec in enumerate(specs, 1):
        print(f"  {index}. {spec.name}: {spec.purpose} [{', '.join(sorted(spec.tools))}]")


def _generate_chain(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    show_start_hint: bool,
) -> Path:
    if args.max_agents < 1:
        parser.error("--max-agents must be at least 1")
    if args.context_chars < 0:
        parser.error("--context-chars must not be negative")
    try:
        workspace, target = _resolve_scope(args.file)
    except ValueError as exc:
        parser.error(str(exc))
    task = args.task or input("Task: ").strip()
    if not task:
        task = (
            f"Complete {target}: inspect it, resolve incomplete or defective parts, and verify it."
            if target
            else "Complete the requested workspace task."
        )
    output = Path(args.output).expanduser().resolve() if args.output else workspace / DEFAULT_CHAIN_FILE
    specs = DynamicChainGenerator().generate(
        workspace=workspace,
        target=target,
        task=task,
        max_agents=args.max_agents,
        context_chars=args.context_chars,
    )
    save_chain(output, workspace=workspace, task=task, target=target, specs=specs)
    print(f"Saved chain: {output}")
    _print_specs(specs)
    if show_start_hint:
        print(f"Run with: mau-flow start --chain {output}")
    return output


def generate(argv: Sequence[str]) -> int:
    parser = _generate_parser()
    args = parser.parse_args(argv)
    _generate_chain(args, parser, show_start_hint=True)
    return 0


def _execute_chain(
    chain_path: Path,
    *,
    max_rounds: int,
    approval_mode: str,
    sandbox: SandboxMode,
    parser: argparse.ArgumentParser,
    announce_chain: bool,
) -> int:
    if max_rounds < 1:
        parser.error("--max-rounds must be at least 1")
    if not chain_path.is_file():
        parser.error(f"chain file does not exist: {chain_path}")
    workspace, task, target, specs = load_chain(chain_path)
    if announce_chain:
        print(f"Loaded chain: {chain_path}")
        _print_specs(specs)

    def show_round(name: str, round_number: int, action: str, status: str) -> None:
        print(f"[{name} round {round_number}/{max_rounds}] {action} status={status}", flush=True)

    pipeline = materialize_chain(
        workspace=workspace,
        target=target,
        task=task,
        specs=specs,
        on_round=show_round,
        shell_approver=_prompt_shell_approval if approval_mode == "prompt" else None,
        sandbox_mode=sandbox,
    )
    result = pipeline.execute(
        BaseHandoff(summary=task, status=Status.SUCCESS),
        start=specs[0].name,
        max_rounds_per_agent=max_rounds,
    )
    print(result.model_dump_json(indent=2))
    return 0 if result.status == Status.SUCCESS else 1


def start(argv: Sequence[str]) -> int:
    parser = _start_parser()
    args = parser.parse_args(argv)
    chain_path = Path(args.chain).expanduser().resolve()
    return _execute_chain(
        chain_path,
        max_rounds=args.max_rounds,
        approval_mode=args.approval_mode,
        sandbox=args.sandbox,
        parser=parser,
        announce_chain=True,
    )


def run(argv: Sequence[str]) -> int:
    parser = _run_parser()
    args = parser.parse_args(argv)
    chain_path = _generate_chain(args, parser, show_start_hint=False)
    print(f"Starting generated chain: {chain_path}")
    return _execute_chain(
        chain_path,
        max_rounds=args.max_rounds,
        approval_mode=args.approval_mode,
        sandbox=args.sandbox,
        parser=parser,
        announce_chain=False,
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "start":
        return start(arguments[1:])
    if arguments and arguments[0] == "run":
        return run(arguments[1:])
    return generate(arguments)
