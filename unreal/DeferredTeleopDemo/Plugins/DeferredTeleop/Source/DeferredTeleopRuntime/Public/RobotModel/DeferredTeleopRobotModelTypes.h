#pragma once

#include "CoreMinimal.h"
#include "Math/Quat.h"
#include "Math/Vector.h"
#include "DeferredTeleopRobotModelTypes.generated.h"

/**
 * A three-component value in the canonical robotics coordinate system.
 *
 * Canonical vectors are right-handed, Z-up, and use metres where the value is
 * a translation.  Joint axes use the same storage type but are dimensionless
 * unit vectors.  The explicit field names keep a Blueprint-authored value
 * from being mistaken for an Unreal centimetre vector.
 */
USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttCanonicalVector
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Canonical")
    double X = 0.0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Canonical")
    double Y = 0.0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Canonical")
    double Z = 0.0;

    bool IsFinite() const;
    FVector3d ToVector3d() const;

    static FDttCanonicalVector FromVector3d(const FVector3d& Value);
};

/** A unit quaternion in canonical XYZW order. */
USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttCanonicalQuaternion
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Canonical")
    double X = 0.0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Canonical")
    double Y = 0.0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Canonical")
    double Z = 0.0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Canonical")
    double W = 1.0;

    bool IsFinite() const;
    bool IsNormalized(double Tolerance = 1.0e-9) const;
};

/**
 * Rigid transform in the canonical robotics convention:
 * right-handed, Z-up, metres, radians, and column-vector composition.
 *
 * A value named ParentToJoint represents ^parent T_joint.  It must not carry
 * scale; Unreal conversion is deliberately performed at the kinematics
 * boundary instead of by an Actor or component scale.
 */
USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttCanonicalTransform
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Canonical")
    FDttCanonicalVector TranslationMetres;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Canonical")
    FDttCanonicalQuaternion Rotation;

    bool IsFinite() const;
    bool IsRigid(double Tolerance = 1.0e-9) const;

    static FDttCanonicalTransform Identity();
    static FDttCanonicalTransform FromTranslationRotation(
        const FVector3d& TranslationMetres,
        const FQuat4d& Rotation);
    static FDttCanonicalTransform FromAxisAngle(
        const FVector3d& TranslationMetres,
        const FVector3d& Axis,
        double AngleRadians);

    FVector3d GetTranslationMetres() const;
    FQuat4d GetRotationQuaternion() const;

    /** Composition uses ^A T_C = ^A T_B * ^B T_C. */
    FDttCanonicalTransform operator*(const FDttCanonicalTransform& Other) const;
};

UENUM(BlueprintType)
enum class EDttRobotJointType : uint8
{
    Fixed,
    Revolute,
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttRobotLinkDescription
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    FName Name = NAME_None;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttRobotJointDescription
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    FName Name = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    EDttRobotJointType Type = EDttRobotJointType::Fixed;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    FName ParentLink = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    FName ChildLink = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    FDttCanonicalTransform ParentToJoint;

    /** Unit axis expressed in the joint frame.  Required for revolute joints. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    FDttCanonicalVector AxisJointFrame;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    bool bHasPositionLimits = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    double LowerPositionRadians = 0.0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    double UpperPositionRadians = 0.0;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttRobotJointGroupDescription
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    FName Name = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    TArray<FName> JointNames;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttRobotToolFrameDescription
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    FName Name = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    FName LinkName = NAME_None;

    /** ^link T_tool, independent of any visual mesh origin. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    FDttCanonicalTransform LinkToTool;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttRobotDescription
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    FString ModelId;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    FString ModelRevision;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    FName RootLinkName = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    TArray<FDttRobotLinkDescription> Links;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    TArray<FDttRobotJointDescription> Joints;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    TArray<FDttRobotJointGroupDescription> JointGroups;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Robot Model")
    TArray<FDttRobotToolFrameDescription> ToolFrames;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttNamedJointPosition
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics")
    FName JointName = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics")
    double PositionRadians = 0.0;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttNamedCanonicalTransform
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics")
    FName Name = NAME_None;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics")
    FDttCanonicalTransform Transform;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttForwardKinematicsResult
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics")
    bool bSuccess = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics")
    bool bWithinJointLimits = true;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics")
    FString ErrorMessage;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics")
    FString ModelId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics")
    FString ModelRevision;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics")
    TArray<FDttNamedCanonicalTransform> LinkTransforms;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics")
    TArray<FDttNamedCanonicalTransform> ToolTransforms;

    /** Non-fatal diagnostics, including joint-limit violations. */
    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics")
    TArray<FString> Diagnostics;
};

namespace DeferredTeleop::RobotModel
{
/**
 * Load the committed dtt.robot-description/0 JSON representation.
 *
 * This is intentionally a small schema-scoped reader for the generated
 * canonical description; it is not a runtime URDF importer or a global JSON
 * validator. Fields consumed by the kinematics model are checked for their
 * required shape and values. Source metadata is checked structurally for
 * traceability, while visual entries are only checked to be JSON objects;
 * neither is retained in FDttRobotDescription or used by FK. The caller owns
 * the JSON text, which keeps file/network policy outside the runtime model
 * core.
 */
DEFERREDTELEOPRUNTIME_API bool ParseRobotDescriptionJson(
    const FString& Json,
    FDttRobotDescription& OutDescription,
    FString& OutError);
} // namespace DeferredTeleop::RobotModel
