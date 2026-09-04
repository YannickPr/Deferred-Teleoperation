#pragma once

#include "GameFramework/Actor.h"
#include "Kinematics/DeferredTeleopKinematicsLibrary.h"
#include "DeferredTeleopKinematicRobotActor.generated.h"

class UArrowComponent;
class UMaterialInterface;
class USceneComponent;
class UStaticMesh;
class UStaticMeshComponent;
class UTextRenderComponent;

/** Presentation semantic supplied by the caller of a kinematic twin. */
UENUM(BlueprintType)
enum class EDeferredTeleopKinematicSemanticLayer : uint8
{
    Confirmed UMETA(DisplayName = "Confirmed"),
    Arrival UMETA(DisplayName = "Arrival"),
    Target UMETA(DisplayName = "Target"),
};

/**
 * A generic rigid-link visual twin driven by the canonical FK result.
 *
 * The actor owns no skeletal mesh and contains no kinematics of its own.  A
 * model is validated by the shared kinematics core, then each successful
 * named state is evaluated by that core and converted at the one canonical
 * Unreal boundary.  Link-frame components are flat children of SceneRoot and
 * receive absolute world transforms; Unreal attachment hierarchy never
 * re-evaluates the robot tree.
 */
UCLASS(Blueprintable)
class DEFERREDTELEOPRUNTIME_API ADeferredTeleopKinematicRobotActor : public AActor
{
    GENERATED_BODY()

public:
    ADeferredTeleopKinematicRobotActor();

    /** Semantic layer is explicit input for presentation; it is not inferred from a material. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Presentation")
    EDeferredTeleopKinematicSemanticLayer SemanticLayer =
        EDeferredTeleopKinematicSemanticLayer::Confirmed;

    /** Optional presentation materials for generated primitive markers and segments. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Presentation")
    TObjectPtr<UMaterialInterface> LinkMaterial;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Presentation")
    TObjectPtr<UMaterialInterface> ToolMaterial;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Presentation")
    TObjectPtr<UMaterialInterface> SegmentMaterial;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Debug")
    bool bShowDebugNames = true;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Debug")
    FColor DebugXAxisColor = FColor::Red;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Debug")
    FColor DebugYAxisColor = FColor::Green;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Debug")
    FColor DebugZAxisColor = FColor::Blue;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Debug")
    FColor DebugJointAxisColor = FColor::Yellow;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Debug")
    FColor DebugToolColor = FColor::Magenta;

    /** Whether a validated model is currently installed. */
    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematic Robot")
    bool bModelInitialized = false;

    /** Whether the actor has a successfully applied pose to render and query. */
    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematic Robot")
    bool bHasValidPose = false;

    /** Most recent rejected input error, or empty after a successful operation. */
    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematic Robot")
    FString LastError;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematic Robot")
    bool bLastStateWithinJointLimits = true;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematic Robot")
    TArray<FString> LastDiagnostics;

    /**
     * Validate and install a model and its canonical world pose for the root.
     * The pose is applied by the first subsequent successful ApplyState call.
     */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Kinematic Robot")
    bool InitializeModel(
        const FDttRobotDescription& Description,
        const FDttCanonicalTransform& WorldTransformOfRoot,
        FString& OutError);

    /** Evaluate a named revolute state through the shared FK core and render it. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Kinematic Robot")
    bool ApplyState(
        const TArray<FDttNamedJointPosition>& JointPositions,
        FString& OutError);

    /** Return the last successful absolute world transform for a named link. */
    UFUNCTION(BlueprintPure, Category = "Deferred Teleoperation|Kinematic Robot")
    bool GetLinkTransform(
        FName LinkName,
        FTransform& OutWorldTransform,
        FString& OutError) const;

    /** Return the last successful absolute world transform for a named tool frame. */
    UFUNCTION(BlueprintPure, Category = "Deferred Teleoperation|Kinematic Robot")
    bool GetToolTransform(
        FName ToolName,
        FTransform& OutWorldTransform,
        FString& OutError) const;

    /** Toggle generated axes, joint-axis arrows, origin segments, and labels. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Kinematic Robot|Debug")
    void SetDebugFramesVisible(bool bVisible);

private:
    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<USceneComponent> SceneRoot;

    UPROPERTY(Transient)
    TArray<TObjectPtr<USceneComponent>> LinkFrameComponents;

    UPROPERTY(Transient)
    TArray<TObjectPtr<UStaticMeshComponent>> LinkMarkerComponents;

    UPROPERTY(Transient)
    TArray<TObjectPtr<UArrowComponent>> LinkXAxisComponents;

    UPROPERTY(Transient)
    TArray<TObjectPtr<UArrowComponent>> LinkYAxisComponents;

    UPROPERTY(Transient)
    TArray<TObjectPtr<UArrowComponent>> LinkZAxisComponents;

    UPROPERTY(Transient)
    TArray<TObjectPtr<UTextRenderComponent>> LinkLabelComponents;

    UPROPERTY(Transient)
    TArray<TObjectPtr<UStaticMeshComponent>> LinkSegmentComponents;

    UPROPERTY(Transient)
    TArray<TObjectPtr<USceneComponent>> ToolFrameComponents;

    UPROPERTY(Transient)
    TArray<TObjectPtr<UStaticMeshComponent>> ToolMarkerComponents;

    UPROPERTY(Transient)
    TArray<TObjectPtr<UTextRenderComponent>> ToolLabelComponents;

    UPROPERTY(Transient)
    TArray<TObjectPtr<UArrowComponent>> JointAxisComponents;

    UPROPERTY()
    TObjectPtr<UStaticMesh> CubeMesh;

    UPROPERTY()
    TObjectPtr<UStaticMesh> SphereMesh;

    UPROPERTY(Transient)
    FDttRobotDescription CurrentDescription;

    FDttValidatedRobotModel ValidatedModel;
    FDttCanonicalTransform WorldTransformOfRootCanonical;
    FTransform RootWorldRenderTransform;

    TMap<FName, int32> LinkSlotByName;
    TMap<FName, int32> ToolSlotByName;
    TArray<int32> ParentLinkIndexByLink;

    TArray<FTransform> LastLinkWorldTransforms;
    TArray<FTransform> LastToolWorldTransforms;
    TArray<FDttNamedJointPosition> LastJointPositions;

    bool bDebugFramesVisible = true;
    int32 TopologyGeneration = 0;

    void DestroyTopology();
    bool BuildTopology(
        const FDttRobotDescription& Description,
        const FDttValidatedRobotModel& Model,
        FString& OutError);
    void SetTopologyVisible(bool bPoseVisible);
    void UpdateDebugVisibility();
    void HidePoseComponents();

    static bool AreDescriptionsEqual(
        const FDttRobotDescription& Left,
        const FDttRobotDescription& Right);
    static bool AreCanonicalTransformsEqual(
        const FDttCanonicalTransform& Left,
        const FDttCanonicalTransform& Right);
    static FString SemanticLayerLabel(EDeferredTeleopKinematicSemanticLayer Layer);
    bool HasUnitActorScale(FString& OutError) const;
};
