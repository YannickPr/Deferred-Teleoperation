#if WITH_DEV_AUTOMATION_TESTS

#include "Articulated/DeferredTeleopArticulatedViewParser.h"
#include "DeferredTeleopMissionViewParser.h"
#include "Kinematics/DeferredTeleopKinematicsLibrary.h"
#include "Dom/JsonObject.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace DeferredTeleop::JsonFieldExactnessTests
{
using FJsonObjectPtr = TSharedPtr<FJsonObject>;
using FJsonValuePtr = TSharedPtr<FJsonValue>;

FString RepositoryPath(const TCHAR* RelativePath)
{
    return FPaths::ConvertRelativePathToFull(
        FPaths::ProjectDir() / TEXT("../../") / RelativePath);
}

bool LoadText(const FString& Path, FString& OutText)
{
    return FFileHelper::LoadFileToString(OutText, *Path);
}

int32 CountExactOccurrences(const FString& Text, const FString& Needle)
{
    int32 Count = 0;
    int32 SearchFrom = 0;
    for (;;)
    {
        const int32 FoundAt = Text.Find(
            *Needle,
            ESearchCase::CaseSensitive,
            ESearchDir::FromStart,
            SearchFrom);
        if (FoundAt == INDEX_NONE)
        {
            return Count;
        }
        ++Count;
        SearchFrom = FoundAt + Needle.Len();
    }
}

bool ReadString(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    FString& OutValue,
    FString& OutError)
{
    if (!Object.IsValid() || !Object->TryGetStringField(FStringView(Name), OutValue))
    {
        OutError = FString::Printf(TEXT("manifest field '%s' must be a string"), Name);
        return false;
    }
    return true;
}

bool ParseJsonFieldCase(
    const FString& Parser,
    const FString& Json,
    FString& OutValue,
    FString& OutError)
{
    if (Parser == TEXT("mission_view"))
    {
        FDeferredTeleopMissionViewState State;
        State.SourceId = TEXT("sentinel-before-parse");
        const bool bParsed = DeferredTeleop::MissionView::Parse(Json, State, OutError);
        OutValue = State.SourceId;
        return bParsed;
    }

    if (Parser == TEXT("robot_model"))
    {
        FDttRobotDescription Description;
        Description.ModelId = TEXT("sentinel-before-parse");
        const bool bParsed = DeferredTeleop::RobotModel::ParseRobotDescriptionJson(
            Json,
            Description,
            OutError);
        OutValue = Description.ModelId;
        return bParsed;
    }

    if (Parser == TEXT("articulated_view"))
    {
        FDeferredTeleopArticulatedViewState State;
        State.SourceId = TEXT("sentinel-before-parse");
        const bool bParsed = DeferredTeleop::ArticulatedView::ParseArticulated(
            Json,
            State,
            OutError);
        OutValue = State.bHasConfirmedRobotState
            ? State.ConfirmedRobotState.ModelReference.ModelId
            : State.SourceId;
        return bParsed;
    }

    OutError = FString::Printf(TEXT("unsupported parser '%s'"), *Parser);
    return false;
}
} // namespace DeferredTeleop::JsonFieldExactnessTests

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopJsonFieldExactnessTest,
    "DeferredTeleop.M2.JsonFieldExactness.SharedMatrix",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopJsonFieldExactnessTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    using namespace DeferredTeleop::JsonFieldExactnessTests;

    FString ManifestJson;
    if (!TestTrue(
            TEXT("shared exactness manifest loads"),
            LoadText(RepositoryPath(TEXT("fixtures/m2/json-field-exactness.json")), ManifestJson)))
    {
        return false;
    }

    FJsonObjectPtr Manifest;
    const TSharedRef<TJsonReader<>> ManifestReader = TJsonReaderFactory<>::Create(ManifestJson);
    if (!TestTrue(
            TEXT("shared exactness manifest parses"),
            FJsonSerializer::Deserialize(ManifestReader, Manifest) && Manifest.IsValid()))
    {
        return false;
    }

    FString SchemaVersion;
    FString ManifestError;
    if (!TestTrue(
            TEXT("shared exactness manifest has its declared schema"),
            ReadString(Manifest, TEXT("schema_version"), SchemaVersion, ManifestError)))
    {
        return false;
    }
    if (!TestEqual(
            TEXT("shared exactness manifest schema is current"),
            SchemaVersion,
            FString(TEXT("dtt.json-field-exactness/0"))))
    {
        return false;
    }

    const TArray<FJsonValuePtr>* Cases = nullptr;
    if (!TestTrue(
            TEXT("shared exactness manifest has cases"),
            Manifest->TryGetArrayField(FStringView(TEXT("cases")), Cases)
                && Cases != nullptr))
    {
        return false;
    }

    for (int32 Index = 0; Index < Cases->Num(); ++Index)
    {
        const FJsonObjectPtr Case = (*Cases)[Index].IsValid()
            ? (*Cases)[Index]->AsObject()
            : nullptr;
        if (!TestTrue(
                *FString::Printf(TEXT("matrix case %d is an object"), Index),
                Case.IsValid()))
        {
            continue;
        }

        FString Name;
        FString Parser;
        FString Fixture;
        FString Before;
        FString After;
        FString Expected;
        if (!ReadString(Case, TEXT("name"), Name, ManifestError)
            || !ReadString(Case, TEXT("parser"), Parser, ManifestError)
            || !ReadString(Case, TEXT("fixture"), Fixture, ManifestError)
            || !ReadString(Case, TEXT("before"), Before, ManifestError)
            || !ReadString(Case, TEXT("after"), After, ManifestError)
            || !ReadString(Case, TEXT("expected"), Expected, ManifestError))
        {
            AddError(FString::Printf(TEXT("matrix case %d is missing a required field"), Index));
            continue;
        }

        FString FixtureJson;
        if (!TestTrue(
                *FString::Printf(TEXT("%s fixture loads"), *Name),
                LoadText(RepositoryPath(*Fixture), FixtureJson)))
        {
            continue;
        }

        const int32 MatchCount = CountExactOccurrences(FixtureJson, Before);
        if (!TestEqual(
                *FString::Printf(TEXT("%s has one exact mutation anchor"), *Name),
                MatchCount,
                1))
        {
            continue;
        }

        const FString MutatedJson = FixtureJson.Replace(
            *Before,
            *After,
            ESearchCase::CaseSensitive);
        if (!TestTrue(
                *FString::Printf(TEXT("%s mutation changes the fixture bytes"), *Name),
                MutatedJson.Compare(FixtureJson, ESearchCase::CaseSensitive) != 0))
        {
            continue;
        }

        FString Error;
        FString ParsedValue;
        const bool bParsed = ParseJsonFieldCase(Parser, MutatedJson, ParsedValue, Error);
        if (Expected == TEXT("REJECT"))
        {
            TestFalse(*FString::Printf(TEXT("%s rejects"), *Name), bParsed);
            TestTrue(
                *FString::Printf(TEXT("%s reports a rejection diagnostic"), *Name),
                !Error.IsEmpty());
            TestEqual(
                *FString::Printf(TEXT("%s keeps the prior parsed value"), *Name),
                ParsedValue,
                FString(TEXT("sentinel-before-parse")));
        }
        else if (Expected == TEXT("ACCEPT"))
        {
            TestTrue(*FString::Printf(TEXT("%s accepts"), *Name), bParsed);
            FString ExpectedValue;
            if (Case->TryGetStringField(FStringView(TEXT("value")), ExpectedValue))
            {
                TestEqual(
                    *FString::Printf(TEXT("%s applies exact-duplicate last-wins"), *Name),
                    ParsedValue,
                    ExpectedValue);
            }
        }
        else
        {
            AddError(FString::Printf(TEXT("%s declares unsupported outcome %s"), *Name, *Expected));
        }
    }
    return true;
}

#endif
