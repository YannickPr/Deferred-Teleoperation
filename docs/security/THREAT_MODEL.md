# Initial threat model

Status: M0 design baseline, not a security certification.

## Assets

- physical safety of people, robots and the environment;
- integrity and ordering of operation, assignment and contract decisions;
- confidentiality of camera data, maps, calibration and telemetry;
- availability of Robot and Field safety functions;
- auditability of delayed or autonomous effects;
- integrity of model, configuration and protocol versions.

## Trust boundaries

```text
Unreal VR Client <-> Mission Server
Mission Server <-> persistent inter-site relay <-> Field Server
Field Server <-> Robot Runtime
Field/Mission <-> optional Simulation or ML workers
services <-> persistent stores and blob caches
```

A syntactically valid message, simulation result, LLM proposal or operator annotation is not automatically trusted for physical execution.

## Representative threats and failures

- replayed or duplicated operation causes duplicate physical effect;
- stale operation is accepted after the world or target binding changed;
- compromised Mission client requests an unauthorized skill;
- compromised planner/LLM fabricates a target, capability or invariant;
- forged execution result hides a failure;
- protocol downgrade or ambiguous message interpretation;
- storage exhaustion in persistent inbox/outbox or blob caches;
- clock error causes premature expiry or unsafe ordering assumptions;
- denial of service removes Field coordination;
- Robot/Field restart occurs between dispatch and durable acknowledgement;
- private visual or geometric data is published or transferred unnecessarily.

## M0 controls and design rules

- no public hardware path and hardware disabled by default;
- experimental, explicit protocol version;
- strict schema fixtures and rejection of unknown fields at trust boundaries where appropriate;
- globally unique message IDs plus future durable execution journals;
- expiry, supersession, causation and correlation are application concepts, not transport assumptions;
- Simulation Workers and learned models have no direct actuator authority;
- deterministic capability, invariant and approval checks surround future planner output;
- local Robot safety must not require Mission, Field, Unreal, an LLM or an active WAN link;
- code never accepts arbitrary executable payloads from protocol messages;
- queues and caches require configurable quotas and retention policies;
- security-sensitive details are reported privately.

## Known open decisions

M0 does not yet select production identity, authentication, key management, message signing, encryption-at-rest or relay technology. These must be designed before any network-accessible hardware deployment.

The delayed-link design should eventually protect stored commands as well as transport sessions: an operation may remain at rest in relays long after its original connection ended.
