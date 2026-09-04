"""Deterministic delayed/faulted link with in-memory and WebSocket adapters."""

from __future__ import annotations

import argparse
import asyncio
import heapq
import json
import random
import time
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from deferred_teleop.protocol import MessageEnvelope

Side = Literal["mission", "field"]
FrameKind = Literal["envelope", "ack"]


@dataclass(frozen=True)
class ScriptedDelivery:
    delay_seconds: float | None = None
    duplicates: int | None = None

    def __post_init__(self) -> None:
        if self.delay_seconds is not None and self.delay_seconds < 0:
            raise ValueError("scripted delay must be non-negative")
        if self.duplicates is not None and self.duplicates < 0:
            raise ValueError("scripted duplicates must be non-negative")


@dataclass(frozen=True)
class FaultProfile:
    one_way_delay_seconds: float = 0.0
    jitter_distribution: Literal["none", "uniform"] = "none"
    jitter_seconds: float = 0.0
    duplicate_probability: float = 0.0
    reorder_window_seconds: float = 0.0
    blackout_intervals: tuple[tuple[float, float], ...] = ()
    bandwidth_bytes_per_second: float | None = None
    queue_capacity: int = 10_000
    seed: int = 0
    scripted: dict[str, ScriptedDelivery] = field(default_factory=dict)

    def __post_init__(self) -> None:
        non_negative = (
            self.one_way_delay_seconds,
            self.jitter_seconds,
            self.reorder_window_seconds,
        )
        if any(value < 0 for value in non_negative):
            raise ValueError("delay, jitter and reorder window must be non-negative")
        if self.jitter_distribution not in {"none", "uniform"}:
            raise ValueError("jitter_distribution must be 'none' or 'uniform'")
        if not 0.0 <= self.duplicate_probability <= 1.0:
            raise ValueError("duplicate_probability must be between 0 and 1")
        if self.bandwidth_bytes_per_second is not None and self.bandwidth_bytes_per_second <= 0:
            raise ValueError("bandwidth_bytes_per_second must be positive")
        if self.queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        for start, end in self.blackout_intervals:
            if start < 0 or end <= start:
                raise ValueError("blackout intervals require 0 <= start < end")

    @classmethod
    def from_toml(cls, path: str | Path) -> FaultProfile:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        profile = dict(raw.get("profile", raw))
        scripted = {
            key: ScriptedDelivery(**value) for key, value in profile.pop("scripted", {}).items()
        }
        blackouts = tuple(tuple(item) for item in profile.pop("blackout_intervals", []))
        return cls(**profile, scripted=scripted, blackout_intervals=blackouts)


@dataclass(frozen=True)
class LinkFrame:
    kind: FrameKind
    envelope: MessageEnvelope | None = None
    acknowledged_message_id: UUID | None = None

    @classmethod
    def for_envelope(cls, envelope: MessageEnvelope) -> LinkFrame:
        return cls(kind="envelope", envelope=envelope)

    @classmethod
    def for_ack(cls, message_id: UUID) -> LinkFrame:
        return cls(kind="ack", acknowledged_message_id=message_id)

    @property
    def stable_id(self) -> str:
        if self.kind == "envelope" and self.envelope is not None:
            return str(self.envelope.message_id)
        if self.kind == "ack" and self.acknowledged_message_id is not None:
            return f"ack:{self.acknowledged_message_id}"
        raise ValueError("incomplete link frame")

    @property
    def priority(self) -> int:
        return 0 if self.kind == "ack" else 1

    def to_json(self) -> str:
        if self.kind == "envelope" and self.envelope is not None:
            value = {"kind": "envelope", "envelope": self.envelope.model_dump(mode="json")}
        elif self.kind == "ack" and self.acknowledged_message_id is not None:
            value = {"kind": "ack", "message_id": str(self.acknowledged_message_id)}
        else:
            raise ValueError("incomplete link frame")
        return json.dumps(value, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, encoded: str) -> LinkFrame:
        value = json.loads(encoded)
        if set(value) == {"kind", "envelope"} and value["kind"] == "envelope":
            envelope = MessageEnvelope.model_validate_json(json.dumps(value["envelope"]))
            return cls.for_envelope(envelope)
        if set(value) == {"kind", "message_id"} and value["kind"] == "ack":
            return cls.for_ack(UUID(value["message_id"]))
        raise ValueError("invalid link frame")


