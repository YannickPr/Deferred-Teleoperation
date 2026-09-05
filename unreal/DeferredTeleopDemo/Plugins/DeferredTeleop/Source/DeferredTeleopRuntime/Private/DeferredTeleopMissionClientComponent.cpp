#include "DeferredTeleopMissionClientComponent.h"

#include "Articulated/DeferredTeleopArticulatedViewParser.h"
#include "DeferredTeleopMissionViewParser.h"
#include "DeferredTeleopRuntime.h"
#include "HAL/PlatformTime.h"
#include "IWebSocket.h"
#include "WebSocketsModule.h"

UDeferredTeleopMissionClientComponent::UDeferredTeleopMissionClientComponent()
{
    PrimaryComponentTick.bCanEverTick = true;
}

void UDeferredTeleopMissionClientComponent::BeginPlay()
{
    Super::BeginPlay();
    if (bAutoConnect)
    {
        ConnectToMission();
    }
}

void UDeferredTeleopMissionClientComponent::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    bStopping = true;
    ReleaseSocket(true);
    SetConnectionState(EDeferredTeleopConnectionState::Disconnected);
    Super::EndPlay(EndPlayReason);
}

void UDeferredTeleopMissionClientComponent::TickComponent(
    const float DeltaTime,
    const ELevelTick TickType,
    FActorComponentTickFunction* ThisTickFunction)
{
    Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
    if (!bStopping
        && ConnectionState != EDeferredTeleopConnectionState::Disconnected
        && WireMode != ActiveWireMode)
    {
        HandleWireModeChangeWhileConnected();
    }
    if (bAutoConnect
        && !bStopping
        && ConnectionState == EDeferredTeleopConnectionState::Disconnected
        && FPlatformTime::Seconds() >= NextReconnectMonotonicSeconds)
    {
        ConnectToMission();
    }
}

void UDeferredTeleopMissionClientComponent::ConnectToMission()
{
    if (ConnectionState != EDeferredTeleopConnectionState::Disconnected)
    {
        return;
    }
    bStopping = false;
    bAutoConnect = true;
    ActiveWireMode = WireMode;
    LastSequenceBySourceId.Reset();
    SetConnectionState(EDeferredTeleopConnectionState::Connecting);

    WebSocket = FWebSocketsModule::Get().CreateWebSocket(MissionViewUrl);
    WebSocket->SetTextMessageMemoryLimit(256 * 1024);
    const uint64 CallbackGeneration = ++ConnectionGeneration;
    const TWeakObjectPtr<UDeferredTeleopMissionClientComponent> WeakThis(this);
    ConnectedDelegateHandle = WebSocket->OnConnected().AddLambda(
        [WeakThis, CallbackGeneration]()
        {
            if (UDeferredTeleopMissionClientComponent* Self = WeakThis.Get())
            {
                Self->HandleConnected(CallbackGeneration);
            }
        });
    ConnectionErrorDelegateHandle = WebSocket->OnConnectionError().AddLambda(
        [WeakThis, CallbackGeneration](const FString& Error)
        {
            if (UDeferredTeleopMissionClientComponent* Self = WeakThis.Get())
            {
                Self->HandleConnectionError(CallbackGeneration, Error);
            }
        });
    ClosedDelegateHandle = WebSocket->OnClosed().AddLambda(
        [WeakThis, CallbackGeneration](
            int32 StatusCode,
            const FString& Reason,
            bool bWasClean)
        {
            if (UDeferredTeleopMissionClientComponent* Self = WeakThis.Get())
            {
                Self->HandleClosed(CallbackGeneration, StatusCode, Reason, bWasClean);
            }
        });
    MessageDelegateHandle = WebSocket->OnMessage().AddLambda(
        [WeakThis, CallbackGeneration](const FString& Message)
        {
            if (UDeferredTeleopMissionClientComponent* Self = WeakThis.Get())
            {
                Self->HandleMessage(CallbackGeneration, Message);
            }
        });
    WebSocket->Connect();
}

void UDeferredTeleopMissionClientComponent::DisconnectFromMission()
{
    bAutoConnect = false;
    bStopping = true;
    LastSequenceBySourceId.Reset();
    ReleaseSocket(true);
    SetConnectionState(EDeferredTeleopConnectionState::Disconnected);
}

float UDeferredTeleopMissionClientComponent::GetLastValidStateAgeSeconds() const
{
    if (!bHasValidState)
    {
        return -1.0F;
    }
    return static_cast<float>(
        FMath::Max(0.0, FPlatformTime::Seconds() - LastValidReceiptMonotonicSeconds));
}

float UDeferredTeleopMissionClientComponent::GetLastValidArticulatedStateAgeSeconds() const
{
    if (!bHasValidArticulatedState)
    {
        return -1.0F;
    }
    return static_cast<float>(
        FMath::Max(0.0, FPlatformTime::Seconds() - LastArticulatedReceiptMonotonicSeconds));
}

void UDeferredTeleopMissionClientComponent::SetConnectionState(
    const EDeferredTeleopConnectionState NewState)
{
    if (ConnectionState == NewState)
    {
        return;
    }
    ConnectionState = NewState;
    OnMissionConnectionChanged.Broadcast(ConnectionState);
}

void UDeferredTeleopMissionClientComponent::ScheduleReconnect()
{
    ReleaseSocket(false);
    SetConnectionState(EDeferredTeleopConnectionState::Disconnected);
    NextReconnectMonotonicSeconds =
        FPlatformTime::Seconds() + FMath::Max(0.1F, ReconnectDelaySeconds);
}

