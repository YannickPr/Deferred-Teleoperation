# M2 articulated-state fixtures

These small deterministic records exercise the `robot.articulated_state` payload, the
description-backed SO-101 validator, and the separate `mission.articulated_view_state` frame.

`valid-articulated-state.json` and `reordered-articulated-state.json` carry the same six
structural positions in different JSON orders. `valid-articulated-view.json` demonstrates the
three layer contract and provenance rules. `invalid-articulated-view-duplicate-joint.json`
keeps the same outer view shape while making the confirmed state invalid for the Unreal
last-valid-state parser test. `invalid-articulated-view-nonunit-quaternion.json` exercises the
canonical quaternion tolerance in that parser. `live-articulated-view.json` is the current live
shape: confirmed may be populated while arrival and target are explicit `null`.

The invalid state records cover duplicate, unknown, fixed, and missing names, non-finite values,
model identity mismatch, and a normalized gripper value of `100`. The generated SO-101
description hash is embedded so tests detect accidental description drift. The `100` fixture
demonstrates a structural radian limit failure; the wire number itself does not establish a
device-unit conversion.