class TransportPort(Protocol):
    async def send(self, envelope: MessageEnvelope) -> None: ...

    async def receive(self) -> LinkFrame: ...

    async def acknowledge(self, message_id: UUID) -> None: ...

    def health(self) -> dict[str, Any]: ...


class InMemoryTransport:
    """Substitutable domain-test transport with the same narrow port."""

    def __init__(
        self,
        inbound: asyncio.Queue[LinkFrame],
        outbound: asyncio.Queue[LinkFrame],
    ) -> None:
        self._inbound = inbound
        self._outbound = outbound
        self._sent = 0
        self._received = 0

    @classmethod
    def pair(cls) -> tuple[InMemoryTransport, InMemoryTransport]:
        left_to_right: asyncio.Queue[LinkFrame] = asyncio.Queue()
        right_to_left: asyncio.Queue[LinkFrame] = asyncio.Queue()
        return cls(right_to_left, left_to_right), cls(left_to_right, right_to_left)

    async def send(self, envelope: MessageEnvelope) -> None:
        await self._outbound.put(LinkFrame.for_envelope(envelope))
        self._sent += 1

    async def receive(self) -> LinkFrame:
        frame = await self._inbound.get()
        self._received += 1
        return frame

    async def acknowledge(self, message_id: UUID) -> None:
        await self._outbound.put(LinkFrame.for_ack(message_id))
        self._sent += 1

    def health(self) -> dict[str, Any]:
        return {
            "transport": "memory",
            "sent": self._sent,
            "received": self._received,
            "queued_inbound": self._inbound.qsize(),
        }


@dataclass(order=True, frozen=True)
class ScheduledFrame:
    delivery_time: float
    priority: int
    sequence: int
    destination: Side = field(compare=False)
    frame: LinkFrame = field(compare=False)
    encoded_size: int = field(compare=False)


