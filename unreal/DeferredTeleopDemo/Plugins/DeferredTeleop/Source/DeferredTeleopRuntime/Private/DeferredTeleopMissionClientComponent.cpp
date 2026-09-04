#include "DeferredTeleopMissionClientComponent.h"

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
    SetConnectionState(EDeferredTeleopConnectionState::Connecting);

    WebSocket = FWebSocketsModule::Get().CreateWebSocket(MissionViewUrl);
    WebSocket->SetTextMessageMemoryLimit(256 * 1024);
    WebSocket->OnConnected().AddUObject(this, &UDeferredTeleopMissionClientComponent::HandleConnected);
    WebSocket->OnConnectionError().AddUObject(
        this,
        &UDeferredTeleopMissionClientComponent::HandleConnectionError);
    WebSocket->OnClosed().AddUObject(this, &UDeferredTeleopMissionClientComponent::HandleClosed);
    WebSocket->OnMessage().AddUObject(this, &UDeferredTeleopMissionClientComponent::HandleMessage);
    WebSocket->Connect();
}

void UDeferredTeleopMissionClientComponent::DisconnectFromMission()
{
    bAutoConnect = false;
    bStopping = true;
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
    if (!WebSocket.IsValid())
    {
        return;
    }
    const TSharedPtr<IWebSocket> Socket = WebSocket;
    WebSocket.Reset();
    Socket->OnConnected().RemoveAll(this);
    Socket->OnConnectionError().RemoveAll(this);
    Socket->OnClosed().RemoveAll(this);
    Socket->OnMessage().RemoveAll(this);
    if (bClose && Socket->IsConnected())
    {
        Socket->Close(1000, TEXT("Deferred Teleoperation client stopped"));
    }
}

void UDeferredTeleopMissionClientComponent::HandleConnected()
{
    SetConnectionState(EDeferredTeleopConnectionState::Connected);
    UE_LOG(LogDeferredTeleop, Display, TEXT("Connected to Mission view at %s"), *MissionViewUrl);
}

void UDeferredTeleopMissionClientComponent::HandleConnectionError(const FString& Error)
{
    UE_LOG(LogDeferredTeleop, Warning, TEXT("Mission view connection failed: %s"), *Error);
    if (!bStopping)
    {
        ScheduleReconnect();
    }
}

void UDeferredTeleopMissionClientComponent::HandleClosed(
    const int32 StatusCode,
    const FString& Reason,
    const bool bWasClean)
{
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

void UDeferredTeleopMissionClientComponent::HandleMessage(const FString& Message)
{
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
