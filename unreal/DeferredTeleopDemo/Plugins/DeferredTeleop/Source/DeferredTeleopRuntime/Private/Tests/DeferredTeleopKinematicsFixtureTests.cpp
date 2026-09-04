#if WITH_DEV_AUTOMATION_TESTS

#include "Kinematics/DeferredTeleopKinematicsLibrary.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace DeferredTeleop::Tests::KinematicsFixtures
{
using FJsonObjectPtr = TSharedPtr<FJsonObject>;
using FJsonValuePtr = TSharedPtr<FJsonValue>;

struct FExpectedMatrix
{
    FString Name;
    double Values[4][4] = {};
};

FString FixturePath()
{
    return FPaths::ConvertRelativePathToFull(
        FPaths::ProjectDir()
        / TEXT("../../fixtures/m2/kinematics/so101-fk.json"));
}

FString ModelPath()
{
    return FPaths::ConvertRelativePathToFull(
        FPaths::ProjectDir()
        / TEXT("../../robots/so101/generated/so101.kinematics.json"));
}

bool ReadObject(
    const FJsonObjectPtr& Parent,
    const TCHAR* Name,
    FJsonObjectPtr& OutObject,
    FString& OutError)
{
    const FJsonValuePtr Value = Parent->TryGetField(FStringView(Name));
    if (!Value.IsValid() || Value->Type != EJson::Object || !Value->AsObject().IsValid())
    {
        OutError = FString::Printf(TEXT("missing object field %s"), Name);
        return false;
    }
    OutObject = Value->AsObject();
    return true;
}

bool ReadString(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    FString& OutValue,
    FString& OutError)
{
    if (!Object->TryGetStringField(FStringView(Name), OutValue) || OutValue.IsEmpty())
    {
        OutError = FString::Printf(TEXT("missing string field %s"), Name);
        return false;
    }
    return true;
}

bool ReadNumberArray(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    int32 ExpectedCount,
    TArray<double>& OutValues,
    FString& OutError)
{
    const TArray<FJsonValuePtr>* Values = nullptr;
    if (!Object->TryGetArrayField(FStringView(Name), Values)
        || Values == nullptr
        || Values->Num() != ExpectedCount)
    {
        OutError = FString::Printf(
            TEXT("field %s must contain %d numbers"),
            Name,
            ExpectedCount);
        return false;
    }
    OutValues.Reset(ExpectedCount);
    for (int32 Index = 0; Index < Values->Num(); ++Index)
    {
        if (!(*Values)[Index].IsValid() || (*Values)[Index]->Type != EJson::Number)
        {
            OutError = FString::Printf(TEXT("field %s contains a non-number"), Name);
            return false;
        }
        const double Number = (*Values)[Index]->AsNumber();
        if (!FMath::IsFinite(Number))
        {
            OutError = FString::Printf(TEXT("field %s contains a non-finite number"), Name);
            return false;
        }
        OutValues.Add(Number);
    }
    return true;
}

bool ReadRootTransform(
    const FJsonObjectPtr& Case,
    FDttCanonicalTransform& OutTransform,
    FString& OutError)
{
    FJsonObjectPtr RootPose;
    if (!ReadObject(Case, TEXT("root_pose"), RootPose, OutError))
    {
        return false;
    }
    TArray<double> Translation;
    TArray<double> Rotation;
    if (!ReadNumberArray(RootPose, TEXT("translation_m"), 3, Translation, OutError)
        || !ReadNumberArray(RootPose, TEXT("rotation_xyzw"), 4, Rotation, OutError))
    {
        return false;
    }
    OutTransform = FDttCanonicalTransform::Identity();
    OutTransform.TranslationMetres.X = Translation[0];
    OutTransform.TranslationMetres.Y = Translation[1];
    OutTransform.TranslationMetres.Z = Translation[2];
    OutTransform.Rotation.X = Rotation[0];
    OutTransform.Rotation.Y = Rotation[1];
    OutTransform.Rotation.Z = Rotation[2];
    OutTransform.Rotation.W = Rotation[3];
    return true;
}

bool ReadJointPositions(
    const FJsonObjectPtr& Case,
    TArray<FDttNamedJointPosition>& OutPositions,
    FString& OutError)
{
    const TArray<FJsonValuePtr>* Values = nullptr;
    if (!Case->TryGetArrayField(FStringView(TEXT("joint_positions_rad")), Values)
        || Values == nullptr)
    {
        OutError = TEXT("case is missing joint_positions_rad");
        return false;
    }
    OutPositions.Reset(Values->Num());
    for (int32 Index = 0; Index < Values->Num(); ++Index)
    {
        const FJsonObjectPtr Entry = (*Values)[Index].IsValid()
            ? (*Values)[Index]->AsObject()
            : nullptr;
        if (!Entry.IsValid())
        {
            OutError = FString::Printf(TEXT("joint position %d is not an object"), Index);
            return false;
        }
        FString Name;
        if (!ReadString(Entry, TEXT("name"), Name, OutError))
        {
            return false;
        }
        double Position = 0.0;
        if (!Entry->TryGetNumberField(FStringView(TEXT("position_rad")), Position)
            || !FMath::IsFinite(Position))
        {
            OutError = FString::Printf(TEXT("joint position %d is not finite"), Index);
            return false;
        }
        FDttNamedJointPosition& Parsed = OutPositions.AddDefaulted_GetRef();
        Parsed.JointName = FName(*Name);
        Parsed.PositionRadians = Position;
    }
    return true;
}

bool ReadMatrix(
    const FJsonObjectPtr& Object,
    double (&OutValues)[4][4],
    FString& OutError)
{
    const TArray<FJsonValuePtr>* Rows = nullptr;
    if (!Object->TryGetArrayField(FStringView(TEXT("matrix")), Rows)
        || Rows == nullptr
        || Rows->Num() != 4)
    {
        OutError = TEXT("expected a 4x4 matrix");
        return false;
    }
    for (int32 Row = 0; Row < 4; ++Row)
    {
        if (!(*Rows)[Row].IsValid() || (*Rows)[Row]->Type != EJson::Array)
        {
            OutError = TEXT("matrix row is not an array");
            return false;
        }
        const TArray<FJsonValuePtr>& Columns = (*Rows)[Row]->AsArray();
        if (Columns.Num() != 4)
        {
            OutError = TEXT("matrix row is not length four");
            return false;
        }
        for (int32 Column = 0; Column < 4; ++Column)
        {
            if (!Columns[Column].IsValid() || Columns[Column]->Type != EJson::Number)
            {
                OutError = TEXT("matrix contains a non-number");
                return false;
            }
            OutValues[Row][Column] = Columns[Column]->AsNumber();
            if (!FMath::IsFinite(OutValues[Row][Column]))
            {
                OutError = TEXT("matrix contains a non-finite number");
                return false;
            }
        }
    }
    return true;
}

bool ReadExpectedMatrices(
    const FJsonObjectPtr& Expected,
    const TCHAR* Field,
    TArray<FExpectedMatrix>& OutMatrices,
    FString& OutError)
{
    const TArray<FJsonValuePtr>* Values = nullptr;
    if (!Expected->TryGetArrayField(FStringView(Field), Values) || Values == nullptr)
    {
        OutError = FString::Printf(TEXT("expected.%s is missing"), Field);
        return false;
    }
    OutMatrices.Reset(Values->Num());
    for (int32 Index = 0; Index < Values->Num(); ++Index)
    {
        const FJsonObjectPtr Entry = (*Values)[Index].IsValid()
            ? (*Values)[Index]->AsObject()
            : nullptr;
        if (!Entry.IsValid())
        {
            OutError = FString::Printf(TEXT("expected.%s[%d] is not an object"), Field, Index);
            return false;
        }
        FExpectedMatrix& Parsed = OutMatrices.AddDefaulted_GetRef();
        if (!ReadString(Entry, TEXT("name"), Parsed.Name, OutError)
            || !ReadMatrix(Entry, Parsed.Values, OutError))
        {
            return false;
        }
    }
    return true;
}

bool LoadJson(const FString& Path, FJsonObjectPtr& OutRoot, FString& OutError)
{
    FString Json;
    if (!FFileHelper::LoadFileToString(Json, *Path))
    {
        OutError = FString::Printf(TEXT("could not load %s"), *Path);
        return false;
    }
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Json);
    if (!FJsonSerializer::Deserialize(Reader, OutRoot) || !OutRoot.IsValid())
    {
        OutError = FString::Printf(TEXT("could not parse %s"), *Path);
        return false;
    }
    return true;
}

