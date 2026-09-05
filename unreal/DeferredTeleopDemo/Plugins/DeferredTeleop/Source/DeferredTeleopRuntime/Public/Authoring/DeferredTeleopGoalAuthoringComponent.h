#pragma once

#include "Components/ActorComponent.h"
#include "Articulated/DeferredTeleopArticulatedSceneTypes.h"
#include "Kinematics/DeferredTeleopKinematicPreviewTypes.h"
#include "DeferredTeleopGoalAuthoringComponent.generated.h"

/** Local presentation settings, not robot controller or motor limits. */
USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttGoalAuthoringSettings
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Authoring")
    FName JointGroupName = TEXT("arm");

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Authoring")
    FName ToolFrameName = TEXT("gripper_frame_link");

    /** Rotate this tool-local axis by the target orientation to obtain the desired site direction. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Authoring")
    FDttCanonicalVector LocalToolApproachAxis;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Authoring")
    FDttIKSettings IK;

    /** JointVelocities must be supplied explicitly, including inactive joints. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Authoring")
    FDttKinematicPreviewSettings Preview;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Authoring")
    double MaximumSolveRateHz = 20.0;

    FDttGoalAuthoringSettings() { LocalToolApproachAxis.Z = 1.0; }
};

DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(
    FDttAuthoredPreviewChanged, const FDttKinematicPreview&, Preview);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(
    FDttAuthoringDiagnosticChanged, const FString&, Diagnostic);

/**
 * Local goal -> existing IK -> existing preview, with one latest-wins input slot.
 * No network, actor mutation, physical command or Mission Target ownership.
 * The source is an explicit, frozen CONFIRMED snapshot in this bounded slice.
 * All mutating methods and delegates belong to the Game Thread.
 */
UCLASS(ClassGroup = (DeferredTeleop), meta = (BlueprintSpawnableComponent))
class DEFERREDTELEOPRUNTIME_API UDeferredTeleopGoalAuthoringComponent : public UActorComponent
{
    GENERATED_BODY()

public:
    UDeferredTeleopGoalAuthoringComponent();

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Authoring")
    bool bConfigured = false;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Authoring")
    bool bHasPendingGoal = false;

    /** Kept for display after a rejected goal; never implies current validity. */
    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Authoring")
    FDttKinematicPreview LastValidPreview;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Authoring")
    FDttIKResult LastIKResult;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Authoring")
    FDttCanonicalTransform StartToolTransform;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Authoring")
    FString LastDiagnostic;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Authoring")
    double LastSolveMilliseconds = 0.0;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Authoring")
    int32 SolveCount = 0;

    UPROPERTY(BlueprintAssignable, Category = "Deferred Teleoperation|Authoring")
    FDttAuthoredPreviewChanged OnPreviewUpdated;

    UPROPERTY(BlueprintAssignable, Category = "Deferred Teleoperation|Authoring")
    FDttAuthoringDiagnosticChanged OnAuthoringDiagnostic;

    /** Explicit rebase: validates exact local model bytes and the selected confirmed state. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Authoring")
    bool ConfigureFromConfirmedView(
        const FDeferredTeleopArticulatedModelBinding& Binding,
        const FDeferredTeleopArticulatedViewState& View,
        const FDttGoalAuthoringSettings& Settings,
        FString& OutError);

    /** Parse with the existing strict parser; useful for an explicitly labelled fixture replay. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Authoring")
    bool ConfigureFromConfirmedJson(
        const FDeferredTeleopArticulatedModelBinding& Binding,
        const FString& ViewJson,
        const FDttGoalAuthoringSettings& Settings,
        FString& OutError);

    /** Target pose is in the canonical site frame, not root-relative coordinates. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Authoring")
    bool QueueCanonicalGoal(const FDttCanonicalTransform& Goal, EDttIKMode Mode, FString& OutError);

    /** Both transforms must be rigid and unit-scale. SiteToUnrealWorld is a presentation anchor. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Authoring")
    bool QueueUnrealGoal(
        const FTransform& TargetWorld,
        const FTransform& SiteToUnrealWorld,
        EDttIKMode Mode,
        FString& OutError);

    /** False immediately on new input, failed solve or failed source reconfiguration. */
    UFUNCTION(BlueprintPure, Category = "Deferred Teleoperation|Authoring")
    bool HasCurrentPreview() const;

    /** Copies a current candidate only. Freezing is local and submits NOTHING. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Authoring")
    bool CopyCurrentPreview(FDttKinematicPreview& OutPreview, FString& OutError) const;

    /** Local data for a SEPARATE candidate actor, never a Mission-owned actor. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Authoring")
    bool GetSourceModelAndState(
        FDttRobotDescription& OutDescription,
        FDttCanonicalTransform& OutRoot,
        TArray<FDttNamedJointPosition>& OutJoints) const;

    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Authoring")
    void ClearCandidate();

protected:
    virtual void TickComponent(float DeltaTime, ELevelTick TickType,
        FActorComponentTickFunction* ThisTickFunction) override;

private:
#if WITH_DEV_AUTOMATION_TESTS
    friend struct FDttGoalAuthoringTestAccess;
#endif
    FDeferredTeleopArticulatedModelBinding SourceBinding;
    FDttKinematicPreviewRequest SourceRequest;
    FDttGoalAuthoringSettings CurrentSettings;
    TArray<FDttNamedJointPosition> WarmSeed;
    FDttCanonicalTransform PendingGoal;
    EDttIKMode PendingMode = EDttIKMode::PositionOnly;
    FGuid PendingGoalId;
    FGuid CurrentInputId;
    FGuid AcceptedInputId;
    double NextSolveAt = 0.0;
    double LastPumpAt = -1.0;

    bool Reject(const FString& Reason, FString& OutError);
    void PublishDiagnostic(const FString& Reason);
    void ProcessPendingAt(double MonotonicSeconds);
};
