# Third-party notices

The Apache-2.0 licence at the repository root applies to original Deferred Teleoperation code
unless a file or directory explicitly states otherwise. It does not relicense third-party
material.

## TheRobotStudio SO-ARM100 / SO-101 structural description

This repository vendors one unmodified source file for deterministic development and tests:

```text
Project:     SO-ARM100
Source:      https://github.com/TheRobotStudio/SO-ARM100
Commit:      385e8d7c68e24945df6c60d9bd68837a4b7411ae
File:        Simulation/SO101/so101_new_calib.urdf
Git blob:    9552a231d8b23bed68ec15779eba620c5d875ec4
Local path:  robots/so101/upstream/so101_new_calib.urdf
Licence:     Apache License 2.0
Modified:    no
```

The source file states that it was generated with `onshape-to-robot` from the linked Onshape CAD
model. Deferred Teleoperation preserves that source comment in the vendored file.

Any generated canonical description is a source-derived representation and must retain this
provenance. Official mesh files are **not** included by this notice; each exact mesh and any
converted Unreal asset must be reviewed and recorded before it is added.

## Policy for future third-party material

Before adding a robot mesh, texture, dataset, trained weight or copied source file, a pull request
must record:

- source and immutable version or commit;
- copyright holder when known;
- licence and redistribution conditions;
- local modifications;
- required notices;
- whether the material may be bundled, downloaded separately, or used only as a development
  dependency.

The Apache-2.0 licence applies to original repository code unless a file or directory explicitly
states otherwise. It does not relicense third-party material.

## Direct Python runtime dependencies

These packages are installed separately from PyPI and are not vendored or modified in this
repository:

| Package | Accepted versions | Version used for local validation | Source | Licence |
|---|---:|---:|---|---|
| Pydantic | `>=2.11,<3` | 2.13.5 | <https://github.com/pydantic/pydantic> | MIT |
| websockets | `>=15,<17` | 16.1.1 | <https://github.com/python-websockets/websockets> | BSD-3-Clause |

Their distributions carry the applicable copyright and licence texts. No robot assets,
datasets or model weights were introduced by M1. The pinned SO-101 URDF described above is the
first robot source vendored for M2.
