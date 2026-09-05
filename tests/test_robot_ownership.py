"""Process fencing for two Robot services sharing one local SQLite database."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import subprocess
import sys
import textwrap
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from deferred_teleop.protocol import ExecutionContract
from deferred_teleop.runtime import (
    DummyRobotService,
    EnvelopeFactory,
    FieldService,
    M3aRobotService,
    MissionService,
    SystemClock,
)
from deferred_teleop.storage import BusyError, NodeStore

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
WORKER = textwrap.dedent(
    """
    import asyncio
    import json
    import sys
    from pathlib import Path

    from deferred_teleop.external_effect import PersistentDummyExternalEffect
    from deferred_teleop.protocol import ExecutionContract
    from deferred_teleop.runtime import DummyRobotService, EnvelopeFactory, SystemClock
    from deferred_teleop.storage import NodeStore


    def emit(value):
        sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\\n")
        sys.stdout.flush()


    class GateAdapter(PersistentDummyExternalEffect):
        def __init__(self, path, gate):
            super().__init__(path)
            self.gate = gate

        def _wait(self, event):
            emit({"event": event})
            for line in sys.stdin:
                if line.strip() == "release":
                    return
            raise RuntimeError("control pipe closed while Robot was gated")

        def press(self, effect_key):
            if self.gate == "before-press":
                self._wait("press_entered")
            result = super().press(effect_key)
            if self.gate == "after-press":
                self._wait("press_persisted")
            return result


    class CountingAdapter(PersistentDummyExternalEffect):
        def __init__(self, path, device_id="dummy-external-button-1"):
            super().__init__(path, device_id=device_id)
            self.observe_calls = 0

        def observe(self, effect_key):
            self.observe_calls += 1
            return super().observe(effect_key)


    async def main():
        database = Path(sys.argv[1])
        external = Path(sys.argv[2])
        mode = sys.argv[3]
        clock = SystemClock()
        if mode == "no-adapter":
            adapter = None
        elif mode in {"before-press", "after-press"}:
            adapter = GateAdapter(external, mode)
        elif mode == "different-device":
            adapter = CountingAdapter(external, device_id="different-device")
        else:
            adapter = CountingAdapter(external)
        store = NodeStore(database)
        service = DummyRobotService(
            store,
            EnvelopeFactory("dummy-robot-1", clock),
            phase_duration=0.0,
            external_effect_adapter=adapter,
        )
        try:
            for line in sys.stdin:
                command = line.strip()
                if command == "quit":
                    return
                try:
                    if command == "handle":
                        contract = next(
                            message
                            for message in store.inbox_messages()
                            if isinstance(message.payload, ExecutionContract)
                        )
                        await service.handle(contract)
                        emit({"event": "done", "command": command})
                    elif command == "recover":
                        recovered = await service.recover()
                        emit({"event": "done", "command": command, "recovered": recovered})
                    elif command == "inspect":
                        journals = store.inspect_execution_journal()
                        inbox = store.inspect_inbox()
                        emit(
                            {
                                "event": "inspect",
                                "inbox": [row["processing_state"] for row in inbox],
                                "journal": [row["state"] for row in journals],
                                "press_count": (
                                    adapter.press_count if adapter is not None else None
                                ),
                                "observe_calls": (
                                    adapter.observe_calls if adapter is not None else None
                                ),
                            }
                        )
                    else:
                        emit({"event": "error", "type": "ValueError", "message": command})
                except BaseException as error:
                    emit({"event": "error", "type": type(error).__name__, "message": str(error)})
        finally:
            store.close()


    asyncio.run(main())
    """
)


class VirtualClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 5, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    async def sleep(self, _seconds: float) -> None:
        return None


class CancellationClock(VirtualClock):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def sleep(self, _seconds: float) -> None:
        self.entered.set()
        await self.release.wait()


async def _prepare_robot_database(tmp_path: Path) -> tuple[Path, Path]:
    clock = VirtualClock()
    mission_path = tmp_path / "mission.sqlite3"
    field_path = tmp_path / "field.sqlite3"
    robot_path = tmp_path / "robot.sqlite3"
    with NodeStore(mission_path) as mission_store, NodeStore(field_path) as field_store:
        mission = MissionService(
            mission_store,
            EnvelopeFactory("mission-1", clock),
            configured_one_way_delay=0.0,
        )
        field = FieldService(field_store, EnvelopeFactory("field-1", clock))
        intent = mission.submit_press_button()
        assert field_store.receive(intent, received_at=clock.now())
        await field.handle(intent)
        assignment = next(
            message
            for message in field_store.outbox_messages()
            if message.message_type == "task.assignment"
        )
        contract = next(
            message
            for message in field_store.outbox_messages()
            if message.message_type == "execution.contract"
        )
    with NodeStore(robot_path) as robot_store:
        assert robot_store.receive(assignment, received_at=clock.now())
        assert robot_store.receive(contract, received_at=clock.now())
    return robot_path, tmp_path / "external.jsonl"


def _worker_process(database: Path, external: Path, mode: str) -> subprocess.Popen[str]:
    environment = os.environ.copy()
    package_path = str(ROOT / "python" / "src")
    environment["PYTHONPATH"] = package_path + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.Popen(
        [PYTHON, "-u", "-c", WORKER, str(database), str(external), mode],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def _send(process: subprocess.Popen[str], command: str) -> None:
    assert process.stdin is not None
    process.stdin.write(command + "\n")
    process.stdin.flush()


def _event(process: subprocess.Popen[str]) -> dict[str, Any]:
    assert process.stdout is not None
    result: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

    def read_line() -> None:
        try:
            result.put(("line", process.stdout.readline()))
        except BaseException as error:
            result.put(("error", error))

    threading.Thread(target=read_line, daemon=True).start()
    try:
        kind, value = result.get(timeout=10)
    except queue.Empty as error:
        _kill(process)
        raise AssertionError("worker did not emit an event within 10 seconds") from error
    if kind == "error":
        assert isinstance(value, BaseException)
        raise value
    line = value
    assert isinstance(line, str)
    assert line, _process_error(process)
    return json.loads(line)


def _process_error(process: subprocess.Popen[str]) -> str:
    if process.stderr is None:
        return "worker exited without an event"
    if process.poll() is None:
        return "worker did not emit an event before its process exited"
    return f"worker exited: {process.stderr.read()}"


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        _send(process, "quit")
    assert process.wait(timeout=10) == 0, _process_error(process)


def _kill(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


@pytest.mark.parametrize("gate", ["before-press", "after-press"])
def test_owner_death_recovery_observes_without_repressing(tmp_path: Path, gate: str) -> None:
    async def scenario() -> None:
        database, external = await _prepare_robot_database(tmp_path)
        owner = _worker_process(database, external, gate)
        try:
            _send(owner, "handle")
            expected_event = "press_entered" if gate == "before-press" else "press_persisted"
            assert _event(owner) == {"event": expected_event}
            owner.kill()
            owner_exit = owner.wait(timeout=10)
            assert owner_exit < 0 if os.name != "nt" else owner_exit != 0

            observer = _worker_process(database, external, "observer")
            try:
                _send(observer, "recover")
                result = _event(observer)
                assert result == {"event": "done", "command": "recover", "recovered": 1}
                _send(observer, "inspect")
                state = _event(observer)
                assert state["inbox"] == ["PROCESSED", "PROCESSED"]
                assert state["journal"] == [
                    "SUCCEEDED" if gate == "after-press" else "HELD"
                ]
                assert state["press_count"] == (1 if gate == "after-press" else 0)
                assert state["observe_calls"] == 1
            finally:
                _stop(observer)
        finally:
            _kill(owner)

    asyncio.run(scenario())


def test_owner_blocks_handle_and_recover_until_terminal_commit(tmp_path: Path) -> None:
    async def scenario() -> None:
        database, external = await _prepare_robot_database(tmp_path)
        owner = _worker_process(database, external, "before-press")
        contender = None
        try:
            _send(owner, "handle")
            assert _event(owner) == {"event": "press_entered"}
            contender = _worker_process(database, external, "observer")
            _send(contender, "handle")
            assert _event(contender)["type"] == "BusyError"
            _send(contender, "recover")
            assert _event(contender)["type"] == "BusyError"
            _send(contender, "inspect")
            state = _event(contender)
            assert sorted(state["inbox"]) == ["PROCESSING", "RECEIVED"]
            assert state["journal"] == ["DISPATCH_RECORDED"]
            assert state["press_count"] == 0
            assert state["observe_calls"] == 0

            _send(owner, "release")
            assert _event(owner) == {"event": "done", "command": "handle"}
            _send(contender, "recover")
            assert _event(contender) == {"event": "done", "command": "recover", "recovered": 0}
            _send(contender, "inspect")
            state = _event(contender)
            assert state["journal"] == ["SUCCEEDED"]
            assert state["press_count"] == 1
            assert state["observe_calls"] == 0
        finally:
            _kill(owner)
            if contender is not None:
                _kill(contender)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["no-adapter", "different-device"])
def test_busy_wins_before_robot_configuration_or_external_io(tmp_path: Path, mode: str) -> None:
    async def scenario() -> None:
        database, external = await _prepare_robot_database(tmp_path)
        with NodeStore(database) as owner:
            with owner.exclusive_robot_owner():
                contender = _worker_process(database, external, mode)
                try:
                    _send(contender, "handle")
                    result = _event(contender)
                    assert result["type"] == "BusyError"
                    _send(contender, "inspect")
                    state = _event(contender)
                    assert state["press_count"] == (None if mode == "no-adapter" else 0)
                    assert state["observe_calls"] == (None if mode == "no-adapter" else 0)
                finally:
                    _kill(contender)

    asyncio.run(scenario())


def test_store_lock_is_canonical_and_retained_after_release(tmp_path: Path) -> None:
    database = tmp_path / "real" / "robot.sqlite3"
    database.parent.mkdir()
    alias = tmp_path / "alias.sqlite3"
    try:
        alias.symlink_to(database)
    except OSError as error:
        if os.name == "nt":
            pytest.skip(f"Windows symlink privilege is unavailable: {error}")
        raise
    with NodeStore(database) as owner, NodeStore(alias) as contender:
        assert owner.path == contender.path == database.resolve()
        with owner.exclusive_robot_owner():
            with pytest.raises(BusyError, match="ownership is busy"):
                with contender.exclusive_robot_owner():
                    raise AssertionError("unreachable")
        lock_file = Path(f"{database.resolve()}.robot-owner.lock")
        assert lock_file.exists()
        assert lock_file.stat().st_size == 0
        with contender.exclusive_robot_owner():
            pass


def test_independent_databases_do_not_share_owner_lock(tmp_path: Path) -> None:
    with NodeStore(tmp_path / "one.sqlite3") as first, NodeStore(
        tmp_path / "two.sqlite3"
    ) as second:
        with first.exclusive_robot_owner():
            with second.exclusive_robot_owner():
                pass


def test_m3_robot_inherits_owner_fence_without_a_second_harness(tmp_path: Path) -> None:
    async def scenario() -> None:
        database, _external = await _prepare_robot_database(tmp_path)
        with NodeStore(database) as first_store, NodeStore(database) as second_store:
            second = M3aRobotService(
                second_store,
                EnvelopeFactory("m3-second", SystemClock()),
                phase_duration=0.0,
            )
            envelope = next(
                message
                for message in second_store.inbox_messages()
                if isinstance(message.payload, ExecutionContract)
            )
            with first_store.exclusive_robot_owner():
                with pytest.raises(BusyError):
                    await second.handle(envelope)

    asyncio.run(scenario())


def test_owner_lock_releases_on_exception_and_replay_can_reopen(tmp_path: Path) -> None:
    database = tmp_path / "robot.sqlite3"
    with NodeStore(database) as store:
        with pytest.raises(RuntimeError):
            with store.exclusive_robot_owner():
                raise RuntimeError("owner body failed")
        with store.exclusive_robot_owner():
            pass
    with NodeStore(database) as reopened:
        with reopened.exclusive_robot_owner():
            pass


def test_asyncio_cancellation_releases_owner_during_dummy_phase(tmp_path: Path) -> None:
    async def scenario() -> None:
        database, _external = await _prepare_robot_database(tmp_path)
        clock = CancellationClock()
        with NodeStore(database) as store:
            service = DummyRobotService(
                store,
                EnvelopeFactory("dummy-robot-1", clock),
                phase_duration=1.0,
            )
            contract = next(
                message
                for message in store.inbox_messages()
                if isinstance(message.payload, ExecutionContract)
            )
            running = asyncio.create_task(service.handle(contract))
            await clock.entered.wait()
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running

        with NodeStore(database) as contender:
            with contender.exclusive_robot_owner():
                pass

    asyncio.run(scenario())
