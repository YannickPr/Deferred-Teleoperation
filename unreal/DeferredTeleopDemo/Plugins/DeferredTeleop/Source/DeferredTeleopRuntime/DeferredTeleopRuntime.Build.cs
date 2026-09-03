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
    }
}
