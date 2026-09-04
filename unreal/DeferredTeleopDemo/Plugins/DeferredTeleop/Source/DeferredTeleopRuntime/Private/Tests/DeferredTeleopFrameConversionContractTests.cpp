#if WITH_DEV_AUTOMATION_TESTS

#include "Kinematics/DeferredTeleopKinematicsLibrary.h"
#include "Misc/AutomationTest.h"

namespace DeferredTeleop::Tests::KinematicsFrameContract
{
constexpr double Pi = 3.1415926535897932384626433832795;

// Keep dimensions and numerical contracts named separately.  Translation is
// compared in Unreal centimetres, canonical translation in metres, and
// rotations by the angular distance between unit quaternions.
constexpr double TranslationCentimetresTolerance = 1.0e-5;
constexpr double CanonicalTranslationMetresTolerance = 1.0e-8;
constexpr double QuaternionAngleToleranceRadians = 1.0e-6;
constexpr double OrthonormalityTolerance = 1.0e-6;
constexpr double QuaternionNormTolerance = 1.0e-6;
constexpr double ScaleTolerance = 1.0e-6;

FDttCanonicalTransform Translation(double X, double Y, double Z)
{
    return FDttCanonicalTransform::FromTranslationRotation(
        FVector3d(X, Y, Z),
        FQuat4d(0.0, 0.0, 0.0, 1.0));
}

FDttCanonicalTransform Rotation(
    const FVector3d& Axis,
    double AngleRadians)
{
    return FDttCanonicalTransform::FromAxisAngle(
        FVector3d(0.0, 0.0, 0.0),
        Axis,
        AngleRadians);
}

bool QuaternionsWithinAngle(
    const FQuat4d& Left,
    const FQuat4d& Right,
    double AngleToleranceRadians)
{
    const double LeftNormSquared =
        Left.X * Left.X + Left.Y * Left.Y + Left.Z * Left.Z + Left.W * Left.W;
    const double RightNormSquared =
        Right.X * Right.X + Right.Y * Right.Y + Right.Z * Right.Z + Right.W * Right.W;
    if (!FMath::IsFinite(LeftNormSquared)
        || !FMath::IsFinite(RightNormSquared)
        || LeftNormSquared <= UE_DOUBLE_SMALL_NUMBER
        || RightNormSquared <= UE_DOUBLE_SMALL_NUMBER)
    {
        return false;
    }
    const double Dot = FMath::Abs(
        (Left.X * Right.X + Left.Y * Right.Y + Left.Z * Right.Z + Left.W * Right.W)
        / FMath::Sqrt(LeftNormSquared * RightNormSquared));
    // abs(dot) removes the q/-q representation ambiguity.  Comparing against
    // cos(angle/2) avoids acos near one and therefore remains well-conditioned.
    return Dot >= FMath::Cos(0.5 * AngleToleranceRadians);
}

void TestQuaternionIsUnit(
    FAutomationTestBase& Test,
    const TCHAR* Description,
    const FQuat& Quaternion)
{
    const double NormSquared =
        static_cast<double>(Quaternion.X) * Quaternion.X
        + static_cast<double>(Quaternion.Y) * Quaternion.Y
        + static_cast<double>(Quaternion.Z) * Quaternion.Z
        + static_cast<double>(Quaternion.W) * Quaternion.W;
    Test.TestTrue(
        Description,
        FMath::IsFinite(NormSquared)
            && FMath::Abs(NormSquared - 1.0) <= QuaternionNormTolerance);
}
} // namespace DeferredTeleop::Tests::KinematicsFrameContract

namespace DeferredTeleop::Tests::KinematicsFrameContract
{

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopFrameConversionContractTest,
    "DeferredTeleop.M2.Kinematics.FrameConversionContract",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopFrameConversionContractTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FString Error;
    FTransform Unreal;

