#pragma once

#include "Components/ActorComponent.h"
#include "Articulated/DeferredTeleopArticulatedViewTypes.h"
#include "DeferredTeleopMissionViewTypes.h"
#include "Templates/SharedPointer.h"
#include "DeferredTeleopMissionClientComponent.generated.h"

class IWebSocket;

#if WITH_DEV_AUTOMATION_TESTS
struct FDeferredTeleopMissionClientTestAccess;
#endif

UENUM(BlueprintType)
enum class EDeferredTeleopMissionWireMode : uint8
{
    LegacyView UMETA(DisplayName = "Legacy Mission View"),
    ArticulatedView UMETA(DisplayName = "Articulated Mission View"),
};

DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(
    FDeferredTeleopMissionViewStateUpdated,
    const FDeferredTeleopMissionViewState&,
    ViewState);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(
    FDeferredTeleopMissionArticulatedViewStateUpdated,
    const FDeferredTeleopArticulatedViewState&,
    ViewState);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(
    FDeferredTeleopMissionConnectionChanged,
    EDeferredTeleopConnectionState,
    ConnectionState);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(
    FDeferredTeleopMissionMessageRejected,
    const FString&,
    Reason);

UCLASS(ClassGroup = (DeferredTeleoperation), meta = (BlueprintSpawnableComponent))
class DEFERREDTELEOPRUNTIME_API UDeferredTeleopMissionClientComponent final
    : public UActorComponent
{
    GENERATED_BODY()

public:
    UDeferredTeleopMissionClientComponent();

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Mission")
    FString MissionViewUrl = TEXT("ws://127.0.0.1:8772");

    /** The wire parser is selected once when a socket connection starts. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Mission")
    EDeferredTeleopMissionWireMode WireMode = EDeferredTeleopMissionWireMode::LegacyView;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Mission")
    bool bAutoConnect = true;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Mission", meta = (ClampMin = "0.1"))
    float ReconnectDelaySeconds = 1.0F;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Mission")
    EDeferredTeleopConnectionState ConnectionState = EDeferredTeleopConnectionState::Disconnected;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Mission")
    bool bHasValidState = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Mission")
    FDeferredTeleopMissionViewState LastValidState;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Mission")
    bool bHasValidArticulatedState = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Mission")
    FDeferredTeleopArticulatedViewState LastValidArticulatedState;

    UPROPERTY(BlueprintAssignable, Category = "Deferred Teleoperation|Mission")
    FDeferredTeleopMissionViewStateUpdated OnMissionViewStateUpdated;

    UPROPERTY(BlueprintAssignable, Category = "Deferred Teleoperation|Mission")
    FDeferredTeleopMissionArticulatedViewStateUpdated OnArticulatedViewStateUpdated;

    UPROPERTY(BlueprintAssignable, Category = "Deferred Teleoperation|Mission")
    FDeferredTeleopMissionConnectionChanged OnMissionConnectionChanged;

    UPROPERTY(BlueprintAssignable, Category = "Deferred Teleoperation|Mission")
    FDeferredTeleopMissionMessageRejected OnMissionMessageRejected;

    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Mission")
    void ConnectToMission();

    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Mission")
    void DisconnectFromMission();

    UFUNCTION(BlueprintPure, Category = "Deferred Teleoperation|Mission")
    float GetLastValidStateAgeSeconds() const;

    UFUNCTION(BlueprintPure, Category = "Deferred Teleoperation|Mission")
    float GetLastValidArticulatedStateAgeSeconds() const;

protected:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
    virtual void TickComponent(
        float DeltaTime,
        ELevelTick TickType,
        FActorComponentTickFunction* ThisTickFunction) override;

private:
#if WITH_DEV_AUTOMATION_TESTS
    friend struct FDeferredTeleopMissionClientTestAccess;
#endif
    TSharedPtr<IWebSocket> WebSocket;
    double LastValidReceiptMonotonicSeconds = 0.0;
    double LastArticulatedReceiptMonotonicSeconds = 0.0;
    double NextReconnectMonotonicSeconds = 0.0;
    bool bStopping = false;
    EDeferredTeleopMissionWireMode ActiveWireMode = EDeferredTeleopMissionWireMode::LegacyView;
    TMap<FString, int32> LastSequenceBySourceId;
    uint64 ConnectionGeneration = 0;
    FDelegateHandle ConnectedDelegateHandle;
    FDelegateHandle ConnectionErrorDelegateHandle;
    FDelegateHandle ClosedDelegateHandle;
    FDelegateHandle MessageDelegateHandle;

    void SetConnectionState(EDeferredTeleopConnectionState NewState);
    void ScheduleReconnect();
    void ReleaseSocket(bool bClose);
    void HandleConnected(uint64 CallbackGeneration);
    void HandleConnectionError(uint64 CallbackGeneration, const FString& Error);
    void HandleClosed(
        uint64 CallbackGeneration,
        int32 StatusCode,
        const FString& Reason,
        bool bWasClean);
    void HandleMessage(uint64 CallbackGeneration, const FString& Message);
    void HandleWireModeChangeWhileConnected();
};