void UDeferredTeleopMissionClientComponent::ReleaseSocket(const bool bClose)
{
    ++ConnectionGeneration;
    if (!WebSocket.IsValid())
    {
        return;
    }
    const TSharedPtr<IWebSocket> Socket = WebSocket;
    WebSocket.Reset();
    if (ConnectedDelegateHandle.IsValid())
    {
        Socket->OnConnected().Remove(ConnectedDelegateHandle);
        ConnectedDelegateHandle.Reset();
    }
    if (ConnectionErrorDelegateHandle.IsValid())
    {
        Socket->OnConnectionError().Remove(ConnectionErrorDelegateHandle);
        ConnectionErrorDelegateHandle.Reset();
    }
    if (ClosedDelegateHandle.IsValid())
    {
        Socket->OnClosed().Remove(ClosedDelegateHandle);
        ClosedDelegateHandle.Reset();
    }
    if (MessageDelegateHandle.IsValid())
    {
        Socket->OnMessage().Remove(MessageDelegateHandle);
        MessageDelegateHandle.Reset();
    }
    if (bClose && Socket->IsConnected())
    {
        Socket->Close(1000, TEXT("Deferred Teleoperation client stopped"));
    }
}

void UDeferredTeleopMissionClientComponent::HandleConnected(const uint64 CallbackGeneration)
{
    if (CallbackGeneration != ConnectionGeneration)
    {
        return;
    }
    SetConnectionState(EDeferredTeleopConnectionState::Connected);
    UE_LOG(LogDeferredTeleop, Display, TEXT("Connected to Mission view at %s"), *MissionViewUrl);
}

void UDeferredTeleopMissionClientComponent::HandleConnectionError(
    const uint64 CallbackGeneration,
    const FString& Error)
{
    if (CallbackGeneration != ConnectionGeneration)
    {
        return;
    }
    UE_LOG(LogDeferredTeleop, Warning, TEXT("Mission view connection failed: %s"), *Error);
    if (!bStopping)
    {
        ScheduleReconnect();
    }
}

void UDeferredTeleopMissionClientComponent::HandleClosed(
    const uint64 CallbackGeneration,
    const int32 StatusCode,
    const FString& Reason,
    const bool bWasClean)
{
    if (CallbackGeneration != ConnectionGeneration)
    {
        return;
    }
    UE_LOG(
        LogDeferredTeleop,
        Display,
        TEXT("Mission view connection closed (code=%d clean=%s): %s"),
        StatusCode,
        bWasClean ? TEXT("true") : TEXT("false"),
        *Reason);
    if (!bStopping)
    {
        ScheduleReconnect();
    }
}

void UDeferredTeleopMissionClientComponent::HandleMessage(
    const uint64 CallbackGeneration,
    const FString& Message)
{
    if (CallbackGeneration != ConnectionGeneration)
    {
        return;
    }

    if (ActiveWireMode == EDeferredTeleopMissionWireMode::ArticulatedView)
    {
        FDeferredTeleopArticulatedViewState Parsed;
        FString Error;
        if (!DeferredTeleop::ArticulatedView::ParseArticulated(Message, Parsed, Error))
        {
            UE_LOG(
                LogDeferredTeleop,
                Warning,
                TEXT("Rejected articulated Mission view message: %s"),
                *Error);
            OnMissionMessageRejected.Broadcast(Error);
            return;
        }

        const int32* LastSequence = LastSequenceBySourceId.Find(Parsed.SourceId);
        if (LastSequence != nullptr && Parsed.SourceSequence <= *LastSequence)
        {
            Error = FString::Printf(
                TEXT("rejected articulated source_sequence %d for source '%s'; last accepted is %d"),
                Parsed.SourceSequence,
                *Parsed.SourceId,
                *LastSequence);
            UE_LOG(LogDeferredTeleop, Warning, TEXT("%s"), *Error);
            OnMissionMessageRejected.Broadcast(Error);
            return;
        }
        LastSequenceBySourceId.Add(Parsed.SourceId, Parsed.SourceSequence);
        LastValidArticulatedState = MoveTemp(Parsed);
        bHasValidArticulatedState = true;
        LastArticulatedReceiptMonotonicSeconds = FPlatformTime::Seconds();
        OnArticulatedViewStateUpdated.Broadcast(LastValidArticulatedState);
        return;
    }

    FDeferredTeleopMissionViewState Parsed;
    FString Error;
    if (!DeferredTeleop::MissionView::Parse(Message, Parsed, Error))
    {
        UE_LOG(LogDeferredTeleop, Warning, TEXT("Rejected Mission view message: %s"), *Error);
        OnMissionMessageRejected.Broadcast(Error);
        return;
    }

    LastValidState = MoveTemp(Parsed);
    bHasValidState = true;
    LastValidReceiptMonotonicSeconds = FPlatformTime::Seconds();
    OnMissionViewStateUpdated.Broadcast(LastValidState);
}

void UDeferredTeleopMissionClientComponent::HandleWireModeChangeWhileConnected()
{
    const bool bShouldReconnect = bAutoConnect;
    LastSequenceBySourceId.Reset();
    ReleaseSocket(true);
    SetConnectionState(EDeferredTeleopConnectionState::Disconnected);
    if (bShouldReconnect && !bStopping)
    {
        NextReconnectMonotonicSeconds = FPlatformTime::Seconds();
    }
}
