"""Executable Mission, Field and dummy-Robot processes for the M1 runtime slice."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Collection, Mapping
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from deferred_teleop.link import LinkFrame
from deferred_teleop.runtime import (
    DummyRobotService,
    EnvelopeFactory,
    FieldService,
    MissionService,
    RuntimeService,
    SystemClock,
)
from deferred_teleop.storage import NodeStore


class JsonLogger:
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id

    def __call__(self, event: str, fields: Mapping[str, Any]) -> None:
        print(
            json.dumps(
                {"event": event, "node_id": self.node_id, **fields},
                separators=(",", ":"),
                sort_keys=True,
            ),
            flush=True,
        )


async def _run_socket_connection(
    connection: ClientConnection | ServerConnection,
    service: RuntimeService,
    *,
    destinations: Collection[str],
    retry_interval: float,
    logger: JsonLogger,
) -> None:
    async def receive_frames() -> None:
        async for encoded in connection:
            if not isinstance(encoded, str):
                raise ValueError("binary frames are not supported")
            frame = LinkFrame.from_json(encoded)
            if frame.kind == "ack" and frame.acknowledged_message_id is not None:
                try:
                    changed = service.store.acknowledge(
                        frame.acknowledged_message_id,
                        acked_at=service.clock.now(),
                    )
                except KeyError:
                    changed = False
                logger(
                    "node.ack_received",
                    {
                        "message_id": str(frame.acknowledged_message_id),
                        "changed": changed,
                    },
                )
                continue
            if frame.kind != "envelope" or frame.envelope is None:
                raise ValueError("incomplete envelope frame")
            envelope = frame.envelope
            is_new = service.store.receive(envelope, received_at=service.clock.now())
            await connection.send(LinkFrame.for_ack(envelope.message_id).to_json())
            logger(
                "node.ack_sent",
                {
                    "message_id": str(envelope.message_id),
                    "duplicate": not is_new,
                },
            )
            if is_new:
                await service.handle(envelope)

    async def send_pending() -> None:
        while True:
            now = service.clock.now()
            for envelope in service.store.pending_outbox(now=now):
                if envelope.destination_id not in destinations:
                    continue
                service.store.record_attempt(
                    envelope.message_id,
                    next_attempt_at=now + timedelta(seconds=retry_interval),
                )
                await connection.send(LinkFrame.for_envelope(envelope).to_json())
                logger(
                    "node.message_sent",
                    {
                        "message_id": str(envelope.message_id),
                        "message_type": envelope.message_type,
                        "destination_id": envelope.destination_id,
                    },
                )
            await asyncio.sleep(min(0.05, retry_interval / 2))

    tasks = {
        asyncio.create_task(receive_frames()),
        asyncio.create_task(send_pending()),
    }
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()


async def _connect_forever(
    uri: str,
    service: RuntimeService,
    *,
    destinations: Collection[str],
    retry_interval: float,
    logger: JsonLogger,
    connection_name: str,
    on_connection_changed: Callable[[bool], None] | None = None,
) -> None:
    while True:
        try:
            async with connect(
                uri,
                open_timeout=2.0,
                ping_interval=10.0,
                ping_timeout=10.0,
            ) as connection:
                if on_connection_changed is not None:
                    on_connection_changed(True)
                logger("node.connected", {"connection": connection_name, "uri": uri})
                await _run_socket_connection(
                    connection,
                    service,
                    destinations=destinations,
                    retry_interval=retry_interval,
                    logger=logger,
                )
        except asyncio.CancelledError:
            raise
        except (ConnectionClosed, OSError, TimeoutError, ValueError) as error:
            logger(
                "node.disconnected",
                {"connection": connection_name, "error": type(error).__name__},
            )
        finally:
            if on_connection_changed is not None:
                on_connection_changed(False)
        await asyncio.sleep(retry_interval)


async def _mission_view_handler(
    connection: ServerConnection,
    service: MissionService,
    logger: JsonLogger,
    publish_interval: float,
) -> None:
    logger("mission.view_client_connected", {"remote": str(connection.remote_address)})
    try:
        while True:
            await connection.send(service.view_state().model_dump_json())
            await asyncio.sleep(publish_interval)
    except ConnectionClosed:
        pass
    finally:
        logger("mission.view_client_disconnected", {"remote": str(connection.remote_address)})


async def _mission_articulated_view_handler(
    connection: ServerConnection,
    service: MissionService,
    logger: JsonLogger,
    publish_interval: float,
) -> None:
    """Serve the opt-in M2 articulated view on its own WebSocket endpoint."""

    logger(
        "mission.articulated_view_client_connected",
        {"remote": str(connection.remote_address)},
    )
    try:
        while True:
            await connection.send(service.articulated_view_state().model_dump_json())
            await asyncio.sleep(publish_interval)
    except ConnectionClosed:
        pass
    finally:
        logger(
            "mission.articulated_view_client_disconnected",
            {"remote": str(connection.remote_address)},
        )


async def _mission_api_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    service: MissionService,
    logger: JsonLogger,
) -> None:
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
        request = json.loads(raw)
        command = request.get("command")
        if command == "submit_press_button":
            intent = service.submit_press_button(
                entity_id=request.get("entity_id", "dummy-button-1"),
                executor_id=request.get("executor_id", "dummy-robot-1"),
                expires_in_seconds=request.get("expires_in_seconds", 60.0),
            )
            response: dict[str, Any] = {
                "ok": True,
                "message_id": str(intent.message_id),
                "operation_id": str(intent.payload.operation_id),
                "correlation_id": str(intent.correlation_id),
            }
        elif command == "view":
            response = {"ok": True, "view": service.view()}
        elif command == "causal_history":
            correlation_id = UUID(request["correlation_id"])
            response = {"ok": True, "history": service.store.causal_history(correlation_id)}
        else:
            response = {"ok": False, "error": "unknown-command"}
    except (KeyError, TypeError, ValueError, TimeoutError) as error:
        response = {"ok": False, "error": type(error).__name__}
    writer.write((json.dumps(response, separators=(",", ":")) + "\n").encode())
    await writer.drain()
    writer.close()
    await writer.wait_closed()
    logger("mission.api_request", {"command": command if "command" in locals() else None})


def _parse_address(value: str) -> tuple[str, int]:
    host, separator, port_text = value.rpartition(":")
    if not separator or not host:
        raise argparse.ArgumentTypeError("address must be HOST:PORT")
    try:
        port = int(port_text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 0 <= port <= 65_535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return host, port


async def run_mission(args: argparse.Namespace) -> None:
    clock = SystemClock()
    logger = JsonLogger(args.node_id)
    with NodeStore(args.db) as store:
        service = MissionService(
            store,
            EnvelopeFactory(args.node_id, clock),
            configured_one_way_delay=args.one_way_delay,
            emit=logger,
        )
        recovered = await service.recover()
        api = await asyncio.start_server(
            lambda reader, writer: _mission_api_handler(reader, writer, service, logger),
            *args.api,
        )
        view_server = await serve(
            lambda connection: _mission_view_handler(
                connection,
                service,
                logger,
                args.view_publish_interval,
            ),
            *args.view_ws,
        )
        articulated_view_ws = getattr(args, "articulated_view_ws", None)
        articulated_view_server = (
            await serve(
                lambda connection: _mission_articulated_view_handler(
                    connection,
                    service,
                    logger,
                    args.view_publish_interval,
                ),
                *articulated_view_ws,
            )
            if articulated_view_ws is not None
            else None
        )
        api_port = int(api.sockets[0].getsockname()[1]) if api.sockets else 0
        view_port = int(view_server.sockets[0].getsockname()[1]) if view_server.sockets else 0
        articulated_view_port = (
            int(articulated_view_server.sockets[0].getsockname()[1])
            if articulated_view_server is not None and articulated_view_server.sockets
            else None
        )
        logger(
            "mission.started",
            {
                "api_host": args.api[0],
                "api_port": api_port,
                "view_ws_host": args.view_ws[0],
                "view_ws_port": view_port,
                "articulated_view_ws_host": (
                    articulated_view_ws[0] if articulated_view_ws is not None else None
                ),
                "articulated_view_ws_port": articulated_view_port,
                "database": str(Path(args.db).resolve()),
                "recovered_inbox": recovered,
            },
        )
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(api)
            await stack.enter_async_context(view_server)
            if articulated_view_server is not None:
                await stack.enter_async_context(articulated_view_server)
            await _connect_forever(
                args.link,
                service,
                destinations={"field-1"},
                retry_interval=args.retry_interval,
                logger=logger,
                connection_name="delayed-link",
                on_connection_changed=service.set_link_connected,
            )


async def run_field(args: argparse.Namespace) -> None:
    clock = SystemClock()
    logger = JsonLogger(args.node_id)
    with NodeStore(args.db) as store:
        service = FieldService(
            store,
            EnvelopeFactory(args.node_id, clock),
            mission_id=args.mission_id,
            robot_id=args.robot_id,
            emit=logger,
        )
        recovered = await service.recover()
        logger(
            "field.started",
            {"database": str(Path(args.db).resolve()), "recovered_inbox": recovered},
        )
        await asyncio.gather(
            _connect_forever(
                args.link,
                service,
                destinations={args.mission_id},
                retry_interval=args.retry_interval,
                logger=logger,
                connection_name="delayed-link",
            ),
            _connect_forever(
                args.robot,
                service,
                destinations={args.robot_id},
                retry_interval=args.retry_interval,
                logger=logger,
                connection_name="dummy-robot",
            ),
        )


async def run_robot(args: argparse.Namespace) -> None:
    clock = SystemClock()
    logger = JsonLogger(args.node_id)
    with NodeStore(args.db) as store:
        service = DummyRobotService(
            store,
            EnvelopeFactory(args.node_id, clock),
            field_id=args.field_id,
            phase_duration=args.phase_duration,
            emit=logger,
        )
        recovered = await service.recover()

        async def handler(connection: ServerConnection) -> None:
            await _run_socket_connection(
                connection,
                service,
                destinations={args.field_id},
                retry_interval=args.retry_interval,
                logger=logger,
            )

        server = await serve(handler, *args.listen)
        port = int(server.sockets[0].getsockname()[1]) if server.sockets else 0
        logger(
            "robot.started",
            {
                "listen_host": args.listen[0],
                "listen_port": port,
                "database": str(Path(args.db).resolve()),
                "capabilities": service.capabilities,
                "effect_counter": service.effect_counter,
                "recovered_inbox": recovered,
            },
        )
        async with server:
            await server.serve_forever()


def _add_common_node_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--retry-interval", type=float, default=0.2)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="process", required=True)

    mission = subparsers.add_parser("mission")
    _add_common_node_arguments(mission)
    mission.add_argument("--node-id", default="mission-1")
    mission.add_argument("--link", required=True)
    mission.add_argument("--api", type=_parse_address, default=("127.0.0.1", 8770))
    mission.add_argument("--view-ws", type=_parse_address, default=("127.0.0.1", 8772))
    mission.add_argument(
        "--articulated-view-ws",
        type=_parse_address,
        default=None,
        help="opt-in M2 articulated Mission view WebSocket (keeps the M1 endpoint unchanged)",
    )
    mission.add_argument("--view-publish-interval", type=float, default=0.1)
    mission.add_argument("--one-way-delay", type=float, default=0.05)
    mission.set_defaults(run=run_mission)

    field = subparsers.add_parser("field")
    _add_common_node_arguments(field)
    field.add_argument("--node-id", default="field-1")
    field.add_argument("--mission-id", default="mission-1")
    field.add_argument("--robot-id", default="dummy-robot-1")
    field.add_argument("--link", required=True)
    field.add_argument("--robot", required=True)
    field.set_defaults(run=run_field)

    robot = subparsers.add_parser("robot")
    _add_common_node_arguments(robot)
    robot.add_argument("--node-id", default="dummy-robot-1")
    robot.add_argument("--field-id", default="field-1")
    robot.add_argument("--listen", type=_parse_address, default=("127.0.0.1", 8771))
    robot.add_argument("--phase-duration", type=float, default=0.05)
    robot.set_defaults(run=run_robot)
    return parser


def _run_entry(arguments: list[str]) -> None:
    args = _build_parser().parse_args(arguments)
    if args.retry_interval <= 0:
        raise SystemExit("--retry-interval must be positive")
    if getattr(args, "view_publish_interval", 1.0) <= 0:
        raise SystemExit("--view-publish-interval must be positive")
    try:
        asyncio.run(args.run(args))
    except KeyboardInterrupt:
        pass


def mission_main() -> None:
    _run_entry(["mission", *sys.argv[1:]])


def field_main() -> None:
    _run_entry(["field", *sys.argv[1:]])


def robot_main() -> None:
    _run_entry(["robot", *sys.argv[1:]])


def main() -> None:
    _run_entry(sys.argv[1:])


if __name__ == "__main__":
    main()
