import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from deferred_teleop.protocol import MessageEnvelope
from deferred_teleop.schema import SCHEMA_PATH, rendered_schema
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
CHAIN_PATH = ROOT / "protocol" / "v0" / "fixtures" / "valid" / "dummy-operation-chain.json"
INVALID_PATH = ROOT / "protocol" / "v0" / "fixtures" / "invalid" / "m1-cases.json"


def _load(path: Path) -> object:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def test_complete_dummy_chain_round_trips_and_preserves_cross_references() -> None:
    raw_messages = _load(CHAIN_PATH)["messages"]
    messages = [
        MessageEnvelope.model_validate_json(json.dumps(message)) for message in raw_messages
    ]

    assert [message.message_type for message in messages] == [
        "operation.intent",
        "operation.grounded",
        "operation.plan",
        "task.assignment",
        "execution.contract",
        "execution.event",
    ]
    assert all(message.correlation_id == messages[0].correlation_id for message in messages)
    assert all(
        message.causation_id == messages[index - 1].message_id
        for index, message in enumerate(messages[1:], start=1)
    )
    operation_id = UUID("30000000-0000-4000-8000-000000000001")
    assert messages[0].payload.operation_id == operation_id
    assert messages[4].payload.operation_id == operation_id
    assert messages[2].payload.plan_id == messages[3].payload.plan_id
    assert messages[2].payload.tasks[0].task_id == messages[3].payload.task_id
    assert messages[3].payload.assignment_id == messages[4].payload.assignment_id
    assert messages[4].payload.contract_id == messages[5].payload.contract_id
    assert messages[4].payload.contract_revision == messages[5].payload.contract_revision

    encoded = [message.model_dump_json() for message in messages]
    assert [MessageEnvelope.model_validate_json(item) for item in encoded] == messages


@pytest.mark.parametrize("case", ["unknown_field", "missing_causation"])
def test_committed_invalid_envelopes_are_rejected(case: str) -> None:
    with pytest.raises(ValidationError):
        MessageEnvelope.model_validate_json(json.dumps(_load(INVALID_PATH)[case]))


@pytest.mark.parametrize(
    "case",
    ["invalid_spatial_metadata", "invalid_quaternion", "illegal_contract_transition"],
)
def test_committed_invalid_mutation_fixtures_are_rejected(case: str) -> None:
    specification = _load(INVALID_PATH)[case]
    message = deepcopy(_load(CHAIN_PATH)["messages"][specification["source_message_index"]])
    for dotted_path, value in specification["replace"].items():
        target = message
        path = dotted_path.split(".")
        for component in path[:-1]:
            target = target[component]
        target[path[-1]] = value
    with pytest.raises(ValidationError):
        MessageEnvelope.model_validate_json(json.dumps(message))


def test_committed_schema_has_no_drift() -> None:
    assert SCHEMA_PATH.read_text(encoding="utf-8") == rendered_schema()
