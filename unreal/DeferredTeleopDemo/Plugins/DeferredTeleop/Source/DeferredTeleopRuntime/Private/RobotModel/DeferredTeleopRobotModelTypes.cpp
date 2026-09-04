#include "RobotModel/DeferredTeleopRobotModelTypes.h"

#include "Math/UnrealMathUtility.h"

namespace DeferredTeleop::RobotModel::Private
{
FQuat4d MultiplyQuaternions(const FQuat4d& Left, const FQuat4d& Right)
{
    return FQuat4d(
        Left.W * Right.X + Left.X * Right.W + Left.Y * Right.Z - Left.Z * Right.Y,
        Left.W * Right.Y - Left.X * Right.Z + Left.Y * Right.W + Left.Z * Right.X,
        Left.W * Right.Z + Left.X * Right.Y - Left.Y * Right.X + Left.Z * Right.W,
        Left.W * Right.W - Left.X * Right.X - Left.Y * Right.Y - Left.Z * Right.Z);
}

FVector3d RotateVector(const FQuat4d& Rotation, const FVector3d& Vector)
{
    const FVector3d QuaternionVector(Rotation.X, Rotation.Y, Rotation.Z);
    const FVector3d TwiceCross = 2.0 * FVector3d::CrossProduct(QuaternionVector, Vector);
    return Vector + Rotation.W * TwiceCross + FVector3d::CrossProduct(QuaternionVector, TwiceCross);
}
} // namespace DeferredTeleop::RobotModel::Private

bool FDttCanonicalVector::IsFinite() const
{
    return FMath::IsFinite(X) && FMath::IsFinite(Y) && FMath::IsFinite(Z);
}

FVector3d FDttCanonicalVector::ToVector3d() const
{
    return FVector3d(X, Y, Z);
}

FDttCanonicalVector FDttCanonicalVector::FromVector3d(const FVector3d& Value)
{
    FDttCanonicalVector Result;
    Result.X = Value.X;
    Result.Y = Value.Y;
    Result.Z = Value.Z;
    return Result;
}

bool FDttCanonicalQuaternion::IsFinite() const
{
    return FMath::IsFinite(X) && FMath::IsFinite(Y) && FMath::IsFinite(Z)
        && FMath::IsFinite(W);
}

bool FDttCanonicalQuaternion::IsNormalized(double Tolerance) const
{
    if (!IsFinite() || !FMath::IsFinite(Tolerance) || Tolerance < 0.0)
    {
        return false;
    }

    const double NormSquared = X * X + Y * Y + Z * Z + W * W;
    return FMath::IsFinite(NormSquared) && FMath::Abs(NormSquared - 1.0) <= Tolerance;
}

bool FDttCanonicalTransform::IsFinite() const
{
    return TranslationMetres.IsFinite() && Rotation.IsFinite();
}

bool FDttCanonicalTransform::IsRigid(double Tolerance) const
{
    return IsFinite() && Rotation.IsNormalized(Tolerance);
}

FDttCanonicalTransform FDttCanonicalTransform::Identity()
{
    return FDttCanonicalTransform();
}

FDttCanonicalTransform FDttCanonicalTransform::FromTranslationRotation(
    const FVector3d& InTranslationMetres,
    const FQuat4d& InRotation)
{
    FDttCanonicalTransform Result;
    Result.TranslationMetres = FDttCanonicalVector::FromVector3d(InTranslationMetres);
    Result.Rotation.X = InRotation.X;
    Result.Rotation.Y = InRotation.Y;
    Result.Rotation.Z = InRotation.Z;
    Result.Rotation.W = InRotation.W;
    return Result;
}

FDttCanonicalTransform FDttCanonicalTransform::FromAxisAngle(
    const FVector3d& InTranslationMetres,
    const FVector3d& Axis,
    double AngleRadians)
{
    const double AxisNormSquared = Axis.SizeSquared();
    const double AxisNorm = FMath::Sqrt(AxisNormSquared);
    const double HalfAngle = 0.5 * AngleRadians;
    const double Sine = FMath::Sin(HalfAngle);
    const double AxisScale = AxisNorm > UE_DOUBLE_SMALL_NUMBER ? Sine / AxisNorm : 0.0;
    const FQuat4d Rotation(
        Axis.X * AxisScale,
        Axis.Y * AxisScale,
        Axis.Z * AxisScale,
        FMath::Cos(HalfAngle));
    return FromTranslationRotation(InTranslationMetres, Rotation);
}

FVector3d FDttCanonicalTransform::GetTranslationMetres() const
{
    return TranslationMetres.ToVector3d();
}

FQuat4d FDttCanonicalTransform::GetRotationQuaternion() const
{
    return FQuat4d(Rotation.X, Rotation.Y, Rotation.Z, Rotation.W);
}

FDttCanonicalTransform FDttCanonicalTransform::operator*(
    const FDttCanonicalTransform& Other) const
{
    const FQuat4d LeftRotation = GetRotationQuaternion();
    const FVector3d ComposedTranslation =
        GetTranslationMetres()
        + DeferredTeleop::RobotModel::Private::RotateVector(
            LeftRotation,
            Other.GetTranslationMetres());
    const FQuat4d ComposedRotation =
        DeferredTeleop::RobotModel::Private::MultiplyQuaternions(
            LeftRotation,
            Other.GetRotationQuaternion());
    return FromTranslationRotation(ComposedTranslation, ComposedRotation);
}