    struct FTranslationCase
    {
        const TCHAR* Name;
        FDttCanonicalTransform Canonical;
        FVector ExpectedCentimetres;
    };
    const FTranslationCase TranslationCases[] = {
        {TEXT("origin"), Translation(0.0, 0.0, 0.0), FVector(0.0F, 0.0F, 0.0F)},
        {TEXT("positive X"), Translation(1.0, 0.0, 0.0), FVector(100.0F, 0.0F, 0.0F)},
        {TEXT("positive Y"), Translation(0.0, 1.0, 0.0), FVector(0.0F, -100.0F, 0.0F)},
        {TEXT("positive Z"), Translation(0.0, 0.0, 1.0), FVector(0.0F, 0.0F, 100.0F)},
    };
    for (const FTranslationCase& Case : TranslationCases)
    {
        Error.Reset();
        TestTrue(
            *FString::Printf(TEXT("%s translation converts"), Case.Name),
            DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
                Case.Canonical,
                Unreal,
                Error));
        TestTrue(
            *FString::Printf(TEXT("%s maps to centimetres and S"), Case.Name),
            Unreal.GetLocation().Equals(
                Case.ExpectedCentimetres,
                static_cast<float>(TranslationCentimetresTolerance)));
        TestTrue(
            *FString::Printf(TEXT("%s keeps unit scale"), Case.Name),
            Unreal.GetScale3D().Equals(FVector::OneVector, static_cast<float>(ScaleTolerance)));
        TestQuaternionIsUnit(*this, *FString::Printf(TEXT("%s has unit quaternion"), Case.Name), Unreal.GetRotation());
    }

    struct FQuarterTurnCase
    {
        const TCHAR* Name;
        FVector3d Axis;
        double AngleRadians;
        FVector ExpectedImages[3];
    };
    // These are the images of Unreal +X, +Y, +Z under S R S, with
    // S=diag(1,-1,1).  The table is independent of the implementation under test.
    const FQuarterTurnCase QuarterTurns[] = {
        {
            TEXT("+90 around X"),
            FVector3d(1.0, 0.0, 0.0),
            Pi / 2.0,
            {FVector(1.0F, 0.0F, 0.0F), FVector(0.0F, 0.0F, -1.0F), FVector(0.0F, 1.0F, 0.0F)},
        },
        {
            TEXT("-90 around X"),
            FVector3d(1.0, 0.0, 0.0),
            -Pi / 2.0,
            {FVector(1.0F, 0.0F, 0.0F), FVector(0.0F, 0.0F, 1.0F), FVector(0.0F, -1.0F, 0.0F)},
        },
        {
            TEXT("+90 around Y"),
            FVector3d(0.0, 1.0, 0.0),
            Pi / 2.0,
            {FVector(0.0F, 0.0F, -1.0F), FVector(0.0F, 1.0F, 0.0F), FVector(1.0F, 0.0F, 0.0F)},
        },
        {
            TEXT("-90 around Y"),
            FVector3d(0.0, 1.0, 0.0),
            -Pi / 2.0,
            {FVector(0.0F, 0.0F, 1.0F), FVector(0.0F, 1.0F, 0.0F), FVector(-1.0F, 0.0F, 0.0F)},
        },
        {
            TEXT("+90 around Z"),
            FVector3d(0.0, 0.0, 1.0),
            Pi / 2.0,
            {FVector(0.0F, -1.0F, 0.0F), FVector(1.0F, 0.0F, 0.0F), FVector(0.0F, 0.0F, 1.0F)},
        },
        {
            TEXT("-90 around Z"),
            FVector3d(0.0, 0.0, 1.0),
            -Pi / 2.0,
            {FVector(0.0F, 1.0F, 0.0F), FVector(-1.0F, 0.0F, 0.0F), FVector(0.0F, 0.0F, 1.0F)},
        },
    };
    const FVector BasisVectors[] = {
        FVector(1.0F, 0.0F, 0.0F),
        FVector(0.0F, 1.0F, 0.0F),
        FVector(0.0F, 0.0F, 1.0F),
    };
    for (const FQuarterTurnCase& Case : QuarterTurns)
    {
        Error.Reset();
        TestTrue(
            *FString::Printf(TEXT("%s conversion succeeds"), Case.Name),
            DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
                Rotation(Case.Axis, Case.AngleRadians),
                Unreal,
                Error));
        TestQuaternionIsUnit(*this, *FString::Printf(TEXT("%s has unit quaternion"), Case.Name), Unreal.GetRotation());

        FVector Images[3];
        for (int32 BasisIndex = 0; BasisIndex < UE_ARRAY_COUNT(BasisVectors); ++BasisIndex)
        {
            Images[BasisIndex] = Unreal.TransformVector(BasisVectors[BasisIndex]);
            TestTrue(
                *FString::Printf(TEXT("%s maps basis %d according to SRS"), Case.Name, BasisIndex),
                Images[BasisIndex].Equals(
                    Case.ExpectedImages[BasisIndex],
                    static_cast<float>(OrthonormalityTolerance)));
            TestTrue(
                *FString::Printf(TEXT("%s basis %d has unit norm"), Case.Name, BasisIndex),
                FMath::Abs(static_cast<double>(Images[BasisIndex].SizeSquared()) - 1.0)
                    <= OrthonormalityTolerance);
        }
        TestTrue(
            *FString::Printf(TEXT("%s basis X/Y are orthogonal"), Case.Name),
            FMath::Abs(static_cast<double>(FVector::DotProduct(Images[0], Images[1])))
                <= OrthonormalityTolerance);
        TestTrue(
            *FString::Printf(TEXT("%s basis X/Z are orthogonal"), Case.Name),
            FMath::Abs(static_cast<double>(FVector::DotProduct(Images[0], Images[2])))
                <= OrthonormalityTolerance);
        TestTrue(
            *FString::Printf(TEXT("%s basis Y/Z are orthogonal"), Case.Name),
            FMath::Abs(static_cast<double>(FVector::DotProduct(Images[1], Images[2])))
                <= OrthonormalityTolerance);
        const double Determinant = static_cast<double>(FVector::DotProduct(
            Images[0],
            FVector::CrossProduct(Images[1], Images[2])));
        TestTrue(
            *FString::Printf(TEXT("%s rotation determinant is +1"), Case.Name),
            FMath::Abs(Determinant - 1.0) <= OrthonormalityTolerance);
    }

    // Analytic non-commutativity check.  The expected point is calculated from
    // the stated matrices and basis change by hand, rather than from another
    // conversion helper: p=(1,2,3)m -> B p=(1,-2,2)m -> A*B p=(3,3,2)m
    // -> (300,-300,200)cm.
    const FDttCanonicalTransform A = FDttCanonicalTransform::FromAxisAngle(
        FVector3d(1.0, 2.0, 0.0),
        FVector3d(0.0, 0.0, 1.0),
        Pi / 2.0);
    const FDttCanonicalTransform B = FDttCanonicalTransform::FromAxisAngle(
        FVector3d(0.0, 1.0, 0.0),
        FVector3d(1.0, 0.0, 0.0),
        Pi / 2.0);
    const FDttCanonicalTransform AB = A * B;
    const FDttCanonicalTransform BA = B * A;
    TestTrue(
        TEXT("A*B and B*A have different translations"),
        !AB.GetTranslationMetres().Equals(BA.GetTranslationMetres(), 1.0e-9));
    TestFalse(
        TEXT("A*B and B*A have different rotations"),
        QuaternionsWithinAngle(
            AB.GetRotationQuaternion(),
            BA.GetRotationQuaternion(),
            QuaternionAngleToleranceRadians));
    Error.Reset();
    TestTrue(
        TEXT("A*B converts for a non-null point"),
        DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(AB, Unreal, Error));
    TestTrue(
        TEXT("A*B maps the hand-computed point through S and centimetres"),
        Unreal.TransformPosition(FVector(100.0F, -200.0F, 300.0F)).Equals(
            FVector(300.0F, -300.0F, 200.0F),
            static_cast<float>(TranslationCentimetresTolerance)));

    const FDttCanonicalTransform RoundTripInput = FDttCanonicalTransform::FromAxisAngle(
        FVector3d(1.25, -2.5, 3.75),
        FVector3d(1.0, 2.0, 3.0),
        -0.73);
    Error.Reset();
    TestTrue(
        TEXT("non-trivial canonical transform converts for round trip"),
        DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
            RoundTripInput,
            Unreal,
            Error));
    TestTrue(
        TEXT("round-trip example scales metres exactly once"),
        Unreal.GetLocation().Equals(
            FVector(125.0F, 250.0F, 375.0F),
            static_cast<float>(TranslationCentimetresTolerance)));
    TestTrue(
        TEXT("round-trip example keeps unit scale"),
        Unreal.GetScale3D().Equals(FVector::OneVector, static_cast<float>(ScaleTolerance)));
    TestQuaternionIsUnit(*this, TEXT("round-trip Unreal quaternion is unit"), Unreal.GetRotation());

    FDttCanonicalTransform RoundTripOutput;
    Error.Reset();
    TestTrue(
        TEXT("non-trivial Unreal transform converts back"),
        DeferredTeleop::Kinematics::ConvertUnrealToCanonicalTransform(
            Unreal,
            RoundTripOutput,
            Error));
    TestTrue(
        TEXT("round-trip translation remains in metres"),
        RoundTripOutput.GetTranslationMetres().Equals(
            RoundTripInput.GetTranslationMetres(),
            CanonicalTranslationMetresTolerance));
    TestTrue(
        TEXT("round-trip rotation uses a sign-robust angular comparison"),
        QuaternionsWithinAngle(
            RoundTripInput.GetRotationQuaternion(),
            RoundTripOutput.GetRotationQuaternion(),
            QuaternionAngleToleranceRadians));
    return true;
}

} // namespace DeferredTeleop::Tests::KinematicsFrameContract

#endif
