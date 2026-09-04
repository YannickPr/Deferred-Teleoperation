# Time, coordinate frames and provenance

## Units and canonical robot frame

Core robot and protocol quantities use SI units:

- metres;
- kilograms;
- seconds;
- radians;
- newtons;
- newton-metres.

The initial canonical robot/world convention is right-handed, Z-up:

```text
+X forward
+Y left
+Z up
```

Unreal units, handedness and transforms are converted only at the Unreal boundary. Conversion code must be explicit and covered by numerical tests before robot geometry is introduced.

Every spatial datum identifies its `frame_id` and, when applicable, the calibration or transform-tree version used to express it.

## Time

Different meanings of time remain separate:

- `observed_at`: when a sensor or system state was valid at the source;
- `produced_at`: when an estimate or message was generated;
- `created_at`: transport-envelope creation time;
- `predicted_for`: future time represented by a forecast;
- `not_before`: earliest admissible application time;
- `expires_at`: time after which an intent or binding must not be newly accepted;
- local monotonic time: durations, control and ordering within one process boot.

Wall-clock timestamps support cross-system audit but are never assumed perfectly synchronized. Future messages will carry clock-source and uncertainty metadata where timing precision matters.

## Evidence provenance

Operational data must distinguish:

```text
MEASURED
FUSED
OPERATOR_ASSERTED
INFERRED
PREDICTED
SIMULATED
```

Suggested metadata:

```text
source_ids
observed_at
produced_at
frame_id
calibration_version
model_version
world_revision
freshness
uncertainty
provenance
```

`confidence` is not automatically a calibrated probability. Until calibration exists, expose observable contributors such as data age, covariance, residual, source count, horizon and model identity.

## Operator-side representations

- **Confirmed State:** the last Field estimate actually received by Mission.
- **Arrival Belief:** projection of the Field state at the estimated arrival time of a newly sent intent.
- **Target Branch:** conditional state after the proposed operation is accepted and succeeds.

The three representations must never overwrite one another in storage or visualization.