bool LoadDescription(FDttRobotDescription& OutDescription, FString& OutError)
{
    FString Json;
    if (!FFileHelper::LoadFileToString(Json, *ModelPath()))
    {
        OutError = TEXT("could not reload robot description");
        return false;
    }
    return DeferredTeleop::RobotModel::ParseRobotDescriptionJson(
        Json,
        OutDescription,
        OutError);
}

void TransformToMatrix(const FDttCanonicalTransform& Transform, double (&OutValues)[4][4])
{
    FQuat4d Quaternion = Transform.GetRotationQuaternion();
    const double Norm = FMath::Sqrt(
        Quaternion.X * Quaternion.X
        + Quaternion.Y * Quaternion.Y
        + Quaternion.Z * Quaternion.Z
        + Quaternion.W * Quaternion.W);
    Quaternion.X /= Norm;
    Quaternion.Y /= Norm;
    Quaternion.Z /= Norm;
    Quaternion.W /= Norm;

    const double XX = Quaternion.X * Quaternion.X;
    const double YY = Quaternion.Y * Quaternion.Y;
    const double ZZ = Quaternion.Z * Quaternion.Z;
    const double XY = Quaternion.X * Quaternion.Y;
    const double XZ = Quaternion.X * Quaternion.Z;
    const double YZ = Quaternion.Y * Quaternion.Z;
    const double WX = Quaternion.W * Quaternion.X;
    const double WY = Quaternion.W * Quaternion.Y;
    const double WZ = Quaternion.W * Quaternion.Z;
    OutValues[0][0] = 1.0 - 2.0 * (YY + ZZ);
    OutValues[0][1] = 2.0 * (XY - WZ);
    OutValues[0][2] = 2.0 * (XZ + WY);
    OutValues[0][3] = Transform.TranslationMetres.X;
    OutValues[1][0] = 2.0 * (XY + WZ);
    OutValues[1][1] = 1.0 - 2.0 * (XX + ZZ);
    OutValues[1][2] = 2.0 * (YZ - WX);
    OutValues[1][3] = Transform.TranslationMetres.Y;
    OutValues[2][0] = 2.0 * (XZ - WY);
    OutValues[2][1] = 2.0 * (YZ + WX);
    OutValues[2][2] = 1.0 - 2.0 * (XX + YY);
    OutValues[2][3] = Transform.TranslationMetres.Z;
    OutValues[3][0] = 0.0;
    OutValues[3][1] = 0.0;
    OutValues[3][2] = 0.0;
    OutValues[3][3] = 1.0;
}

