# Unreal Engine bootstrap

The M0 host project is `unreal/DeferredTeleopDemo` and contains one project plugin with one compiled runtime module:

```text
DeferredTeleopDemo/
└── Plugins/DeferredTeleop/
    └── Source/DeferredTeleopRuntime/
```

The plugin intentionally exposes only a Blueprint-pure protocol-version function and a startup log. It does not contain networking, kinematics, robot assets or hardware control.

## Local verification

These files are not compiled by GitHub CI. The M0 skeleton was verified on 4 September 2026
with Unreal Engine 5.8.2 (build 56702186), Windows 11 25H2, Visual Studio 2022 toolchain
14.44.35228 and Windows SDK 10.0.26100.0:

1. associated `DeferredTeleopDemo.uproject` with UE 5.8;
2. accepted the editor's automatic regeneration and rebuild;
3. built the Development Editor target for Win64;
4. opened the project and verified that `DeferredTeleop` was enabled;
5. observed `DeferredTeleopRuntime dtt/0 loaded` in the log;
6. called `Get Deferred Teleop Protocol Version` in a test Blueprint and confirmed `dtt/0`.

The full result and visible evidence are recorded on pull request #2. No hardware was used and
no hardware-control path was enabled.