class DeterministicLink:
    """Pure scheduling core driven by caller-supplied virtual time."""

    def __init__(
        self,
        profile: FaultProfile,
        *,
        wall_epoch: datetime | None = None,
    ) -> None:
        self.profile = profile
        self.wall_epoch = wall_epoch or datetime.now(UTC)
        if self.wall_epoch.tzinfo is None or self.wall_epoch.utcoffset() is None:
            raise ValueError("wall_epoch must be timezone-aware")
        self._random = random.Random(profile.seed)
        self._queue: list[ScheduledFrame] = []
        self._sequence = 0
        self._lane_available: dict[tuple[Side, int], float] = {}
        self.metrics: dict[str, int] = {
            "submitted": 0,
            "delivered": 0,
            "duplicates_injected": 0,
            "dropped_capacity": 0,
            "dropped_disconnected": 0,
            "blackout_deferrals": 0,
            "expired": 0,
            "bytes_transmitted": 0,
            "reconnect_count": 0,
        }

    def submit(self, source: Side, frame: LinkFrame, *, now: float) -> int:
        if now < 0:
            raise ValueError("virtual time must be non-negative")
        if source not in {"mission", "field"}:
            raise ValueError("source must be mission or field")
        self.metrics["submitted"] += 1
        directive = self.profile.scripted.get(frame.stable_id)
        duplicate_count = self._duplicate_count(directive)
        scheduled = 0
        for copy_index in range(duplicate_count + 1):
            if len(self._queue) >= self.profile.queue_capacity:
                self.metrics["dropped_capacity"] += 1
                continue
            destination: Side = "field" if source == "mission" else "mission"
            delivery = self._delivery_time(
                destination, frame, now=now, copy_index=copy_index, directive=directive
            )
            if self._is_expired(frame, delivery):
                self.metrics["expired"] += 1
                continue
            encoded_size = len(frame.to_json().encode("utf-8"))
            self._sequence += 1
            heapq.heappush(
                self._queue,
                ScheduledFrame(
                    delivery,
                    frame.priority,
                    self._sequence,
                    destination,
                    frame,
                    encoded_size,
                ),
            )
            scheduled += 1
        if duplicate_count:
            self.metrics["duplicates_injected"] += max(0, scheduled - 1)
        return scheduled

    def deliver_due(self, *, now: float) -> list[ScheduledFrame]:
        delivered: list[ScheduledFrame] = []
        while self._queue and self._queue[0].delivery_time <= now:
            item = heapq.heappop(self._queue)
            delivered.append(item)
            self.metrics["delivered"] += 1
            self.metrics["bytes_transmitted"] += item.encoded_size
        return delivered

    def status(self, *, now: float) -> dict[str, Any]:
        return {
            **self.metrics,
            "queued_deliveries": len(self._queue),
            "next_delivery_time": self._queue[0].delivery_time if self._queue else None,
            "blackout": self._in_blackout(now),
        }

    def _duplicate_count(self, directive: ScriptedDelivery | None) -> int:
        if directive is not None and directive.duplicates is not None:
            return directive.duplicates
        return int(self._random.random() < self.profile.duplicate_probability)

    def _delivery_time(
        self,
        destination: Side,
        frame: LinkFrame,
        *,
        now: float,
        copy_index: int,
        directive: ScriptedDelivery | None,
    ) -> float:
        delay = self.profile.one_way_delay_seconds
        if directive is not None and directive.delay_seconds is not None:
            delay = directive.delay_seconds
        if self.profile.jitter_distribution == "uniform":
            delay += self._random.uniform(-self.profile.jitter_seconds, self.profile.jitter_seconds)
        delay = max(0.0, delay)
        reorder = self._random.uniform(0.0, self.profile.reorder_window_seconds)
        candidate = now + delay + reorder + copy_index * 1e-6
        encoded_size = len(frame.to_json().encode("utf-8"))
        bandwidth = self.profile.bandwidth_bytes_per_second
        if bandwidth is not None:
            lane = (destination, frame.priority)
            start = max(candidate, self._lane_available.get(lane, candidate))
            candidate = start + encoded_size / bandwidth
        after_blackout = self._after_blackout(candidate)
        if bandwidth is not None:
            self._lane_available[(destination, frame.priority)] = after_blackout
        if after_blackout != candidate:
            self.metrics["blackout_deferrals"] += 1
        return after_blackout

    def _after_blackout(self, candidate: float) -> float:
        changed = True
        while changed:
            changed = False
            for start, end in self.profile.blackout_intervals:
                if start <= candidate < end:
                    candidate = end
                    changed = True
        return candidate

    def _in_blackout(self, now: float) -> bool:
        return any(start <= now < end for start, end in self.profile.blackout_intervals)

    def _is_expired(self, frame: LinkFrame, delivery: float) -> bool:
        if frame.kind != "envelope" or frame.envelope is None:
            return False
        expires_at = frame.envelope.expires_at
        if expires_at is None:
            return False
        delivery_wall_time = self.wall_epoch + timedelta(seconds=delivery)
        return delivery_wall_time >= expires_at