const FDttNamedCanonicalTransform* FindTransform(
    const TArray<FDttNamedCanonicalTransform>& Transforms,
    const FString& Name)
{
    for (const FDttNamedCanonicalTransform& Candidate : Transforms)
    {
        if (Candidate.Name == FName(*Name))
        {
            return &Candidate;
        }
    }
    return nullptr;
}

template <typename TTest>
void ValidateExpectedNames(
    TTest& Test,
    const FString& Prefix,
    const TArray<FExpectedMatrix>& Actual,
    const TArray<FString>& Expected)
{
    TSet<FString> ExpectedNames;
    for (const FString& Name : Expected)
    {
        ExpectedNames.Add(Name);
    }
    TSet<FString> SeenNames;
    Test.TestEqual(*FString::Printf(TEXT("%s cardinality"), *Prefix), Actual.Num(), Expected.Num());
    for (const FExpectedMatrix& Matrix : Actual)
    {
        Test.TestTrue(
            *FString::Printf(TEXT("%s name is non-empty"), *Prefix),
            !Matrix.Name.IsEmpty());
        Test.TestTrue(
            *FString::Printf(TEXT("%s name belongs to the model"), *Prefix),
            ExpectedNames.Contains(Matrix.Name));
        Test.TestFalse(
            *FString::Printf(TEXT("%s names are unique"), *Prefix),
            SeenNames.Contains(Matrix.Name));
        SeenNames.Add(Matrix.Name);
    }
    for (const FString& Name : Expected)
    {
        Test.TestTrue(
            *FString::Printf(TEXT("%s includes expected %s"), *Prefix, *Name),
            SeenNames.Contains(Name));
    }
}

