"""One-command subprocess supervisor for the M1 delayed-dummy demonstration."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from deferred_teleop.storage import NodeStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PROFILE_ALIASES = {
    "short-visible-delay": REPOSITORY_ROOT / "profiles" / "short-visible-delay.toml",
    "short-visible-fault": REPOSITORY_ROOT / "profiles" / "short-visible-fault.toml",
}


def _emit(event: str, fields: Mapping[str, Any]) -> None:
    print(json.dumps({"event": event, **fields}, separators=(",", ":"), sort_keys=True), flush=True)


def _unused_ports(count: int) -> tuple[int, ...]:
    listeners: list[socket.socket] = []
    try:
        for _ in range(count):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            listeners.append(listener)
        return tuple(int(listener.getsockname()[1]) for listener in listeners)
    finally:
        for listener in listeners:
            listener.close()


def _resolve_profile(value: str) -> Path:
    alias = value.removesuffix(".toml")
    path = PROFILE_ALIASES.get(alias, Path(value))
    if not path.is_file():
        choices = ", ".join(sorted(PROFILE_ALIASES))
        raise argparse.ArgumentTypeError(f"profile not found; use a path or one of: {choices}")
    return path.resolve()


class ManagedProcess:
    def __init__(
        self,
        name: str,
        process: asyncio.subprocess.Process,
        *,
        show_logs: bool,
    ) -> None:
        self.name = name
        self.process = process
        self.show_logs = show_logs
        self.events: dict[str, list[dict[str, Any]]] = {}
        self._signals: dict[str, asyncio.Event] = {}
        self._visible_delivery_events: set[tuple[str, str]] = set()
        self._reader_task = asyncio.create_task(self._read_output())

    @classmethod
    async def start(
        cls,
        name: str,
        command: Sequence[str],
        *,
        show_logs: bool,
    ) -> ManagedProcess:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=REPOSITORY_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        return cls(name, process, show_logs=show_logs)

    async def _read_output(self) -> None:
        assert self.process.stdout is not None
        while raw := await self.process.stdout.readline():
            line = raw.decode(errors="replace").rstrip()
            try:
                fields = json.loads(line)
            except json.JSONDecodeError:
                if self.show_logs:
                    print(f"[{self.name}] {line}", flush=True)
                continue
            if not isinstance(fields, dict):
                continue
            event = fields.get("event")
            if isinstance(event, str):
                self.events.setdefault(event, []).append(fields)
                self._signals.setdefault(event, asyncio.Event()).set()
            if self.show_logs:
                output_event = event if isinstance(event, str) else "process.output"
                message_id = fields.get("message_id")
                delivery_key = (output_event, str(message_id))
                repeated_delivery = output_event.startswith("node.") and message_id is not None
                polling_log = (
                    output_event == "mission.api_request" and fields.get("command") == "view"
                )
                if not polling_log and (
                    not repeated_delivery or delivery_key not in self._visible_delivery_events
                ):
                    _emit(output_event, {"process": self.name, **fields})
                if repeated_delivery:
                    self._visible_delivery_events.add(delivery_key)

    async def wait_event(self, event: str, *, timeout: float) -> dict[str, Any]:
        if not self.events.get(event):
            try:
                await asyncio.wait_for(
                    self._signals.setdefault(event, asyncio.Event()).wait(),
                    timeout=timeout,
                )
            except TimeoutError as error:
                return_code = self.process.returncode
                detail = f" (process exited {return_code})" if return_code is not None else ""
                raise RuntimeError(f"{self.name} did not emit {event}{detail}") from error
        return self.events[event][-1]

    async def stop(self) -> None:
        self.show_logs = False
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3.0)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        await asyncio.gather(self._reader_task, return_exceptions=True)


async def _mission_request(port: int, request: Mapping[str, Any]) -> dict[str, Any]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write((json.dumps(request, separators=(",", ":")) + "\n").encode())
    await writer.drain()
    response = json.loads(await asyncio.wait_for(reader.readline(), timeout=2.0))
    writer.close()
    await writer.wait_closed()
    if not response.get("ok"):
        command = request.get("command")
        raise RuntimeError(f"Mission API rejected {command}: {response.get('error')}")
    return response


async def _wait_for_terminal(port: int, *, timeout: float) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            response = await _mission_request(port, {"command": "view"})
        except (ConnectionError, OSError):
            await asyncio.sleep(0.05)
            continue
        view = response["view"]
        if view["terminal_state"] is not None and view["confirmed_state"] is not None:
            return view
        await asyncio.sleep(0.05)
    raise TimeoutError("the delayed dummy did not reconcile before the demo timeout")


def _effect_counter(robot_db: Path) -> int:
    with NodeStore(robot_db) as store:
        return sum(int(row["effect_count"]) for row in store.inspect_execution_journal())


def _prepare_data_dir(value: Path | None) -> Path:
    path = value.resolve() if value is not None else Path(tempfile.mkdtemp(prefix="dtt-demo-"))
    path.mkdir(parents=True, exist_ok=True)
    occupied = [
        path / name
        for name in ("mission.db", "field.db", "robot.db")
        if (path / name).exists()
    ]
    if occupied:
        raise RuntimeError("the demo data directory already contains a node database")
    return path


async def _run_delayed_dummy(args: argparse.Namespace) -> None:
    data_dir = _prepare_data_dir(args.data_dir)
    mission_db = data_dir / "mission.db"
    field_db = data_dir / "field.db"
    robot_db = data_dir / "robot.db"
    mission_link_port, field_link_port, robot_port, api_port, dynamic_view_port = _unused_ports(5)
    view_ws_port = args.view_ws_port or dynamic_view_port
    retry = str(args.retry_interval)
    python = sys.executable
    processes: list[ManagedProcess] = []
    mission: ManagedProcess | None = None

    async def start(name: str, command: Sequence[str], event: str) -> ManagedProcess:
        child = await ManagedProcess.start(name, command, show_logs=not args.quiet)
        processes.append(child)
        await child.wait_event(event, timeout=args.timeout)
        return child

    link_command = (
        python,
        "-m",
        "deferred_teleop.link",
        "--mission-listen",
        f"127.0.0.1:{mission_link_port}",
        "--field-listen",
        f"127.0.0.1:{field_link_port}",
        "--profile",
        str(args.profile),
        "--status-interval",
        "1",
    )
    robot_command = (
        python,
        "-m",
        "deferred_teleop.node",
        "robot",
        "--db",
        str(robot_db),
        "--listen",
        f"127.0.0.1:{robot_port}",
        "--phase-duration",
        str(args.phase_duration),
        "--retry-interval",
        retry,
    )
    field_command = (
        python,
        "-m",
        "deferred_teleop.node",
        "field",
        "--db",
        str(field_db),
        "--link",
        f"ws://127.0.0.1:{field_link_port}",
        "--robot",
        f"ws://127.0.0.1:{robot_port}",
        "--retry-interval",
        retry,
    )
    mission_command = (
        python,
        "-m",
        "deferred_teleop.node",
        "mission",
        "--db",
        str(mission_db),
        "--link",
        f"ws://127.0.0.1:{mission_link_port}",
        "--api",
        f"127.0.0.1:{api_port}",
        "--view-ws",
        f"127.0.0.1:{view_ws_port}",
        "--one-way-delay",
        str(args.one_way_delay),
        "--retry-interval",
        retry,
    )

    _emit(
        "demo.started",
        {
            "data_dir": str(data_dir),
            "profile": str(args.profile),
            "processes": ["link", "mission", "field", "robot"],
        },
    )
    try:
        link = await start("link", link_command, "dtt-link.started")
        robot = await start("robot", robot_command, "robot.started")
        field = await start("field", field_command, "field.started")
        mission = await start("mission", mission_command, "mission.started")
        _emit(
            "demo.processes_ready",
            {
                "pids": {
                    "link": link.process.pid,
                    "mission": mission.process.pid,
                    "field": field.process.pid,
                    "robot": robot.process.pid,
                },
                "stores": {
                    "mission": str(mission_db),
                    "field": str(field_db),
                    "robot": str(robot_db),
                },
                "mission_view_ws": f"ws://127.0.0.1:{view_ws_port}",
            },
        )
        if args.pre_submit_delay > 0:
            _emit("demo.waiting_before_submit", {"seconds": args.pre_submit_delay})
            await asyncio.sleep(args.pre_submit_delay)
        submitted = await _mission_request(
            api_port,
            {
                "command": "submit_press_button",
                "entity_id": "dummy-button-1",
                "executor_id": "dummy-robot-1",
            },
        )
        _emit(
            "demo.operation_submitted",
            {
                "operation_id": submitted["operation_id"],
                "correlation_id": submitted["correlation_id"],
            },
        )

        if args.restart_mission_after_admission:
            admitted = await field.wait_event("field.operation_admitted", timeout=args.timeout)
            _emit(
                "demo.mission_stopped_after_admission",
                {"contract_id": admitted["contract_id"]},
            )
            await mission.stop()
            processes.remove(mission)
            await robot.wait_event("robot.effect_committed", timeout=args.timeout)
            mission = await start("mission-restarted", mission_command, "mission.started")
            _emit("demo.mission_restarted", {"database": str(mission_db)})

        view = await _wait_for_terminal(api_port, timeout=args.timeout)
        phases = [item["phase"] for item in robot.events.get("robot.phase", [])]
        effect_counter = _effect_counter(robot_db)
        _emit(
            "demo.completed",
            {
                "operation_id": submitted["operation_id"],
                "contract_id": view["terminal_contract_id"],
                "estimated_arrival_at": view["estimated_arrival_at"],
                "phases": phases,
                "effect_counter": effect_counter,
                "terminal_state": view["terminal_state"],
                "confirmed_provenance": view["confirmed_state"]["evidence"]["provenance"],
                "arrival_provenance": view["arrival_belief"]["evidence"]["provenance"],
                "target_provenance": view["target_branch"]["provenance"],
                "mission_restarted": args.restart_mission_after_admission,
                "inspect_command": (
                    f'dtt-inspect causal-history --data-dir "{data_dir}" '
                    f'--correlation-id {submitted["correlation_id"]}'
                ),
            },
        )
        if effect_counter != 1 or view["terminal_state"] != "SUCCEEDED":
            raise RuntimeError("demo invariant failed: expected one successful dummy effect")
        if args.hold_open_seconds > 0:
            _emit("demo.holding_open", {"seconds": args.hold_open_seconds})
            await asyncio.sleep(args.hold_open_seconds)
    finally:
        for child in reversed(processes):
            await child.stop()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="demo", required=True)
    delayed = subparsers.add_parser("delayed-dummy")
    delayed.add_argument(
        "--profile",
        type=_resolve_profile,
        default=_resolve_profile("short-visible-delay"),
    )
    delayed.add_argument("--data-dir", type=Path)
    delayed.add_argument("--restart-mission-after-admission", action="store_true")
    delayed.add_argument("--phase-duration", type=float, default=0.1)
    delayed.add_argument("--one-way-delay", type=float, default=0.15)
    delayed.add_argument("--retry-interval", type=float, default=0.05)
    delayed.add_argument("--timeout", type=float, default=15.0)
    delayed.add_argument("--hold-open-seconds", type=float, default=0.0)
    delayed.add_argument(
        "--pre-submit-delay",
        type=float,
        default=0.0,
        help="wait after process readiness so an observer can connect before submission",
    )
    delayed.add_argument(
        "--view-ws-port",
        type=int,
        default=0,
        help="stable Mission-view port for Unreal; 0 selects an unused port",
    )
    delayed.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    delayed.set_defaults(run=_run_delayed_dummy)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    if min(args.phase_duration, args.one_way_delay, args.retry_interval, args.timeout) <= 0:
        raise SystemExit("durations and timeout must be positive")
    if min(args.hold_open_seconds, args.pre_submit_delay) < 0:
        raise SystemExit("hold-open and pre-submit delays cannot be negative")
    if not 0 <= args.view_ws_port <= 65_535:
        raise SystemExit("--view-ws-port must be between 0 and 65535")
    try:
        asyncio.run(args.run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
