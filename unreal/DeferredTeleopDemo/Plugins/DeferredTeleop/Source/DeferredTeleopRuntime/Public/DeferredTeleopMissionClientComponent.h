#pragma once

#include "Components/ActorComponent.h"
#include "DeferredTeleopMissionViewTypes.h"
#include "Templates/SharedPointer.h"
#include "DeferredTeleopMissionClientComponent.generated.h"

class IWebSocket;

DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(
    FDeferredTeleopMissionViewStateUpdated,
    const FDeferredTeleopMissionViewState&,
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

    UPROPERTY(BlueprintAssignable, Category = "Deferred Teleoperation|Mission")
    FDeferredTeleopMissionViewStateUpdated OnMissionViewStateUpdated;

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

protected:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
    virtual void TickComponent(
        float DeltaTime,
        ELevelTick TickType,
        FActorComponentTickFunction* ThisTickFunction) override;

private:
    TSharedPtr<IWebSocket> WebSocket;
    double LastValidReceiptMonotonicSeconds = 0.0;
    double NextReconnectMonotonicSeconds = 0.0;
    bool bStopping = false;

    void SetConnectionState(EDeferredTeleopConnectionState NewState);
    void ScheduleReconnect();
    void ReleaseSocket(bool bClose);
    void HandleConnected();
    void HandleConnectionError(const FString& Error);
    void HandleClosed(int32 StatusCode, const FString& Reason, bool bWasClean);
    void HandleMessage(const FString& Message);
};
