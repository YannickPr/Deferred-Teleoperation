#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "Kinematics/DeferredTeleopIKTypes.h"
#include "DeferredTeleopIKLibrary.generated.h"

namespace DeferredTeleop::Kinematics
{
/**
 * Solve one bounded position or position-plus-approach-axis task.
 *
 * The solver consumes canonical metres/radians and calls the existing generic
 * FK implementation for every counted state evaluation.  It never commands
 * an actor or hardware and it does not claim a global workspace proof.
 */
DEFERREDTELEOPRUNTIME_API bool SolveInverseKinematics(
    const FDttRobotDescription& Description,
    const FDttIKRequest& Request,
    const FDttIKSettings& Settings,
    FDttIKResult& OutResult);
} // namespace DeferredTeleop::Kinematics

/** Blueprint boundary for the bounded canonical IK solver. */
UCLASS()
class DEFERREDTELEOPRUNTIME_API UDeferredTeleopIKLibrary final
    : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Kinematics|IK")
    static bool SolveInverseKinematics(
        const FDttRobotDescription& Description,
        const FDttIKRequest& Request,
        const FDttIKSettings& Settings,
        FDttIKResult& OutResult);
};

