"""Generate or verify committed JSON Schema interoperability artefacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from deferred_teleop.protocol import MessageEnvelope

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "protocol" / "v0" / "schemas" / "message-envelope.schema.json"


def rendered_schema() -> str:
    schema = MessageEnvelope.model_json_schema(mode="validation")
    schema["$id"] = (
        "https://github.com/YannickPr/Deferred-Teleoperation/"
        "protocol/v0/schemas/message-envelope.schema.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = rendered_schema()
    if args.check:
        if not SCHEMA_PATH.exists() or SCHEMA_PATH.read_text(encoding="utf-8") != expected:
            print(f"schema drift detected: regenerate {SCHEMA_PATH}")
            return 1
        return 0
    SCHEMA_PATH.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
