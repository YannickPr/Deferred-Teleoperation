# Unreal Engine bootstrap

The M0 host project is `unreal/DeferredTeleopDemo` and contains one project plugin with one compiled runtime module:

```text
DeferredTeleopDemo/
└── Plugins/DeferredTeleop/
    └── Source/DeferredTeleopRuntime/
```

The plugin intentionally exposes only a Blueprint-pure protocol-version function and a startup log. It does not contain networking, kinematics, robot assets or hardware control.

## Required local verification

These files have not been compiled by GitHub CI. Before M0 closes:

1. install or select Unreal Engine 5.8;
2. regenerate project files for `DeferredTeleopDemo.uproject`;
3. build the editor target on the primary development platform;
4. open the project and verify that `DeferredTeleop` is enabled;
5. observe `DeferredTeleopRuntime dtt/0 loaded` in the log;
6. call `Get Deferred Teleop Protocol Version` in a test Blueprint and confirm `dtt/0`;
7. record the exact engine build, platform, commands and result in pull request #1.

No Unreal build claim should be added to the README or CI badge before this manual verification succeeds.
