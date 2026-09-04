#pragma once

#include "Kismet/BlueprintFunctionLibrary.h"
#include "DeferredTeleopMissionViewTypes.h"
#include "DeferredTeleopVisualizationLibrary.generated.h"

UCLASS()
class DEFERREDTELEOPRUNTIME_API UDeferredTeleopVisualizationLibrary final
    : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintPure, Category = "Deferred Teleoperation|Visualization")
    static FTransform MissionPoseToUnrealTransform(const FDeferredTeleopPose& MissionPose);
};
