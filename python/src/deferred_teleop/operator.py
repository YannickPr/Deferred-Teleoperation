"""Local control/read client for a running M1 Mission process."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID


def _parse_address(value: str) -> tuple[str, int]:
    host, separator, port_text = value.rpartition(":")
    if not separator or not host:
        raise argparse.ArgumentTypeError("address must be HOST:PORT")
    try:
        return host, int(port_text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error


async def _request(address: tuple[str, int], request: Mapping[str, Any]) -> dict[str, Any]:
    reader, writer = await asyncio.open_connection(*address)
    writer.write((json.dumps(request, separators=(",", ":")) + "\n").encode())
    await writer.drain()
    response = json.loads(await reader.readline())
    writer.close()
    await writer.wait_closed()
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", type=_parse_address, default=("127.0.0.1", 8770))
    commands = parser.add_subparsers(dest="command", required=True)
    submit = commands.add_parser("submit-press-button")
    submit.add_argument("--entity-id", default="dummy-button-1")
    submit.add_argument("--executor-id", default="dummy-robot-1")
    submit.add_argument("--expires-in-seconds", type=float, default=60.0)
    commands.add_parser("view")
    history = commands.add_parser("causal-history")
    history.add_argument("--correlation-id", type=UUID, required=True)
    args = parser.parse_args()

    if args.command == "submit-press-button":
        request: dict[str, Any] = {
            "command": "submit_press_button",
            "entity_id": args.entity_id,
            "executor_id": args.executor_id,
            "expires_in_seconds": args.expires_in_seconds,
        }
    elif args.command == "causal-history":
        request = {
            "command": "causal_history",
            "correlation_id": str(args.correlation_id),
        }
    else:
        request = {"command": "view"}

    response = asyncio.run(_request(args.api, request))
    print(json.dumps(response, indent=2, sort_keys=True))
    if not response.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
