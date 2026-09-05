using UnrealBuildTool;

public class DeferredTeleopRuntime : ModuleRules
{
    public DeferredTeleopRuntime(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(
            new[]
            {
                "Core",
                "CoreUObject",
                "Engine",
            }
        );

        PrivateDependencyModuleNames.AddRange(
            new[]
            {
                "Json",
                "WebSockets",
            }
        );

        if (Target.Platform == UnrealTargetPlatform.Linux
            || Target.Platform == UnrealTargetPlatform.Win64)
        {
            AddEngineThirdPartyPrivateStaticDependencies(Target, "OpenSSL");
        }
    }
}
