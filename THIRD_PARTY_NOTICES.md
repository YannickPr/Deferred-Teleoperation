# Third-party notices

No third-party robot meshes, textures, datasets, trained weights or copied source files are included in M0.

Before adding third-party material, a pull request must record:

- source and immutable version or commit;
- copyright holder when known;
- licence and redistribution conditions;
- local modifications;
- required notices;
- whether the material may be bundled, downloaded separately, or used only as a development dependency.

The Apache-2.0 licence applies to original repository code unless a file or directory explicitly states otherwise. It does not relicense third-party material.

## Direct Python runtime dependencies

These packages are installed separately from PyPI and are not vendored or modified in this
repository:

| Package | Accepted versions | Version used for local validation | Source | Licence |
|---|---:|---:|---|---|
| Pydantic | `>=2.11,<3` | 2.13.5 | <https://github.com/pydantic/pydantic> | MIT |
| websockets | `>=15,<17` | 16.1.1 | <https://github.com/python-websockets/websockets> | BSD-3-Clause |

Their distributions carry the applicable copyright and licence texts. No robot assets,
datasets, model weights or copied third-party source files are introduced by M1.3.
