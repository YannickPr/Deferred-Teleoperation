import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "protocol" / "v0" / "schemas" / "message-envelope.schema.json"
VALID_FIXTURE = ROOT / "protocol" / "v0" / "fixtures" / "valid" / "message-envelope.json"
INVALID_FIXTURE = (
    ROOT / "protocol" / "v0" / "fixtures" / "invalid" / "missing-message-id.json"
)


def _load(path: Path) -> object:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _validator() -> Draft202012Validator:
    return Draft202012Validator(_load(SCHEMA_PATH), format_checker=FormatChecker())


def test_valid_message_envelope_fixture() -> None:
    _validator().validate(_load(VALID_FIXTURE))


def test_invalid_message_envelope_fixture() -> None:
    with pytest.raises(ValidationError):
        _validator().validate(_load(INVALID_FIXTURE))
