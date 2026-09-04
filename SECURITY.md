# Security policy

## Project status

Deferred Teleoperation is an experimental research prototype and is not certified for safety-critical operation. Hardware support is not yet part of a public release.

## Reporting a vulnerability

Please report security-sensitive findings privately to the repository owner through GitHub's private vulnerability reporting feature when available. Do not include live credentials, private captures, device addresses or exploit details in a public issue.

## Scope of security review

Security includes both software and cyber-physical consequences. Relevant findings include:

- command replay or duplicate physical execution;
- authorization bypass between Mission, Field and Robot roles;
- unsafe hardware enablement defaults;
- arbitrary code or model execution from untrusted messages;
- protocol downgrade or schema-confusion attacks;
- forged world revisions, target bindings or execution results;
- storage exhaustion through delayed-message or blob queues;
- exposure of camera data, maps, calibration or robot telemetry;
- denial of service that could leave a robot in an unsafe state.

The initial trust model and known gaps are documented in `docs/security/THREAT_MODEL.md`.
