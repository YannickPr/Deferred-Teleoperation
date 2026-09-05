# Exact JSON field names

UE 5.8.2 accepts case-only aliases and collapses their collisions while building
`FJsonObject`. Checking its field count and `HasField` after deserialization
therefore cannot enforce exact wire field names. The shared native tests
reproduce this gap in [issue #47](https://github.com/YannickPr/Deferred-Teleoperation/issues/47).

The correction has two parts. A shared preflight uses Unreal's JSON token
reader to reject differently spelled names that compare equal without case
inside one object, before the DOM can collapse them. The three strict field
helpers then compare the spelling of each stored key with the expected names.
This does not introduce another JSON parser or duplicate the protocol schema.

Case-only aliases of required fields are rejected. Collisions such as `foo`
and `FOO` are rejected in either order, including in otherwise opaque extension
objects. A unique uppercase extension key is allowed wherever the existing
parser allows free-form data. Duplicate keys with identical spelling retain
the existing last-wins behavior; producers should emit each field once.

The shared [mutation matrix](../../fixtures/m2/json-field-exactness.json) exercises
raw JSON in the legacy Mission view, articulated Mission view, and Unreal
robot-description parser. Each case replaces a unique fragment of a valid
fixture before parsing, so neither spelling nor duplicate keys are normalized
away by a dictionary round trip. Rejection preserves the previous parsed value.

The two Mission-view formats have Python wire models and run the same cases
through Pydantic and Unreal. Robot-description cases additionally exercise the
Unreal importer; the Python numerical reference loader is not a strict wire
parser and is not claimed as a differential counterpart. This is a bounded
conformance check, not a claim that every JSON input has identical treatment.

Run the portable cases with:

```sh
PYTHONPATH=python/src python -m pytest -q tests/test_json_field_exactness.py
```

Build the plugin and run `Automation RunTests DeferredTeleop.; SoftQuit` with
UE 5.8.2 to include the native matrix and existing last-good view tests. The
[platform record](evidence/json-field-platform-validation.json) records the
source and report hashes, including the failing regression baseline and the
corrected Linux and Win64 checks.

The corrected runs each execute 52 tests: 50 successes and two expected warning
cases (missing model and duplicate sequence), with zero failures or unexecuted
tests. Both JSON test groups pass without warnings. The unchanged 21-case
matrix produced 54 failed assertions before the correction on both platforms.
Validate report counters and registered test names as well as build/editor exit
codes; the Windows failing baseline returned editor exit code zero.
