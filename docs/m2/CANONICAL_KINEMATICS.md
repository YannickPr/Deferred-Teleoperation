# Canonical kinematics in Unreal

M2.2 covers the articulated robot-state and model-reference protocol (#14).
M2.3 implements a small, robot-independent C++ kinematics core in
`DeferredTeleopRuntime` (#15). It consumes the generated
`dtt.robot-description/0` JSON representation; it does not parse arbitrary
URDF at runtime and it does not command hardware. The implementation is
present and validated on Linux and Win64 with Unreal Engine 5.8.2. The
cross-language numerical oracle remains M2.4 work; this does not claim an FK
golden result.

## Contract

The core uses one explicit convention until the Unreal presentation boundary:

```text
right-handed, Z-up
metres and radians
quaternion XYZW
^A T_C = ^A T_B * ^B T_C
double precision
```

Robot descriptions contain named links, fixed or revolute joints, semantic
joint groups, and tool frames. Validation rejects malformed transforms,
non-unit axes, duplicate names, invalid references, multiple parents, cycles,
disconnected links, and invalid group membership.

The JSON reader is schema-scoped rather than a global strict validator. It
checks the exact fields and values consumed by the kinematics model. Source
metadata is checked structurally for traceability, and visual entries are only
checked to be JSON objects; neither is retained in the kinematics model or used
by FK. Visual parsing, mesh semantics, source provenance enforcement, and URDF
import remain outside this increment.

Forward kinematics accepts one finite named position for every revolute joint.
It traverses the validated tree in a deterministic order and returns named
world transforms for every link and tool. Position-limit violations are
reported without silently clamping the requested state.

## Unreal boundary

Canonical transforms cross into Unreal through one conversion function. The
basis change is `S = diag(1, -1, 1)`, translation is converted from metres to
centimetres exactly once, and scale remains `(1, 1, 1)`. The reverse function
exists for authoring and round-trip tests. Negative Actor scale is not used to
repair handedness.

Blueprint callers can parse a canonical JSON string, validate a description,
evaluate FK, and convert transforms through
`UDeferredTeleopKinematicsLibrary`. File selection and asset packaging stay
outside the mathematical core.

## Verification

The [platform validation summary](evidence/fk-platform-validation.json) records
the executed Linux and Win64 results, toolchains, test names and source-file
hashes for this increment. It is a historical validation snapshot.

Unreal Automation Tests under `DeferredTeleop.M2` cover:

- rigid-transform composition;
- generic branched fixed/revolute FK and tool propagation;
- invalid tree, axis, transform, group, and joint-state inputs;
- explicit limit diagnostics without clamping;
- canonical JSON parsing;
- the committed generated SO-101 description, including its model identity,
  link/joint/group/tool names and cardinalities (structure only, not FK values);
- signed quarter-turn basis cases and canonical/Unreal round trips.

The generated-description check is an integration guard for the structural
model input consumed by M2.3. It is not the articulated-state protocol work in
M2.2 (#14) or the numerical cross-language FK oracle planned for M2.4 (#16), and
no SO-101 FK golden claim is made here.

The M2.3 verification recipe targets Unreal Engine 5.8.2 on Linux and Win64.
It builds the existing project and runtime module from an intact monorepo
checkout, then runs the Automation queue with `SoftQuit`:

```bash
ENGINE_ROOT='<unreal-engine-root>'
REPO_ROOT='<deferred-teleoperation-checkout>'
PROJECT="$REPO_ROOT/unreal/DeferredTeleopDemo/DeferredTeleopDemo.uproject"
PLUGIN="$REPO_ROOT/unreal/DeferredTeleopDemo/Plugins/DeferredTeleop/DeferredTeleop.uplugin"
REPORT_DIR='<writable-automation-report-directory>'

"$ENGINE_ROOT/Engine/Build/BatchFiles/Linux/Build.sh" UnrealEditor Linux Development \
  -Project="$PROJECT" -Plugin="$PLUGIN" -NoUBTMakefiles -NoHotReload \
  -MaxParallelActions=4

"$ENGINE_ROOT/Engine/Binaries/Linux/UnrealEditor-Cmd" "$PROJECT" \
  -unattended -nop4 -nosplash -NullRHI \
  -ExecCmds="Automation RunTests DeferredTeleop.; SoftQuit" \
  -ReportExportPath="$REPORT_DIR" -log
```

The equivalent Win64 commands are:

```powershell
$EngineRoot = '<unreal-engine-root>'
$RepoRoot = '<deferred-teleoperation-checkout>'
$Project = "$RepoRoot\unreal\DeferredTeleopDemo\DeferredTeleopDemo.uproject"
$Plugin = "$RepoRoot\unreal\DeferredTeleopDemo\Plugins\DeferredTeleop\DeferredTeleop.uplugin"
$ReportDir = '<writable-automation-report-directory>'

& "$EngineRoot\Engine\Build\BatchFiles\Build.bat" UnrealEditor Win64 Development `
  "-Project=$Project" "-Plugin=$Plugin" -NoUBTMakefiles -NoHotReload `
  -MaxParallelActions=4

$EditorCmd = Join-Path $EngineRoot 'Engine\Binaries\Win64\UnrealEditor-Cmd.exe'
$StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
$StartInfo.FileName = $EditorCmd
$StartInfo.Arguments = @(
  "`"$Project`""
  '-unattended'
  '-nop4'
  '-nosplash'
  '-NullRHI'
  '-ExecCmds="Automation RunTests DeferredTeleop.; SoftQuit"'
  "-ReportExportPath=`"$ReportDir`""
  '-log'
) -join ' '
$StartInfo.UseShellExecute = $false
$StartInfo.WorkingDirectory = Split-Path -Parent $Project
$Process = [System.Diagnostics.Process]::Start($StartInfo)
$Process.WaitForExit()
$ExitCode = $Process.ExitCode
$Process.Dispose()
if ($ExitCode -ne 0) {
  throw "UnrealEditor-Cmd failed with exit code $ExitCode"
}
```

The explicit `ProcessStartInfo.Arguments` string is intentional. A direct
PowerShell native invocation can pass the `-ExecCmds` token with whole-argument
quoting; Unreal's command-line parser then stops the value at its first space.
The explicit form keeps the quotes around the complete command value and makes
the editor exit status observable. This quoting procedure is covered by the
recorded Windows run below.

Do not add a queue-empty forced-exit flag to this recipe: on the Linux run it
forced exit code 1 after the Automation tests had succeeded. `SoftQuit` lets
the report complete and produced editor exit code 0.

The tests require the monorepo checkout: the M2 integration test loads
`robots/so101/generated/so101.kinematics.json` relative to `ProjectDir()/../../`,
and the M1 tests load `fixtures/m1/golden-session` the same way. A packaged
plugin without these repository files is insufficient.

Recorded evidence on 4 September 2026 covers both targets. Linux used Unreal
Engine 5.8.2 source commit `ff8421f2b8cb4feb76fff57965a1effc53a6eb7b` with
Clang 20.1.8; the project/module build exited 0 and the headless editor
reported 11 successful Automation tests (4 M1 and 7 M2) before exiting 0 with
`SoftQuit`. Win64 UE 5.8.2 likewise reported 11 successful Automation tests
(4 M1 and 7 M2), with build and editor exit code 0. These runs validate the
M2.3 math core on both targets and support closing #15; they do not implement
the M2.4 numerical oracle or add an SO-101 FK golden claim.

These tests establish rigid-transform and FK behaviour only. Collision,
dynamics, device calibration, motion planning, safety validation, articulated
rendering, Jacobians, and IK remain outside this increment.
