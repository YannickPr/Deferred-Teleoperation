# ADR 0002: Python wire models and JSON Schema artefacts for `dtt/0`

- Status: Proposed for M1
- Date: 2026-09-03
- Scope: experimental `dtt/0` protocol only
- Issue: #5

## Context

The first runnable delayed-dummy slice needs strict typed messages in Python, reviewable language-neutral interoperability artefacts, and a path to Unreal/C++ consumption.

The project must avoid two opposite failure modes:

1. passing arbitrary dictionaries between Mission, Field and Robot until incompatible assumptions spread through the code;
2. freezing a large supposedly final protocol before one physical vertical slice has exercised it.

The existing M0 envelope is a committed Draft 2020-12 JSON Schema. M1 adds the constrained semantic spine:

```text
OperationIntent
-> GroundedOperation
-> OperationPlan / TaskGraph
-> TaskAssignment
-> ExecutionContract
-> ExecutionEvent
```

## Decision

For `dtt/0`:

1. Python wire DTOs use **Pydantic v2** with strict validation and `extra="forbid"`.
2. Domain services do not exchange unvalidated arbitrary dictionaries.
3. Conversion between wire DTOs and future internal/domain representations remains explicit.
4. Draft 2020-12 JSON Schemas are generated from the Python wire DTOs and committed as interoperability artefacts.
5. CI regenerates the schemas and fails when committed artefacts drift from the models.
6. The first conformance bundle covers only the constrained delayed dummy/button path.
7. Spatial values use SI units and the canonical right-handed frame convention.
8. Evidence-bearing values retain source, time, frame, revision and provenance metadata.
9. Predicted or simulated data cannot be relabelled as measured by serialization or visualization code.
10. Message and model names remain `dtt/0` and explicitly experimental; breaking changes are allowed before the physical delayed-button slice is complete.

## Envelope consequences

The M1 envelope adds the fields needed for durable retry and causal audit:

```text
message_id
message_type
source_id
source_boot_id
source_sequence / sequence_id
destination_id
correlation_id
causation_id when the message is a direct consequence
created_at
not_before
expires_at
payload
```

Causation is not mandatory for genuine roots such as an operator-created `OperationIntent` or periodic telemetry. It is mandatory for direct consequences such as a plan, assignment, contract or execution event.

## State-machine consequences

Contract transitions are validated separately from syntactic message validation. The initial M1 path is:

```text
RECEIVED
-> ACCEPTED
-> DISPATCH_RECORDED
-> RUNNING
-> SUCCEEDED | FAILED | HELD | CANCELLED
```

A stable `(contract_id, contract_revision)` is terminal once a terminal state is durably recorded. Retry or rebase requires an explicit new revision.

The schema does not itself guarantee effect-once execution. That guarantee belongs to the durable execution journal in #6.

## Alternatives considered

### Hand-written JSON Schema plus hand-written Python models

Rejected for M1 because the two representations would drift quickly. It may be reconsidered if a stable multi-language protocol later requires a schema-first workflow.

### Protobuf immediately

Deferred. It offers mature code generation and evolution rules, but adds Unreal/C++ build integration and makes the first experimental payloads less inspectable. A later stable version may adopt Protobuf or another IDL behind the same domain boundaries.

### Python dataclasses with ad-hoc validation

Rejected. They provide insufficient strict wire validation without rebuilding a validation framework.

### Pydantic models as permanent protocol authority

Not decided. This ADR applies only to `dtt/0`. Before a stable `dtt/1`, the project must review interoperability, generated C++ ergonomics, binary transport needs and compatibility guarantees.

## Consequences

Positive:

- readable and strongly validated Python code;
- generated schemas for non-Python clients and conformance tests;
- quick iteration while the protocol remains experimental;
- explicit rejection of unknown fields and illegal states;
- recognizable tooling for external contributors.

Costs and risks:

- Pydantic becomes a runtime dependency for the Python path;
- generated schemas may change with tool versions, so the dependency range and output must be controlled;
- Unreal still needs explicit DTO parsing or later code generation;
- large generated schemas should not hide whether the exercised fixture is actually understandable.

## Validation gate

This ADR is accepted when #5 demonstrates:

- one strict complete dummy-operation fixture;
- generated-schema drift detection in CI;
- typed envelope payload parsing;
- illegal transition and provenance failures;
- no transport, Unreal Actor or SO-101 dependency in the protocol package.
