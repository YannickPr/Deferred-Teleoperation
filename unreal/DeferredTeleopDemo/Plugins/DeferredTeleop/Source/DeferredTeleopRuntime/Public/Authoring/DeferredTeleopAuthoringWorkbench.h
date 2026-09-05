#pragma once

#include "GameFramework/Actor.h"
#include "Authoring/DeferredTeleopGoalAuthoringComponent.h"
#include "DeferredTeleopAuthoringWorkbench.generated.h"

class USceneComponent;
class UMaterialInterface;
class ADeferredTeleopKinematicRobotActor;

/**
 * Opt-in, fixture-only Editor/Development workbench. No network or hardware.
 * Place at the identity transform, explicitly enable fixture initialization,
 * and use BeginTargetEdit / EndTargetEdit from desktop or VR input.
 * This example owns two actors, never Mission's Confirmed/Arrival/Target actors.
 */
UCLASS(Blueprintable)
class DEFERREDTELEOPRUNTIME_API ADeferredTeleopAuthoringWorkbench : public AActor
{
    GENERATED_BODY()

public:
    ADeferredTeleopAuthoringWorkbench();

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Deferred Teleoperation|Workbench")
    TObjectPtr<UDeferredTeleopGoalAuthoringComponent> Authoring;

    /** Move this rigid component, not the workbench or robot Actor. */
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Deferred Teleoperation|Workbench")
    TObjectPtr<USceneComponent> TargetHandle;

    /** Off by default: a fixture is never an automatic substitute for live evidence. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Workbench")
    bool bInitializeSyntheticFixtureOnBeginPlay = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Workbench")
    TObjectPtr<UMaterialInterface> ReferenceMaterial;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Workbench")
    TObjectPtr<UMaterialInterface> CandidateMaterial;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Workbench")
    bool bDrawDevelopmentOverlay = true;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Workbench")
    bool bReady = false;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Workbench")
    bool bEditing = false;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Workbench")
    bool bFreezePending = false;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Workbench")
    bool bHasFrozenPreview = false;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Workbench")
    FDttKinematicPreview FrozenPreview;

    /** These are local visual copies of synthetic fixture data. */
    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Workbench")
    TObjectPtr<ADeferredTeleopKinematicRobotActor> ReferenceRobot;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Workbench")
    TObjectPtr<ADeferredTeleopKinematicRobotActor> CandidateRobot;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Workbench")
    FString StatusText = TEXT("NOT INITIALIZED - NO NETWORK / NO HARDWARE");

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Workbench")
    EDttIKMode GoalMode = EDttIKMode::PositionOnly;

    /** Reload the committed fixture and exact model bytes; no fallback and no network. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Workbench")
    bool InitializeSyntheticFixture(FString& OutError);

    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Workbench")
    bool BeginTargetEdit(FString& OutError);

    /** Queues the exact final handle pose; freeze waits for that solve, never the preceding pose. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Workbench")
    bool EndTargetEdit(bool bFreezeLocally, FString& OutError);

    /** Clears pending work and freeze intent. Use on tracking loss and local cancel. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Workbench")
    void CancelLocalEdit();

    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Workbench")
    bool ResetTargetToSource(FString& OutError);

    /** Mode changes invalidate old candidates and require a new solve. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Workbench")
    bool SetGoalMode(EDttIKMode NewMode, FString& OutError);

protected:
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
#if WITH_DEV_AUTOMATION_TESTS
    friend struct FDttAuthoringWorkbenchTestAccess;
#endif
    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Workbench")
    TObjectPtr<USceneComponent> SceneRoot;

    FTransform LastQueuedTarget;
    bool bHasQueuedTarget = false;
    bool bHasPresentationError = false;

    void ConnectDelegates();
    bool ValidateStage(FString& OutError) const;
    bool QueueHandle(FString& OutError);
    bool EnsureOwnedRobots(FString& OutError);
    void DestroyOwnedRobots();
    void DrawOverlay();

    UFUNCTION()
    void HandlePreview(const FDttKinematicPreview& Preview);

    UFUNCTION()
    void HandleDiagnostic(const FString& Diagnostic);
};
