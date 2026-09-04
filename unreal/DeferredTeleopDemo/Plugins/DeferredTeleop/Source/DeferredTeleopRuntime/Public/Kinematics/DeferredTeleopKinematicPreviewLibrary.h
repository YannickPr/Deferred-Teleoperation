#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "Kinematics/DeferredTeleopKinematicPreviewTypes.h"
#include "DeferredTeleopKinematicPreviewLibrary.generated.h"

namespace DeferredTeleop::Kinematics
{
/** Build a bounded, local, FK-backed joint-space kinematic preview. */
DEFERREDTELEOPRUNTIME_API bool BuildPreview(
    const FDttRobotDescription& Description,
    const FDttKinematicPreviewRequest& Request,
    FDttKinematicPreview& OutPreview,
    FString& OutError);
} // namespace DeferredTeleop::Kinematics

/** Blueprint boundary for the pure, deterministic kinematic preview builder. */
UCLASS()
class DEFERREDTELEOPRUNTIME_API UDeferredTeleopKinematicPreviewLibrary final
    : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Kinematics|Preview")
    static bool BuildPreview(
        const FDttRobotDescription& Description,
        const FDttKinematicPreviewRequest& Request,
        FDttKinematicPreview& OutPreview,
        FString& OutError);
};