class WebSocketRelay:
    """Development-only two-sided WebSocket adapter around the deterministic core."""

    def __init__(
        self,
        profile: FaultProfile,
        *,
        mission_host: str = "127.0.0.1",
        mission_port: int = 0,
        field_host: str = "127.0.0.1",
        field_port: int = 0,
    ) -> None:
        self.link = DeterministicLink(profile)
        self._mission_address = (mission_host, mission_port)
        self._field_address = (field_host, field_port)
        self._servers: list[Server] = []
        self._connections: dict[Side, set[ServerConnection]] = {
            "mission": set(),
            "field": set(),
        }
        self._connection_attempts: dict[Side, int] = {"mission": 0, "field": 0}
        self._start_time = 0.0
        self._delivery_task: asyncio.Task[None] | None = None

    @property
    def mission_port(self) -> int:
        return self._bound_port(0)

    @property
    def field_port(self) -> int:
        return self._bound_port(1)

    async def start(self) -> None:
        self._start_time = time.monotonic()
        mission = await serve(
            lambda connection: self._handle("mission", connection), *self._mission_address
        )
        field_server = await serve(
            lambda connection: self._handle("field", connection), *self._field_address
        )
        self._servers = [mission, field_server]
        self._delivery_task = asyncio.create_task(self._delivery_loop())

    async def close(self) -> None:
        if self._delivery_task is not None:
            self._delivery_task.cancel()
            await asyncio.gather(self._delivery_task, return_exceptions=True)
        for server in self._servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in self._servers))
        self._servers.clear()

    async def __aenter__(self) -> WebSocketRelay:
        await self.start()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    def health(self) -> dict[str, Any]:
        return {
            **self.link.status(now=self._now()),
            "mission_connections": len(self._connections["mission"]),
            "field_connections": len(self._connections["field"]),
            "mission_port": self.mission_port,
            "field_port": self.field_port,
        }

    async def _handle(self, side: Side, connection: ServerConnection) -> None:
        if self._connection_attempts[side]:
            self.link.metrics["reconnect_count"] += 1
        self._connection_attempts[side] += 1
        self._connections[side].add(connection)
        try:
            async for encoded in connection:
                if not isinstance(encoded, str):
                    raise ValueError("binary frames are not supported")
                self.link.submit(side, LinkFrame.from_json(encoded), now=self._now())
        except ConnectionClosed:
            pass
        finally:
            self._connections[side].discard(connection)

    async def _delivery_loop(self) -> None:
        while True:
            for scheduled in self.link.deliver_due(now=self._now()):
                encoded = scheduled.frame.to_json()
                connections = tuple(self._connections[scheduled.destination])
                if connections:
                    await asyncio.gather(*(connection.send(encoded) for connection in connections))
                else:
                    self.link.metrics["dropped_disconnected"] += 1
            await asyncio.sleep(0.001)

    def _now(self) -> float:
        return time.monotonic() - self._start_time

    def _bound_port(self, server_index: int) -> int:
        if len(self._servers) <= server_index or not self._servers[server_index].sockets:
            return 0
        return int(self._servers[server_index].sockets[0].getsockname()[1])


def _parse_address(value: str) -> tuple[str, int]:
    host, separator, port = value.rpartition(":")
    if not separator or not host:
        raise argparse.ArgumentTypeError("address must be HOST:PORT")
    try:
        return host, int(port)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error


async def _run_cli(args: argparse.Namespace) -> None:
    profile = FaultProfile.from_toml(args.profile)
    mission_host, mission_port = args.mission_listen
    field_host, field_port = args.field_listen
    relay = WebSocketRelay(
        profile,
        mission_host=mission_host,
        mission_port=mission_port,
        field_host=field_host,
        field_port=field_port,
    )
    await relay.start()
    print(json.dumps({"event": "dtt-link.started", **relay.health()}, sort_keys=True), flush=True)
    try:
        while True:
            await asyncio.sleep(args.status_interval)
            print(
                json.dumps({"event": "dtt-link.status", **relay.health()}, sort_keys=True),
                flush=True,
            )
    finally:
        await relay.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission-listen", type=_parse_address, default=("127.0.0.1", 8765))
    parser.add_argument("--field-listen", type=_parse_address, default=("127.0.0.1", 8766))
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--status-interval", type=float, default=5.0)
    asyncio.run(_run_cli(parser.parse_args()))


if __name__ == "__main__":
    main()