template <typename TTest>
void CompareMatrices(
    TTest& Test,
    const FString& Prefix,
    const TArray<FExpectedMatrix>& Expected,
    const TArray<FDttNamedCanonicalTransform>& Actual,
    double PositionTolerance,
    double RotationTolerance)
{
    Test.TestEqual(*FString::Printf(TEXT("%s count"), *Prefix), Actual.Num(), Expected.Num());
    for (const FExpectedMatrix& ExpectedMatrix : Expected)
    {
        const FDttNamedCanonicalTransform* ActualTransform =
            FindTransform(Actual, ExpectedMatrix.Name);
        Test.TestTrue(
            *FString::Printf(TEXT("%s contains %s"), *Prefix, *ExpectedMatrix.Name),
            ActualTransform != nullptr);
        if (ActualTransform == nullptr)
        {
            continue;
        }
        double ActualMatrix[4][4] = {};
        TransformToMatrix(ActualTransform->Transform, ActualMatrix);
        for (int32 Row = 0; Row < 4; ++Row)
        {
            for (int32 Column = 0; Column < 4; ++Column)
            {
                const double Tolerance = Row < 3 && Column == 3
                    ? PositionTolerance
                    : RotationTolerance;
                Test.TestTrue(
                    *FString::Printf(
                        TEXT("%s %s matrix[%d][%d] within tolerance"),
                        *Prefix,
                        *ExpectedMatrix.Name,
                        Row,
                        Column),
                    FMath::Abs(ActualMatrix[Row][Column] - ExpectedMatrix.Values[Row][Column])
                        <= Tolerance);
            }
        }
    }
}
} // namespace DeferredTeleop::Tests::KinematicsFixtures

namespace DeferredTeleop::Tests::KinematicsFixtures
{

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopSO101KinematicsFixtureTest,
    "DeferredTeleop.M2.Kinematics.CrossLanguageSO101Fixtures",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopSO101KinematicsFixtureTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FJsonObjectPtr Fixture;
    FString Error;
    if (!TestTrue(TEXT("SO-101 fixture loads"), LoadJson(FixturePath(), Fixture, Error)))
    {
        AddError(Error);
        return false;
    }

    FString FixtureSchemaVersion;
    if (!ReadString(Fixture, TEXT("schema_version"), FixtureSchemaVersion, Error))
    {
        AddError(Error);
        return false;
    }
    TestEqual(
        TEXT("fixture uses the supported fixture schema"),
        FixtureSchemaVersion,
        FString(TEXT("dtt.kinematics-fixtures/0")));

    FDttRobotDescription Description;
    if (!TestTrue(TEXT("generated SO-101 description loads"), LoadDescription(Description, Error)))
    {
        AddError(Error);
        return false;
    }

    FJsonObjectPtr ModelMetadata;
    if (!TestTrue(
            TEXT("fixture contains model metadata"),
            ReadObject(Fixture, TEXT("model"), ModelMetadata, Error)))
    {
        AddError(Error);
        return false;
    }
    FString FixtureModelId;
    FString FixtureModelRevision;
    if (!ReadString(ModelMetadata, TEXT("model_id"), FixtureModelId, Error)
        || !ReadString(ModelMetadata, TEXT("model_revision"), FixtureModelRevision, Error))
    {
        AddError(Error);
        return false;
    }
    TestEqual(TEXT("fixture binds the generated model id"), Description.ModelId, FixtureModelId);
    TestEqual(
        TEXT("fixture binds the generated model revision"),
        Description.ModelRevision,
        FixtureModelRevision);

