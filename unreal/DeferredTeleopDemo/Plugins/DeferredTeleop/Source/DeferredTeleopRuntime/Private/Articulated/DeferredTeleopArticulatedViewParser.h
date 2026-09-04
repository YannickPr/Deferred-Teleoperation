#pragma once

#include "Articulated/DeferredTeleopArticulatedViewTypes.h"

namespace DeferredTeleop::ArticulatedView
{
/** Parse a strict dtt/0 mission.articulated_view_state without mutating OutState on failure. */
bool ParseArticulated(
    const FString& Json,
    FDeferredTeleopArticulatedViewState& OutState,
    FString& OutError);

/** Compare all three structural identity fields and expose a diagnostic on mismatch. */
bool CompareModelReference(
    const FDeferredTeleopRobotModelReference& Actual,
    const FDeferredTeleopRobotModelReference& Expected,
    FString& OutDiagnostic);
}

