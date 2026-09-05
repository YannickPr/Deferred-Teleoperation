"""Focused M3a unit proofs for the pure policy and hidden spatial fixture."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from deferred_teleop.external_effect import ExternalEffectObservation
from deferred_teleop.m3a_types import (
    M3aEnsureLatchedIntent,
    SpatialPressCommand,
    TwoButtonAction,
    TwoButtonObservation,
)
from deferred_teleop.protocol import (
    ContractState,
    ExecutionContract,
    ExecutionEvent,
    LocalTwoButtonDecision,
    M3aSpatialExecutionContext,
    MessageEnvelope,
    Quaternion,
    SpatialFrame,
    TwoButtonEffectEvidence,
    Vector3,
)
from deferred_teleop.protocol import (
    M3aEnsureLatchedIntent as WireM3aEnsureLatchedIntent,
)
from deferred_teleop.protocol import (
    SpatialPressCommand as WireSpatialPressCommand,
)
from deferred_teleop.runtime import (
    EnvelopeFactory,
    M3aFieldService,
    M3aMissionService,
    M3aRobotService,
)
from deferred_teleop.storage import BudgetDeadlineError, NodeStore
from deferred_teleop.two_button_fixture import (
    BUTTON_A,
    BUTTON_B,
    ButtonContact,
    FixtureScenario,
    SpatialBindingConflictError,
    SpatialExternalEffectAdapter,
    SpatialFixtureError,
    TwoButtonFixture,
)
from deferred_teleop.two_button_policy import decide_two_button, derive_spatial_press_command

NOW = datetime(2026, 9, 5, 0, 0, tzinfo=UTC)
OPERATION_ID = UUID("30000000-0000-4000-8000-000000000001")


class Clock:
    def __init__(self, current: datetime = NOW) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


def _intent(
    reference: TwoButtonObservation,
    *,
    operation_id: UUID = OPERATION_ID,
    tolerance: float = 0.05,
    target: str = BUTTON_A,
) -> M3aEnsureLatchedIntent:
    detection = reference.detections[0]
    return M3aEnsureLatchedIntent(
        operation_id=operation_id,
        intent_revision=1,
        semantic_effect_id="semantic-effect-1",
        target_entity_id=target,
        desired_latched=True,
        reference_observation_id=reference.observation_id,
        reference_detection_id=detection.detection_id,
        reference_digest=reference.canonical_payload_digest,
        reference_pose=detection.pose,
        reference_frame_id=reference.frame_id,
        reference_calibration_version=reference.calibration_version,
        reference_world_revision=reference.world_revision,
        reference_observed_at=reference.observed_at,
        same_identity_only=True,
        max_displacement_m=tolerance,
        expires_at=NOW + timedelta(minutes=20),
    )


def test_nominal_policy_command_and_fsynced_binding_reopen(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "spatial-device.jsonl"
    fixture = TwoButtonFixture(path, clock=clock)
    reference = fixture.reference_observation(observed_at=NOW)
    current = fixture.current_observation(observed_at=NOW + timedelta(seconds=1))
    intent = _intent(reference)

    decision = decide_two_button(intent, reference, current)
    assert decision.action is TwoButtonAction.EXECUTE
    assert decision.selected_detection_id == reference.detections[0].detection_id
    command = derive_spatial_press_command(
        decision,
        effect_key="effect:nominal",
        command_id="command:nominal",
        reference_observation=reference,
        current_observation=current,
    )
    adapter = SpatialExternalEffectAdapter(fixture)
    receipt = adapter.bind(command.effect_key, command)
    assert receipt.device_id == fixture.device_id
    assert receipt.command_digest == command.command_digest
    assert adapter.bind(command.effect_key, command) == receipt

    proof = adapter.press(command.effect_key)
    assert proof.outcome.value == "APPLIED"
    assert proof.details["contact"] == BUTTON_A
    assert fixture.a_counter == 1
    assert fixture.b_counter == 0
    assert fixture.a_latched is True
    assert len(fixture.records) == 1
    record = fixture.records[0]
    assert record["command_digest"] == command.command_digest
    assert record["command_bytes"] == command.canonical_bytes().decode("utf-8")
    assert record["position_m"] == list(command.position_m)
    assert record["contact"] == ButtonContact.A.value
    assert record["a_counter"] == 1
    assert record["b_counter"] == 0

    reopened = TwoButtonFixture(path, clock=clock)
    reopened_adapter = SpatialExternalEffectAdapter(reopened)
    assert reopened.a_counter == 1
    assert reopened.a_latched is True
    assert reopened_adapter.binding(command.effect_key) == receipt
    assert (
        reopened_adapter.observe(command.effect_key).details["command_digest"]
        == command.command_digest
    )


def test_boundary_reanchors_and_old_point_is_none_for_fixed_reference(tmp_path: Path) -> None:
    clock = Clock()
    fixture = TwoButtonFixture(
        tmp_path / "boundary.jsonl",
        scenario=FixtureScenario.S1_BOUNDARY,
        clock=clock,
    )
    reference = fixture.reference_observation(observed_at=NOW)
    current = fixture.current_observation(observed_at=NOW + timedelta(seconds=1))
    intent = _intent(reference, tolerance=fixture.max_displacement_m)
    decision = decide_two_button(intent, reference, current)
    assert decision.action is TwoButtonAction.REANCHOR_EXECUTE
    assert decision.displacement_m == pytest.approx(fixture.max_displacement_m)

    command = derive_spatial_press_command(
        decision,
        effect_key="effect:boundary",
        command_id="command:boundary",
        reference_observation=reference,
        current_observation=current,
    )
    adapter = SpatialExternalEffectAdapter(fixture)
    adapter.bind(command.effect_key, command)
    assert adapter.press(command.effect_key).details["contact"] == BUTTON_A

    baseline = SpatialPressCommand.from_pose(
        command_id="command:fixed-reference",
        effect_key="effect:fixed-reference",
        pose=reference.detections[0].pose,
        source_observation_id=reference.observation_id,
        source_detection_id=reference.detections[0].detection_id,
    )
    adapter.bind(baseline.effect_key, baseline)
    assert adapter.press(baseline.effect_key).details["contact"] == ButtonContact.NONE.value
    assert fixture.a_counter == 1


def test_tolerance_plus_epsilon_holds_without_device_activity(tmp_path: Path) -> None:
    fixture = TwoButtonFixture(
        tmp_path / "epsilon.jsonl",
        scenario=FixtureScenario.S1_EPSILON,
        clock=Clock(),
    )
    reference = fixture.reference_observation(observed_at=NOW)
    current = fixture.current_observation(observed_at=NOW + timedelta(seconds=1))
    decision = decide_two_button(_intent(reference), reference, current)
    assert decision.action is TwoButtonAction.HOLD_AMBIGUOUS
    assert decision.reason == "DISPLACEMENT_EXCEEDS_TOLERANCE"
    assert decision.displacement_m > fixture.max_displacement_m
    assert fixture.press_count == 0
    assert fixture.binding_records == ()


def test_ambiguous_swap_holds_while_fixed_reference_hits_real_b(tmp_path: Path) -> None:
    fixture = TwoButtonFixture(
        tmp_path / "swap.jsonl",
        scenario=FixtureScenario.S2_SWAP,
        clock=Clock(),
    )
    reference = fixture.reference_observation(observed_at=NOW)
    current = fixture.current_observation(observed_at=NOW + timedelta(seconds=1))
    decision = decide_two_button(_intent(reference), reference, current)
    assert decision.action is TwoButtonAction.HOLD_AMBIGUOUS
    assert decision.selected_detection_id is None

    # The explicit controller ablation sends the stale authoring point.  The
    # fixture resolves that point against hidden current truth, which is B.
    baseline = SpatialPressCommand.from_pose(
        command_id="command:fixed-reference",
        effect_key="effect:fixed-reference",
        pose=reference.detections[0].pose,
        source_observation_id=reference.observation_id,
        source_detection_id=reference.detections[0].detection_id,
    )
    adapter = SpatialExternalEffectAdapter(fixture)
    adapter.bind(baseline.effect_key, baseline)
    assert adapter.press(baseline.effect_key).details["contact"] == BUTTON_B
    assert fixture.a_counter == 0
    assert fixture.b_counter == 1


def test_already_latched_is_pure_preacceptance_and_reopen_does_not_add_pulse(
    tmp_path: Path,
) -> None:
    clock = Clock()
    path = tmp_path / "already-latched.jsonl"
    fixture = TwoButtonFixture(
        path,
        scenario=FixtureScenario.S4_ALREADY_LATCHED,
        clock=clock,
    )
    assert fixture.a_counter == 1
    assert fixture.a_latched is True
    reference = fixture.reference_observation(observed_at=NOW)
    current = fixture.current_observation(observed_at=NOW + timedelta(seconds=1))
    evidence = fixture.level_evidence(BUTTON_A, observed_at=NOW + timedelta(seconds=2))
    decision = decide_two_button(_intent(reference), reference, current, evidence)
    assert decision.action is TwoButtonAction.RECOGNIZE_EFFECT
    assert decision.reason == "ALREADY_LATCHED"
    assert decision.budget_state == "ZERO_RESERVATION_REQUIRED"
    assert decision.selected_detection_id is None
    assert fixture.press_count == 1
    assert fixture.binding_records == ()

    reopened = TwoButtonFixture(path, scenario=FixtureScenario.S4_ALREADY_LATCHED, clock=clock)
    assert reopened.press_count == 1
    assert reopened.a_counter == 1
    assert reopened.level_evidence(BUTTON_A).actual_latched is True


def test_reference_digest_pose_and_context_mismatches_are_holds(tmp_path: Path) -> None:
    fixture = TwoButtonFixture(tmp_path / "integrity.jsonl", clock=Clock())
    reference = fixture.reference_observation(observed_at=NOW)
    current = fixture.current_observation(observed_at=NOW + timedelta(seconds=1))
    intent = _intent(reference)

    changed_pose = replace(
        reference.detections[0],
        pose=PoseFactory.make(
            position=(0.41, 0.1, 0.2),
            frame_id=reference.frame_id,
            calibration_version=reference.calibration_version,
        ),
    )
    changed_reference = replace(reference, detections=(changed_pose,), canonical_payload_digest="")
    changed_decision = decide_two_button(intent, changed_reference, current)
    assert changed_decision.action is TwoButtonAction.HOLD_REFERENCE_MISMATCH

    context_pose = PoseFactory.make(
        position=(0.4, 0.1, 0.2),
        frame_id="other-frame",
        calibration_version=reference.calibration_version,
    )
    context_reference = TwoButtonObservation(
        observation_id=reference.observation_id,
        source_id=reference.source_id,
        world_revision=reference.world_revision,
        observed_at=reference.observed_at,
        produced_at=reference.produced_at,
        frame_id="other-frame",
        calibration_version=reference.calibration_version,
        detections=(replace(reference.detections[0], pose=context_pose),),
    )
    context_decision = decide_two_button(intent, context_reference, current)
    assert context_decision.action is TwoButtonAction.HOLD_CONTEXT_MISMATCH


def test_binding_mismatch_is_rejected_and_policy_has_no_fixture_dependency(tmp_path: Path) -> None:
    fixture = TwoButtonFixture(tmp_path / "binding.jsonl", clock=Clock())
    reference = fixture.reference_observation(observed_at=NOW)
    command = SpatialPressCommand.from_pose(
        command_id="command:immutable",
        effect_key="effect:immutable",
        pose=reference.detections[0].pose,
        source_observation_id=reference.observation_id,
        source_detection_id=reference.detections[0].detection_id,
    )
    adapter = SpatialExternalEffectAdapter(fixture)
    adapter.bind(command.effect_key, command)
    changed = replace(
        command,
        position_m=(command.position_m[0] + 0.01, command.position_m[1], command.position_m[2]),
        command_digest="",
    )
    with pytest.raises(SpatialBindingConflictError):
        adapter.bind(command.effect_key, changed)
    assert fixture.press_count == 0
    assert len(fixture.binding_records) == 1


def test_binding_key_mismatch_and_unbound_or_mutated_press_have_zero_pulses(
    tmp_path: Path,
) -> None:
    fixture = TwoButtonFixture(tmp_path / "binding-guard.jsonl", clock=Clock())
    reference = fixture.reference_observation(observed_at=NOW)
    command = SpatialPressCommand.from_pose(
        command_id="command:guard",
        effect_key="effect:command-key",
        pose=reference.detections[0].pose,
        source_observation_id=reference.observation_id,
        source_detection_id=reference.detections[0].detection_id,
    )
    adapter = SpatialExternalEffectAdapter(fixture)
    with pytest.raises(SpatialBindingConflictError):
        adapter.bind("effect:receipt-key", command)
    assert fixture.binding_records == ()
    with pytest.raises(SpatialFixtureError, match="no persisted device binding"):
        fixture.press_at(command)
    assert fixture.press_count == 0

    adapter.bind(command.effect_key, command)
    mutated = replace(
        command,
        position_m=(command.position_m[0] + 0.2, command.position_m[1], command.position_m[2]),
        command_digest="",
    )
    with pytest.raises(SpatialBindingConflictError):
        fixture.press_at(mutated)
    assert fixture.press_count == 0


def test_file_fsync_does_not_require_directory_fsync_constant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = TwoButtonFixture(tmp_path / "file-only-fsync.jsonl", clock=Clock())
    reference = fixture.reference_observation(observed_at=NOW)
    command = SpatialPressCommand.from_pose(
        command_id="command:file-only",
        effect_key="effect:file-only",
        pose=reference.detections[0].pose,
        source_observation_id=reference.observation_id,
        source_detection_id=reference.detections[0].detection_id,
    )
    monkeypatch.delattr(os, "O_DIRECTORY", raising=False)
    adapter = SpatialExternalEffectAdapter(fixture)
    adapter.bind(command.effect_key, command)
    adapter.press(command.effect_key)
    reopened = TwoButtonFixture(fixture.path, clock=Clock())
    assert reopened.a_counter == 1


class PoseFactory:
    @staticmethod
    def make(
        *,
        position: tuple[float, float, float],
        frame_id: str,
        calibration_version: str,
    ):
        from deferred_teleop.protocol import Pose

        return Pose(
            position=Vector3(x=position[0], y=position[1], z=position[2]),
            orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
            frame=SpatialFrame(
                frame_id=frame_id,
                calibration_version=calibration_version,
            ),
        )


class M3aServiceHarness:
    """Virtual-time Mission/Field/Robot/device harness for the five oracles."""

    def __init__(self, data_dir: Path, scenario: FixtureScenario) -> None:
        self.data_dir = data_dir
        self.clock = Clock()
        self.fixture = TwoButtonFixture(
            data_dir / "device.jsonl",
            scenario=scenario,
            clock=self.clock,
        )
        self.mission_store = NodeStore(data_dir / "mission.sqlite3")
        self.field_store = NodeStore(data_dir / "field.sqlite3")
        self.robot_store = NodeStore(data_dir / "robot.sqlite3")
        self.mission = M3aMissionService(
            self.mission_store,
            EnvelopeFactory("mission-1", self.clock),
            configured_one_way_delay=1200.0,
        )
        self.field = M3aFieldService(
            self.field_store,
            EnvelopeFactory("field-1", self.clock),
            m3a_device_id=self.fixture.device_id,
        )
        self.robot = M3aRobotService(
            self.robot_store,
            EnvelopeFactory("dummy-robot-1", self.clock),
            external_effect_adapter=SpatialExternalEffectAdapter(self.fixture),
            max_elapsed_seconds=60.0,
        )
        self.current_observation: TwoButtonObservation | None = None

    def close(self) -> None:
        self.robot_store.close()
        self.field_store.close()
        self.mission_store.close()

    async def _deliver(
        self,
        source_store: NodeStore,
        target: object,
        envelope: MessageEnvelope,
    ) -> bool:
        if envelope.not_before is not None and self.clock.now() < envelope.not_before:
            return False
        target_store = target.store
        is_new = target_store.receive(envelope, received_at=self.clock.now())
        try:
            source_store.acknowledge(envelope.message_id, acked_at=self.clock.now())
        except KeyError:
            # Direct duplicate/conflict injections are not necessarily rows in
            # the source outbox; transport ACK handling is irrelevant to them.
            pass
        if is_new:
            await target.handle(envelope)
        return is_new

    async def _route(self, source: object, target: object, destination: str) -> bool:
        progressed = False
        source_store = source.store
        for envelope in list(source_store.pending_outbox(now=self.clock.now())):
            if envelope.destination_id != destination:
                continue
            progressed = await self._deliver(source_store, target, envelope) or progressed
        return progressed

    async def record_local_current_observation(self) -> TwoButtonObservation:
        """Acquire current truth locally at Field after the virtual transit."""

        intent_message = next(
            message
            for message in self.mission_store.outbox_messages()
            if isinstance(message.payload, WireM3aEnsureLatchedIntent)
        )
        intent = intent_message.payload
        current = self.fixture.current_observation(
            observed_at=self.clock.now(),
            target_entity_id=intent.target_entity_id,
        )
        self.current_observation = current
        envelope = self.field.record_m3a_current_observation(
            current,
            operation_id=intent.operation_id,
            correlation_id=intent_message.correlation_id,
        )
        await self.field.handle(envelope)
        return current

    async def settle(self) -> None:
        # Reference observation is available immediately; intent waits for the
        # configured 1200-second Mission->Field transit.  Field acquires the
        # current observation locally only after that delay.
        assert await self._route(self.mission, self.field, "field-1")
        assert self.clock.now() == NOW
        self.clock.advance(1200.0)
        await self.record_local_current_observation()
        for _ in range(32):
            progressed = False
            for source, target, destination in (
                (self.mission, self.field, "field-1"),
                (self.field, self.robot, "dummy-robot-1"),
                (self.robot, self.field, "field-1"),
                (self.field, self.mission, "mission-1"),
            ):
                progressed = await self._route(source, target, destination) or progressed
            if not progressed:
                return
        raise AssertionError("M3a virtual service harness did not converge")


def _run(coroutine: object) -> object:
    return asyncio.run(coroutine)


def _m3a_messages(store: NodeStore, message_type: type) -> list[MessageEnvelope]:
    return [
        message
        for message in store.inbox_messages() + store.outbox_messages()
        if isinstance(message.payload, message_type)
    ]


def test_m3a_services_nominal_transit_budget_contact_and_reopen(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = M3aServiceHarness(tmp_path, FixtureScenario.S0_NOMINAL)
        try:
            reference = harness.fixture.reference_observation(observed_at=NOW)
            intent = harness.mission.submit_ensure_button_latched(
                reference,
                max_displacement_m=harness.fixture.max_displacement_m,
            )
            assert intent.not_before == NOW + timedelta(seconds=1200)
            await harness.settle()

            contexts = _m3a_messages(harness.robot_store, M3aSpatialExecutionContext)
            assert len(contexts) == 1
            context = contexts[0].payload
            assert context.reference_observation_id == reference.observation_id
            assert context.reference_digest == reference.canonical_payload_digest
            assert context.current_observation_envelope_id
            assert harness.current_observation is not None
            assert harness.current_observation.observed_at == NOW + timedelta(seconds=1200)
            decisions = _m3a_messages(harness.robot_store, LocalTwoButtonDecision)
            assert len(decisions) == 1
            decision = decisions[0].payload
            assert decision.action.value == "EXECUTE"
            assert decision.current_observation_id == harness.current_observation.observation_id
            assert decision.command_digest
            assert len(harness.fixture.records) == 1
            assert harness.fixture.records[0]["contact"] == BUTTON_A
            assert harness.fixture.a_counter == 1
            assert harness.fixture.b_counter == 0
            journal = harness.robot_store.inspect_execution_journal()
            budget = harness.robot_store.inspect_autonomy_budget()
            assert len(journal) == len(budget) == 1
            assert journal[0]["state"] == ContractState.SUCCEEDED.value
            assert journal[0]["effect_count"] == 0
            assert budget[0]["actions_reserved"] == 1
            assert budget[0]["command_digest"] == decision.command_digest
            view = harness.mission.m3a_view()
            assert view["message_type"] == "m3a.view"
            assert view["current_observation"]["observation_id"] == (
                harness.current_observation.observation_id
            )
            assert view["current_observation"]["observed_at"] == (
                NOW + timedelta(seconds=1200)
            ).isoformat().replace("+00:00", "Z")
            assert view["business_result"] == "APPLIED"
            assert view["physical_contact"] == BUTTON_A
            assert view["a_counter"] == 1
            assert view["b_counter"] == 0
            assert view["a_latched"] is True
            assert view["b_latched"] is False
            assert view["effect_evidence"]["semantic_goal_attained"] is True
            effect_rows = _m3a_messages(harness.robot_store, TwoButtonEffectEvidence)
            assert len(effect_rows) == 1
            assert effect_rows[0].payload.physical_contact == BUTTON_A
            assert len(harness.mission_store.inspect_m3a_effect_bindings()) == 1
            assert len(harness.mission_store.inspect_m3a_effect_diagnostics()) == 0

            # Reopen both the SQLite Robot store and the independent device,
            # then deliver a new transport envelope for the exact contract.
            contract_message = next(
                message
                for message in harness.field_store.outbox_messages()
                if isinstance(message.payload, ExecutionContract)
            )
            contract = contract_message.payload
            harness.robot_store.close()
            harness.robot_store = NodeStore(tmp_path / "robot.sqlite3")
            reopened_fixture = TwoButtonFixture(
                tmp_path / "device.jsonl", clock=harness.clock
            )
            harness.robot = M3aRobotService(
                harness.robot_store,
                EnvelopeFactory("dummy-robot-1", harness.clock),
                external_effect_adapter=SpatialExternalEffectAdapter(reopened_fixture),
                max_elapsed_seconds=60.0,
            )
            assert len(_m3a_messages(harness.robot_store, TwoButtonEffectEvidence)) == 1
            duplicate = harness.field.factory.make(
                "execution.contract",
                "dummy-robot-1",
                contract_message.correlation_id,
                contract,
                causation_id=contract_message.causation_id,
                created_at=harness.clock.now(),
            )
            await harness._deliver(harness.field_store, harness.robot, duplicate)
            assert reopened_fixture.press_count == 1
            assert harness.robot_store.inspect_autonomy_budget()[0]["actions_reserved"] == 1
        finally:
            harness.close()

    _run(scenario())


def test_m3a_services_target_b_derives_expected_contact_from_context(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = M3aServiceHarness(tmp_path, FixtureScenario.S0_NOMINAL)
        try:
            reference = harness.fixture.reference_observation(
                observed_at=NOW,
                target_entity_id=BUTTON_B,
            )
            harness.mission.submit_ensure_button_latched(
                reference,
                target_entity_id=BUTTON_B,
                semantic_effect_id="ensure-latched:B",
            )
            await harness.settle()
            decision = _m3a_messages(harness.robot_store, LocalTwoButtonDecision)[0].payload
            assert decision.action.value == "EXECUTE"
            assert harness.fixture.a_counter == 0
            assert harness.fixture.b_counter == 1
            assert harness.fixture.records[0]["contact"] == BUTTON_B
            assert len(harness.robot_store.inspect_autonomy_budget()) == 1
            view = harness.mission.m3a_view()
            assert view["business_result"] == "APPLIED"
            assert view["physical_contact"] == BUTTON_B
            assert view["a_counter"] == 0
            assert view["b_counter"] == 1
            assert view["a_latched"] is False
            assert view["b_latched"] is True
        finally:
            harness.close()

    _run(scenario())


def test_m3a_mission_effect_waits_for_terminal_before_binding(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = M3aServiceHarness(tmp_path, FixtureScenario.S0_NOMINAL)
        try:
            reference = harness.fixture.reference_observation(observed_at=NOW)
            harness.mission.submit_ensure_button_latched(reference)
            assert await harness._route(harness.mission, harness.field, "field-1")
            harness.clock.advance(1200.0)
            await harness.record_local_current_observation()
            assert await harness._route(harness.mission, harness.field, "field-1")
            assert await harness._route(harness.field, harness.robot, "dummy-robot-1")
            assert await harness._route(harness.robot, harness.field, "field-1")

            pending = [
                message
                for message in harness.field_store.pending_outbox(now=harness.clock.now())
                if message.destination_id == "mission-1"
            ]
            effect = next(
                message
                for message in pending
                if isinstance(message.payload, TwoButtonEffectEvidence)
            )
            terminal = next(
                message
                for message in pending
                if isinstance(message.payload, ExecutionEvent)
                and message.payload.next_state in {ContractState.SUCCEEDED, ContractState.HELD}
            )
            for message in pending:
                if message.message_id in {effect.message_id, terminal.message_id}:
                    continue
                await harness._deliver(harness.field_store, harness.mission, message)

            assert await harness._deliver(harness.field_store, harness.mission, effect)
            effect_row = next(
                row
                for row in harness.mission_store.inspect_inbox()
                if row["message_id"] == str(effect.message_id)
            )
            assert effect_row["processing_state"] == "FAILED"
            assert len(harness.mission_store.inspect_m3a_effect_bindings()) == 0
            assert harness.mission.m3a_view()["effect_evidence"] is None

            assert await harness._deliver(harness.field_store, harness.mission, terminal)
            assert len(harness.mission_store.inspect_m3a_effect_bindings()) == 1
            assert harness.mission.m3a_view()["business_result"] == "APPLIED"
        finally:
            harness.close()

    _run(scenario())


@pytest.mark.parametrize("digest_mode", ["missing", "mismatch"])
def test_m3a_unverified_adapter_digest_closes_unknown_and_replays_without_press(
    tmp_path: Path,
    digest_mode: str,
) -> None:
    async def scenario() -> None:
        harness = M3aServiceHarness(tmp_path, FixtureScenario.S0_NOMINAL)
        try:
            reference = harness.fixture.reference_observation(observed_at=NOW)
            harness.mission.submit_ensure_button_latched(reference)
            adapter = harness.robot.external_effect_adapter
            assert isinstance(adapter, SpatialExternalEffectAdapter)
            original_observe = adapter.observe

            def unverified_observe(effect_key: str) -> ExternalEffectObservation:
                observed = original_observe(effect_key)
                details = dict(observed.details)
                if digest_mode == "missing":
                    details.pop("command_digest", None)
                else:
                    details["command_digest"] = "sha256:" + "0" * 64
                return ExternalEffectObservation(
                    effect_key=observed.effect_key,
                    device_id=observed.device_id,
                    outcome=observed.outcome,
                    observed_at=observed.observed_at,
                    observation_id=observed.observation_id,
                    details=details,
                )

            adapter.observe = unverified_observe  # type: ignore[method-assign]
            await harness.settle()
            assert harness.fixture.press_count == 1
            assert len(harness.fixture.binding_records) == 1
            assert len(harness.robot_store.inspect_autonomy_budget()) == 1
            assert harness.robot_store.inspect_autonomy_budget()[0]["actions_reserved"] == 1
            journal = harness.robot_store.inspect_execution_journal()[0]
            assert journal["state"] == ContractState.HELD.value
            effect = _m3a_messages(harness.robot_store, TwoButtonEffectEvidence)[0].payload
            assert effect.outcome == "UNKNOWN"
            assert effect.command_digest_verified is False
            assert effect.command_digest_diagnostic == (
                "M3A_COMMAND_DIGEST_MISSING"
                if digest_mode == "missing"
                else "M3A_COMMAND_DIGEST_MISMATCH"
            )
            assert effect.physical_contact is None
            assert effect.a_counter is None
            assert effect.b_counter is None
            assert effect.a_latched is None
            assert effect.b_latched is None
            view = harness.mission.m3a_view()
            assert view["business_result"] == "OUTCOME_UNKNOWN"
            assert view["physical_contact"] is None
            assert view["a_counter"] is None
            assert view["b_counter"] is None
            assert len(harness.mission_store.inspect_m3a_effect_bindings()) == 0
            assert len(harness.mission_store.inspect_m3a_effect_diagnostics()) == 1

            contract_message = next(
                message
                for message in harness.field_store.outbox_messages()
                if isinstance(message.payload, ExecutionContract)
            )
            contract = contract_message.payload
            harness.robot_store.close()
            harness.robot_store = NodeStore(tmp_path / "robot.sqlite3")
            reopened_fixture = TwoButtonFixture(
                tmp_path / "device.jsonl", clock=harness.clock
            )
            harness.robot = M3aRobotService(
                harness.robot_store,
                EnvelopeFactory("dummy-robot-1", harness.clock),
                external_effect_adapter=SpatialExternalEffectAdapter(reopened_fixture),
                max_elapsed_seconds=60.0,
            )
            duplicate = harness.field.factory.make(
                "execution.contract",
                "dummy-robot-1",
                contract_message.correlation_id,
                contract,
                causation_id=contract_message.causation_id,
                created_at=harness.clock.now(),
            )
            await harness._deliver(harness.field_store, harness.robot, duplicate)
            assert reopened_fixture.press_count == 1
            assert harness.robot_store.inspect_execution_journal()[0]["state"] == (
                ContractState.HELD.value
            )
            harness.mission_store.close()
            harness.mission_store = NodeStore(tmp_path / "mission.sqlite3")
            harness.mission = M3aMissionService(
                harness.mission_store,
                EnvelopeFactory("mission-1", harness.clock),
                configured_one_way_delay=1200.0,
            )
            assert harness.mission.m3a_view() == view
            assert len(harness.mission_store.inspect_m3a_effect_bindings()) == 0
            assert len(harness.mission_store.inspect_m3a_effect_diagnostics()) == 1
        finally:
            harness.close()

    _run(scenario())


def test_m3a_effect_proof_mutations_cannot_replace_last_valid_view(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = M3aServiceHarness(tmp_path, FixtureScenario.S0_NOMINAL)
        try:
            reference = harness.fixture.reference_observation(observed_at=NOW)
            harness.mission.submit_ensure_button_latched(reference)
            await harness.settle()
            valid_view = harness.mission.m3a_view()
            valid_effect_message = _m3a_messages(
                harness.robot_store, TwoButtonEffectEvidence
            )[0]
            valid_effect = valid_effect_message.payload
            assert valid_view["physical_contact"] == BUTTON_A

            mutations = (
                valid_effect.model_copy(
                    update={
                        "contract_id": UUID(int=(valid_effect.contract_id.int + 1) % (1 << 128))
                    }
                ),
                valid_effect.model_copy(
                    update={"command_digest": "sha256:" + "0" * 64}
                ),
                valid_effect.model_copy(
                    update={"effect_key": f"press:{valid_effect.operation_id}:99"}
                ),
                valid_effect.model_copy(update={"target_entity_id": BUTTON_B}),
                valid_effect.model_copy(update={"device_id": "other-device"}),
                valid_effect.model_copy(update={"a_counter": 999}),
                valid_effect.model_copy(update={"observation_id": "forged-observation"}),
                valid_effect.model_copy(
                    update={
                        "outcome": "UNKNOWN",
                        "command_digest_verified": False,
                        "command_digest_diagnostic": "digest-mismatch",
                        "physical_contact": None,
                        "command_executed": None,
                        "semantic_goal_attained": None,
                        "a_counter": None,
                        "b_counter": None,
                        "a_latched": None,
                        "b_latched": None,
                    }
                ),
            )
            for mutation in mutations:
                forged = harness.robot.factory.make(
                    "m3a.spatial.effect",
                    "field-1",
                    valid_effect_message.correlation_id,
                    mutation,
                    source_id="dummy-robot-1",
                    causation_id=valid_effect_message.causation_id,
                )
                assert await harness._deliver(harness.robot_store, harness.field, forged)

            wrong_source = harness.robot.factory.make(
                "m3a.spatial.effect",
                "field-1",
                valid_effect_message.correlation_id,
                valid_effect,
                source_id="unexpected-robot",
                causation_id=valid_effect_message.causation_id,
            )
            assert await harness._deliver(harness.robot_store, harness.field, wrong_source)
            # Rejected proofs remain durably auditable in Field's inbox, while
            # only the canonical proof is forwarded to Mission.
            assert sum(
                isinstance(message.payload, TwoButtonEffectEvidence)
                for message in harness.field_store.outbox_messages()
            ) == 1
            assert len(harness.field_store.inspect_m3a_effect_bindings()) == 1
            assert len(harness.field_store.inspect_m3a_effect_conflicts()) >= 2

            duplicate_robot = harness.robot.factory.make(
                "m3a.spatial.effect",
                "field-1",
                valid_effect_message.correlation_id,
                valid_effect,
                source_id="dummy-robot-1",
                causation_id=valid_effect_message.causation_id,
                created_at=harness.clock.now(),
            )
            assert await harness._deliver(
                harness.robot_store,
                harness.field,
                duplicate_robot,
            )
            assert await harness._route(harness.field, harness.mission, "mission-1")
            assert harness.mission.m3a_view() == valid_view

            # Mission validates direct effect messages and preserves the
            # canonical proof.
            bypass = harness.field.factory.make(
                "m3a.spatial.effect",
                "mission-1",
                valid_effect_message.correlation_id,
                valid_effect.model_copy(update={"target_entity_id": BUTTON_B}),
                source_id="field-1",
                causation_id=valid_effect_message.causation_id,
            )
            assert await harness._deliver(harness.field_store, harness.mission, bypass)
            assert harness.mission.m3a_view() == valid_view

            device_bypass = harness.field.factory.make(
                "m3a.spatial.effect",
                "mission-1",
                valid_effect_message.correlation_id,
                valid_effect.model_copy(update={"device_id": "other-device"}),
                source_id="field-1",
                causation_id=valid_effect_message.causation_id,
            )
            assert await harness._deliver(harness.field_store, harness.mission, device_bypass)
            assert harness.mission.m3a_view() == valid_view

            for mutation in (
                valid_effect.model_copy(update={"a_counter": 999}),
                valid_effect.model_copy(update={"observation_id": "forged-observation"}),
                valid_effect.model_copy(
                    update={
                        "outcome": "UNKNOWN",
                        "command_digest_verified": False,
                        "command_digest_diagnostic": "digest-mismatch",
                        "physical_contact": None,
                        "command_executed": None,
                        "semantic_goal_attained": None,
                        "a_counter": None,
                        "b_counter": None,
                        "a_latched": None,
                        "b_latched": None,
                    }
                ),
            ):
                direct_mutation = harness.field.factory.make(
                    "m3a.spatial.effect",
                    "mission-1",
                    valid_effect_message.correlation_id,
                    mutation,
                    source_id="field-1",
                    causation_id=valid_effect_message.causation_id,
                )
                assert await harness._deliver(
                    harness.field_store,
                    harness.mission,
                    direct_mutation,
                )
                assert harness.mission.m3a_view() == valid_view

            duplicate = harness.field.factory.make(
                "m3a.spatial.effect",
                "mission-1",
                valid_effect_message.correlation_id,
                valid_effect,
                source_id="field-1",
                causation_id=valid_effect_message.causation_id,
                created_at=harness.clock.now(),
            )
            assert await harness._deliver(harness.field_store, harness.mission, duplicate)
            assert harness.mission.m3a_view() == valid_view
            assert len(harness.mission_store.inspect_m3a_effect_bindings()) == 1
            assert len(harness.mission_store.inspect_m3a_effect_conflicts()) >= 3

            canonical_context_message = next(
                message
                for message in harness.field_store.outbox_messages()
                if isinstance(message.payload, M3aSpatialExecutionContext)
                and message.destination_id == "mission-1"
            )
            contradictory_context = harness.field.factory.make(
                "m3a.spatial.context",
                "mission-1",
                canonical_context_message.correlation_id,
                canonical_context_message.payload.model_copy(
                    update={"expected_device_id": "other-device"}
                ),
                source_id="field-1",
                causation_id=canonical_context_message.message_id,
            )
            assert await harness._deliver(
                harness.field_store,
                harness.mission,
                contradictory_context,
            )
            assert harness.mission.m3a_view() == valid_view

            harness.mission_store.close()
            harness.mission_store = NodeStore(tmp_path / "mission.sqlite3")
            harness.mission = M3aMissionService(
                harness.mission_store,
                EnvelopeFactory("mission-1", harness.clock),
                configured_one_way_delay=1200.0,
            )
            assert harness.mission.m3a_view() == valid_view
            assert len(harness.mission_store.inspect_m3a_effect_bindings()) == 1
        finally:
            harness.close()

    _run(scenario())


def test_m3a_context_substitution_is_held_before_bind_and_budget(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = M3aServiceHarness(tmp_path, FixtureScenario.S0_NOMINAL)
        try:
            reference = harness.fixture.reference_observation(observed_at=NOW)
            harness.mission.submit_ensure_button_latched(
                reference,
            )
            assert await harness._route(harness.mission, harness.field, "field-1")
            harness.clock.advance(1200.0)
            await harness.record_local_current_observation()
            assert await harness._route(harness.mission, harness.field, "field-1")
            assignment_message = next(
                message
                for message in harness.field_store.outbox_messages()
                if message.message_type == "task.assignment"
            )
            contract_message = next(
                message
                for message in harness.field_store.outbox_messages()
                if isinstance(message.payload, ExecutionContract)
            )
            context_message = next(
                message
                for message in harness.field_store.outbox_messages()
                if isinstance(message.payload, M3aSpatialExecutionContext)
            )
            await harness._deliver(harness.field_store, harness.robot, assignment_message)
            await harness._deliver(harness.field_store, harness.robot, context_message)
            substituted = harness.field.factory.make(
                "m3a.spatial.context",
                "dummy-robot-1",
                context_message.correlation_id,
                context_message.payload.model_copy(update={"target_entity_id": BUTTON_B}),
                causation_id=context_message.message_id,
                created_at=harness.clock.now(),
            )
            await harness._deliver(harness.field_store, harness.robot, substituted)
            await harness._deliver(harness.field_store, harness.robot, contract_message)

            binding = harness.robot_store.find_m3a_context_binding(
                contract_message.payload.contract_id,
                contract_message.payload.contract_revision,
            )
            assert binding is not None
            assert len(harness.robot_store.inspect_m3a_context_conflicts()) == 1
            assert harness.robot_store.inspect_execution_journal() == []
            assert harness.robot_store.inspect_autonomy_budget() == []
            assert harness.robot_store.inspect_m3a_decisions()[0]["business_result"] == (
                "HELD_CONTEXT_PAYLOAD_MISMATCH"
            )
            decision = _m3a_messages(harness.robot_store, LocalTwoButtonDecision)[0].payload
            assert decision.action.value == "HOLD_CONTEXT_MISMATCH"
            assert harness.fixture.press_count == 0
            assert harness.fixture.binding_records == ()
        finally:
            harness.close()

    _run(scenario())


def test_m3a_decision_command_outbox_is_atomic_across_crash_reopen(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = M3aServiceHarness(tmp_path, FixtureScenario.S0_NOMINAL)
        try:
            reference = harness.fixture.reference_observation(observed_at=NOW)
            harness.mission.submit_ensure_button_latched(
                reference,
            )

            async def crash_before_external_dispatch(*_args: object, **_kwargs: object) -> None:
                raise RuntimeError("crash after decision transaction")

            harness.robot._process_external_contract = crash_before_external_dispatch  # type: ignore[method-assign]
            with pytest.raises(RuntimeError, match="decision transaction"):
                await harness.settle()
            contract_message = next(
                message
                for message in harness.field_store.outbox_messages()
                if isinstance(message.payload, ExecutionContract)
            )
            assert harness.fixture.press_count == 0
            assert len(harness.fixture.binding_records) == 1
            journal = harness.robot_store.find_execution_journal(
                contract_message.payload.contract_id,
                contract_message.payload.contract_revision,
            )
            assert journal is not None
            assert journal["state"] == ContractState.DISPATCH_RECORDED.value
            decision_row = harness.robot_store.inspect_m3a_decisions()[0]
            assert decision_row["command_envelope_json"] is not None
            command_messages = _m3a_messages(harness.robot_store, WireSpatialPressCommand)
            assert len(command_messages) == 1

            harness.robot_store.close()
            harness.robot_store = NodeStore(tmp_path / "robot.sqlite3")
            reopened_fixture = TwoButtonFixture(tmp_path / "device.jsonl", clock=harness.clock)
            harness.robot = M3aRobotService(
                harness.robot_store,
                EnvelopeFactory("dummy-robot-1", harness.clock),
                external_effect_adapter=SpatialExternalEffectAdapter(reopened_fixture),
                max_elapsed_seconds=60.0,
            )
            assert await harness.robot.recover() == 1
            # Reservation is the M1.8c dispatch boundary.  After a crash at
            # this frontier recovery observes the device and never presses a
            # second time when no physical pulse was recorded.
            assert reopened_fixture.press_count == 0
            assert harness.robot_store.find_execution_journal(
                contract_message.payload.contract_id,
                contract_message.payload.contract_revision,
            )["state"] == ContractState.HELD.value
        finally:
            harness.close()

    _run(scenario())


def test_m3a_budget_refusal_closes_accepted_journal_with_coherent_hold(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = M3aServiceHarness(tmp_path, FixtureScenario.S0_NOMINAL)
        try:
            reference = harness.fixture.reference_observation(observed_at=NOW)
            harness.mission.submit_ensure_button_latched(
                reference,
            )

            def reject_reservation(*_args: object, **_kwargs: object) -> bool:
                raise BudgetDeadlineError("BUDGET_DEADLINE_EXPIRED: injected oracle")

            harness.robot.store.reserve_external_dispatch_with_budget = reject_reservation  # type: ignore[method-assign]
            await harness.settle()
            contract_message = next(
                message
                for message in harness.field_store.outbox_messages()
                if isinstance(message.payload, ExecutionContract)
            )
            journal = harness.robot_store.find_execution_journal(
                contract_message.payload.contract_id,
                contract_message.payload.contract_revision,
            )
            assert journal is not None
            assert journal["state"] == ContractState.HELD.value
            assert journal["dispatch_recorded_at"] is None
            assert harness.robot_store.inspect_autonomy_budget()[0]["actions_reserved"] == 0
            assert harness.robot_store.inspect_autonomy_budget()[0]["resolution"] == (
                "HELD_BUDGET_RESERVATION_REJECTED"
            )
            held = [
                message.payload
                for message in harness.robot_store.outbox_messages()
                if isinstance(message.payload, ExecutionEvent)
                and message.payload.next_state is ContractState.HELD
            ]
            assert len(held) == 1
            assert held[0].previous_state is ContractState.ACCEPTED
            assert harness.fixture.press_count == 0
            assert len(harness.fixture.binding_records) == 1
        finally:
            harness.close()

    _run(scenario())


@pytest.mark.parametrize(
    ("scenario", "expected_action", "expected_contact", "expected_press"),
    [
        (
            FixtureScenario.S1_BOUNDARY,
            "REANCHOR_EXECUTE",
            BUTTON_A,
            1,
        ),
        (
            FixtureScenario.S1_EPSILON,
            "HOLD_AMBIGUOUS",
            None,
            0,
        ),
        (
            FixtureScenario.S2_SWAP,
            "HOLD_AMBIGUOUS",
            None,
            0,
        ),
    ],
)
def test_m3a_services_spatial_boundary_epsilon_and_swap_oracles(
    tmp_path: Path,
    scenario: FixtureScenario,
    expected_action: str,
    expected_contact: str | None,
    expected_press: int,
) -> None:
    async def run_scenario() -> None:
        harness = M3aServiceHarness(tmp_path, scenario)
        try:
            reference = harness.fixture.reference_observation(observed_at=NOW)
            harness.mission.submit_ensure_button_latched(
                reference,
                max_displacement_m=harness.fixture.max_displacement_m,
            )
            await harness.settle()
            decision = _m3a_messages(harness.robot_store, LocalTwoButtonDecision)[0].payload
            assert decision.action.value == expected_action
            assert harness.fixture.press_count == expected_press
            assert len(harness.robot_store.inspect_autonomy_budget()) == (
                1 if expected_press else 0
            )
            if expected_contact is not None:
                assert harness.fixture.records[0]["contact"] == expected_contact
            else:
                assert harness.robot_store.inspect_execution_journal() == []
                assert harness.fixture.binding_records == ()
        finally:
            harness.close()

    _run(run_scenario())


def test_m3a_services_already_latched_is_preacceptance_and_exact_replay(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = M3aServiceHarness(tmp_path, FixtureScenario.S4_ALREADY_LATCHED)
        try:
            reference = harness.fixture.reference_observation(observed_at=NOW)
            harness.mission.submit_ensure_button_latched(
                reference,
            )
            await harness.settle()
            decision = _m3a_messages(harness.robot_store, LocalTwoButtonDecision)[0].payload
            assert decision.action.value == "RECOGNIZE_EFFECT"
            assert decision.reason == "ALREADY_LATCHED"
            assert harness.fixture.press_count == 1  # unrelated seed impulse only
            assert harness.fixture.binding_records == ()
            assert harness.robot_store.inspect_execution_journal() == []
            assert harness.robot_store.inspect_autonomy_budget() == []
            held = [
                message.payload
                for message in harness.robot_store.outbox_messages()
                if isinstance(message.payload, ExecutionEvent)
                and message.payload.next_state is ContractState.HELD
            ]
            assert len(held) == 1
            assert held[0].previous_state is ContractState.RECEIVED
            view = harness.mission.m3a_view()
            assert view["effect_evidence"] is None
            assert view["physical_contact"] is None
            assert view["a_counter"] is None
            assert view["b_counter"] is None
            assert view["a_latched"] is None
            assert view["b_latched"] is None
            assert view["business_result"] == "RECOGNIZED_ALREADY_EFFECTIVE"
        finally:
            harness.close()

    _run(scenario())


def test_m3a_intent_duplicate_bytes_and_mutated_claim_are_durable_conflicts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = M3aServiceHarness(tmp_path, FixtureScenario.S0_NOMINAL)
        try:
            reference = harness.fixture.reference_observation(observed_at=NOW)
            intent_message = harness.mission.submit_ensure_button_latched(
                reference,
            )
            await harness._route(harness.mission, harness.field, "field-1")
            harness.clock.advance(1200.0)
            await harness.record_local_current_observation()
            await harness._route(harness.mission, harness.field, "field-1")
            before = {
                row["message_id"]: row["payload_json"]
                for row in harness.field_store.inspect_outbox()
            }
            duplicate = harness.field.factory.make(
                "m3a.ensure_latched.intent",
                "field-1",
                intent_message.correlation_id,
                intent_message.payload,
                source_id=intent_message.source_id,
                source_boot_id=intent_message.source_boot_id,
                created_at=harness.clock.now(),
            )
            assert await harness._deliver(harness.mission_store, harness.field, duplicate)
            after_duplicate = {
                row["message_id"]: row["payload_json"]
                for row in harness.field_store.inspect_outbox()
            }
            assert after_duplicate == before

            mutated_payload = intent_message.payload.model_copy(
                update={"target_entity_id": "B"}
            )
            mutated = harness.field.factory.make(
                "m3a.ensure_latched.intent",
                "field-1",
                intent_message.correlation_id,
                mutated_payload,
                source_id=intent_message.source_id,
                source_boot_id=intent_message.source_boot_id,
                created_at=harness.clock.now(),
            )
            assert await harness._deliver(harness.mission_store, harness.field, mutated)
            assert len(harness.field_store.inspect_m3a_intent_conflicts()) == 1
            assert {
                row["message_id"]: row["payload_json"]
                for row in harness.field_store.inspect_outbox()
            } == before
            harness.field_store.close()
            harness.field_store = NodeStore(tmp_path / "field.sqlite3")
            assert len(harness.field_store.inspect_m3a_intent_conflicts()) == 1
        finally:
            harness.close()

    _run(scenario())