    FJsonObjectPtr Tolerances;
    if (!TestTrue(
            TEXT("fixture contains separate tolerances"),
            ReadObject(Fixture, TEXT("tolerances"), Tolerances, Error)))
    {
        AddError(Error);
        return false;
    }
    double PositionTolerance = 0.0;
    double RotationTolerance = 0.0;
    if (!Tolerances->TryGetNumberField(FStringView(TEXT("position_m")), PositionTolerance)
        || !Tolerances->TryGetNumberField(FStringView(TEXT("rotation")), RotationTolerance)
        || PositionTolerance <= 0.0
        || RotationTolerance <= 0.0)
    {
        AddError(TEXT("fixture tolerances are invalid"));
        return false;
    }

    const TArray<FJsonValuePtr>* Cases = nullptr;
    if (!TestTrue(
            TEXT("fixture contains cases"),
            Fixture->TryGetArrayField(FStringView(TEXT("cases")), Cases)
                && Cases != nullptr
                && Cases->Num() > 0))
    {
        return false;
    }

    const TArray<FString> ExpectedCaseIds = {
        TEXT("zero"),
        TEXT("shoulder_pan_only"),
        TEXT("shoulder_and_elbow"),
        TEXT("multi_joint_nonsymmetric"),
        TEXT("joint_limits_lower"),
        TEXT("joint_limits_upper"),
        TEXT("tool_fixed"),
        TEXT("root_transform_noncommuting"),
        TEXT("reordered_joint_positions"),
    };
    TSet<FString> ExpectedCaseIdSet;
    for (const FString& CaseId : ExpectedCaseIds)
    {
        ExpectedCaseIdSet.Add(CaseId);
    }
    TestEqual(
        TEXT("fixture has the complete case cardinality"),
        Cases->Num(),
        ExpectedCaseIds.Num());
    TSet<FString> SeenCaseIds;

    TArray<FString> ExpectedLinkNames;
    for (const FDttRobotLinkDescription& Link : Description.Links)
    {
        ExpectedLinkNames.Add(Link.Name.ToString());
    }
    TArray<FString> ExpectedToolNames;
    for (const FDttRobotToolFrameDescription& Tool : Description.ToolFrames)
    {
        ExpectedToolNames.Add(Tool.Name.ToString());
    }

    for (int32 CaseIndex = 0; CaseIndex < Cases->Num(); ++CaseIndex)
    {
        const FJsonObjectPtr Case = (*Cases)[CaseIndex].IsValid()
            ? (*Cases)[CaseIndex]->AsObject()
            : nullptr;
        if (!TestTrue(
                *FString::Printf(TEXT("case %d is an object"), CaseIndex),
                Case.IsValid()))
        {
            continue;
        }
        FString CaseId;
        if (!ReadString(Case, TEXT("id"), CaseId, Error))
        {
            AddError(Error);
            continue;
        }
        TestTrue(
            *FString::Printf(TEXT("case %s has a known id"), *CaseId),
            ExpectedCaseIdSet.Contains(CaseId));
        TestFalse(
            *FString::Printf(TEXT("case %s id is unique"), *CaseId),
            SeenCaseIds.Contains(CaseId));
        SeenCaseIds.Add(CaseId);
        FDttCanonicalTransform RootTransform;
        TArray<FDttNamedJointPosition> JointPositions;
        FJsonObjectPtr ExpectedObject;
        if (!ReadRootTransform(Case, RootTransform, Error)
            || !ReadJointPositions(Case, JointPositions, Error)
            || !ReadObject(Case, TEXT("expected"), ExpectedObject, Error))
        {
            AddError(FString::Printf(TEXT("%s: %s"), *CaseId, *Error));
            continue;
        }

        FDttRobotDescription CaseDescription = Description;
        if (CaseId == TEXT("reordered_joint_positions"))
        {
            for (int32 Left = 0, Right = CaseDescription.Joints.Num() - 1; Left < Right; ++Left, --Right)
            {
                CaseDescription.Joints.Swap(Left, Right);
            }
        }

        FDttForwardKinematicsResult Result;
        const bool bEvaluated = DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            CaseDescription,
            RootTransform,
            JointPositions,
            Result);
        if (!TestTrue(
                *FString::Printf(TEXT("%s evaluates through production FK"), *CaseId),
                bEvaluated && Result.bSuccess))
        {
            AddError(FString::Printf(TEXT("%s: %s"), *CaseId, *Result.ErrorMessage));
            continue;
        }
        TestTrue(
            *FString::Printf(TEXT("%s remains within its declared limit edges"), *CaseId),
            Result.bWithinJointLimits);

