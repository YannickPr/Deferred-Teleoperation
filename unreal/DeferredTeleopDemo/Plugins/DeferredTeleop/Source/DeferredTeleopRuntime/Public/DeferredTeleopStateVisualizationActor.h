#pragma once

#include "GameFramework/Actor.h"
#include "DeferredTeleopMissionViewTypes.h"
#include "DeferredTeleopStateVisualizationActor.generated.h"

class UDeferredTeleopMissionClientComponent;
class UCameraComponent;
class UMaterialInterface;
class UPointLightComponent;
class USceneCaptureComponent2D;
class USceneComponent;
class USplineComponent;
class UStaticMesh;
class UStaticMeshComponent;
class UTextRenderComponent;
class UTextureRenderTarget2D;

UCLASS(Blueprintable)
class DEFERREDTELEOPRUNTIME_API ADeferredTeleopStateVisualizationActor final : public AActor
{
    GENERATED_BODY()

public:
    ADeferredTeleopStateVisualizationActor();

    virtual void Tick(float DeltaSeconds) override;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Deferred Teleoperation")
    TObjectPtr<UDeferredTeleopMissionClientComponent> MissionClient;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Layout")
    FVector ConfirmedPresentationOffset = FVector(0.0, -160.0, 0.0);

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Layout")
    FVector ArrivalPresentationOffset = FVector::ZeroVector;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Layout")
    FVector TargetPresentationOffset = FVector(0.0, 160.0, 0.0);

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Appearance")
    TObjectPtr<UMaterialInterface> ConfirmedMaterial;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Appearance")
    TObjectPtr<UMaterialInterface> ArrivalMaterial;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Appearance")
    TObjectPtr<UMaterialInterface> TargetMaterial;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Appearance")
    TObjectPtr<UMaterialInterface> TrajectoryMaterial;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Demo")
    bool bUseAsViewTarget = true;

protected:
    virtual void BeginPlay() override;

private:
    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<USceneComponent> SceneRoot;

    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<UCameraComponent> DemoCamera;

    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<USceneCaptureComponent2D> EvidenceCamera;

    UPROPERTY(Transient)
    TObjectPtr<UTextureRenderTarget2D> EvidenceRenderTarget;

    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<UPointLightComponent> DemoLight;

    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<UStaticMeshComponent> ConfirmedMesh;

    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<UStaticMeshComponent> ArrivalMesh;

    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<UStaticMeshComponent> TargetMesh;

    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<UTextRenderComponent> ConfirmedLabel;

    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<UTextRenderComponent> ArrivalLabel;

    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<UTextRenderComponent> TargetLabel;

    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<UTextRenderComponent> ConnectionLabel;

    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<USplineComponent> TrajectorySpline;

    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<UStaticMeshComponent> TrajectoryLine;

    UPROPERTY(VisibleAnywhere, Category = "Deferred Teleoperation|Components")
    TObjectPtr<UStaticMeshComponent> TrajectoryMarker;

    UPROPERTY()
    TObjectPtr<UStaticMesh> CubeMesh;

    FDeferredTeleopMissionViewState CurrentView;
    bool bHasCurrentView = false;
    bool bEvidenceCaptureMode = false;
    bool bEvidenceCaptureRequested = false;
    bool bEvidenceExitRequested = false;
    double EvidenceCaptureAtMonotonicSeconds = 0.0;
    double EvidenceExitAtMonotonicSeconds = 0.0;

    UFUNCTION()
    void HandleMissionViewStateUpdated(const FDeferredTeleopMissionViewState& ViewState);

    UFUNCTION()
    void HandleMissionConnectionChanged(EDeferredTeleopConnectionState NewState);

    void ApplyStateTransforms();
    void UpdateTrajectory();
    void UpdateLabels();
};
