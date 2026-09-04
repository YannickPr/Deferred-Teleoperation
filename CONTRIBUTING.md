# Contributing

Deferred Teleoperation is an early research project. Contributions that improve clarity, reproducibility, tests, failure handling, VR interaction or robot interoperability are welcome.

## Before opening a pull request

1. Open or link an issue that states the problem and acceptance criteria.
2. Keep one vertical concern per branch.
3. Preserve the authority boundaries between Robot, Field and Mission.
4. Keep hardware disabled by default.
5. Do not add robot meshes, captures, datasets, models or copied code without documenting provenance and licence compatibility.
6. Mark speculative architecture as planned or experimental rather than implemented.

## Development

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
pytest
ruff check .
```

Unreal changes require a local Unreal Engine 5.8 build verification. GitHub CI does not currently compile Unreal. Record the exact engine version, platform, regeneration/build steps and result in the pull request.

## Pull-request expectations

A pull request should include:

- the linked issue;
- the problem and chosen approach;
- public API or protocol changes;
- tests or a precise manual protocol;
- visible evidence for Unreal/VR/hardware changes;
- limitations and untested assumptions;
- whether physical hardware was used.

AI-assisted code is allowed, but the contributor remains responsible for understanding the diff, verifying licences, running tests and validating safety assumptions.

## Hardware contributions

Never assume a network stop command is an emergency stop. Hardware tests must begin at conservative speed and workspace limits with an independent local stop mechanism available. Public examples must use the dummy backend unless the operator explicitly selects a hardware configuration.
