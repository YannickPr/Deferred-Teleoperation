#include "DeferredTeleopVisualizationLibrary.h"

FTransform UDeferredTeleopVisualizationLibrary::MissionPoseToUnrealTransform(
    const FDeferredTeleopPose& MissionPose)
{
    // dtt/0 is right-handed (+X forward, +Y left, +Z up) in metres.
    // Unreal is left-handed (+X forward, +Y right, +Z up) in centimetres.
    const FVector LocationCentimetres(
        MissionPose.PositionMetres.X * 100.0,
        -MissionPose.PositionMetres.Y * 100.0,
        MissionPose.PositionMetres.Z * 100.0);
    FQuat UnrealOrientation(
        -MissionPose.Orientation.X,
        MissionPose.Orientation.Y,
        -MissionPose.Orientation.Z,
        MissionPose.Orientation.W);
    UnrealOrientation.Normalize();
    return FTransform(UnrealOrientation, LocationCentimetres);
}
