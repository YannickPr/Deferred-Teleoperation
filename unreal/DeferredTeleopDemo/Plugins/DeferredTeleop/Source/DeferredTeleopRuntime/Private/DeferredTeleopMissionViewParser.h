#pragma once

#include "CoreMinimal.h"
#include "DeferredTeleopMissionViewTypes.h"

namespace DeferredTeleop::MissionView
{
bool Parse(const FString& Json, FDeferredTeleopMissionViewState& OutState, FString& OutError);
}