        TArray<FExpectedMatrix> ExpectedLinks;
        TArray<FExpectedMatrix> ExpectedTools;
        if (!ReadExpectedMatrices(ExpectedObject, TEXT("links"), ExpectedLinks, Error)
            || !ReadExpectedMatrices(ExpectedObject, TEXT("tools"), ExpectedTools, Error))
        {
            AddError(FString::Printf(TEXT("%s: %s"), *CaseId, *Error));
            continue;
        }
        ValidateExpectedNames(
            *this,
            FString::Printf(TEXT("%s link names"), *CaseId),
            ExpectedLinks,
            ExpectedLinkNames);
        ValidateExpectedNames(
            *this,
            FString::Printf(TEXT("%s tool names"), *CaseId),
            ExpectedTools,
            ExpectedToolNames);
        CompareMatrices(
            *this,
            FString::Printf(TEXT("%s links"), *CaseId),
            ExpectedLinks,
            Result.LinkTransforms,
            PositionTolerance,
            RotationTolerance);
        CompareMatrices(
            *this,
            FString::Printf(TEXT("%s tools"), *CaseId),
            ExpectedTools,
            Result.ToolTransforms,
            PositionTolerance,
            RotationTolerance);
    }
    for (const FString& CaseId : ExpectedCaseIds)
    {
        TestTrue(
            *FString::Printf(TEXT("fixture includes case %s"), *CaseId),
            SeenCaseIds.Contains(CaseId));
    }
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopSO101KinematicsFixtureInputValidationTest,
    "DeferredTeleop.M2.Kinematics.CrossLanguageFixtureInputValidation",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopSO101KinematicsFixtureInputValidationTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FDttRobotDescription Description;
    FString Error;
    if (!TestTrue(TEXT("generated SO-101 description loads"), LoadDescription(Description, Error)))
    {
        AddError(Error);
        return false;
    }
    TArray<FDttNamedJointPosition> Valid;
    for (const TCHAR* Name : {
             TEXT("shoulder_pan"),
             TEXT("shoulder_lift"),
             TEXT("elbow_flex"),
             TEXT("wrist_flex"),
             TEXT("wrist_roll"),
             TEXT("gripper")})
    {
        FDttNamedJointPosition& Position = Valid.AddDefaulted_GetRef();
        Position.JointName = FName(Name);
        Position.PositionRadians = 0.0;
    }

    FDttForwardKinematicsResult Result;
    TArray<FDttNamedJointPosition> Unknown = Valid;
    Unknown[0].JointName = FName(TEXT("unknown_joint"));
    TestFalse(
        TEXT("unknown named position is rejected"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            FDttCanonicalTransform::Identity(),
            Unknown,
            Result));
    TestTrue(TEXT("unknown error names the input"), Result.ErrorMessage.Contains(TEXT("unknown joint")));

    TArray<FDttNamedJointPosition> Duplicate = Valid;
    Duplicate.Add(Valid[0]);
    TestFalse(
        TEXT("duplicate named position is rejected"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            FDttCanonicalTransform::Identity(),
            Duplicate,
            Result));
    TestTrue(TEXT("duplicate error names the input"), Result.ErrorMessage.Contains(TEXT("duplicate joint")));

    TArray<FDttNamedJointPosition> Missing = Valid;
    Missing.RemoveAt(Missing.Num() - 1);
    TestFalse(
        TEXT("missing named position is rejected"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            FDttCanonicalTransform::Identity(),
            Missing,
            Result));
    TestTrue(TEXT("missing error names the input"), Result.ErrorMessage.Contains(TEXT("missing joint")));
    return true;
}

} // namespace DeferredTeleop::Tests::KinematicsFixtures

#endif
