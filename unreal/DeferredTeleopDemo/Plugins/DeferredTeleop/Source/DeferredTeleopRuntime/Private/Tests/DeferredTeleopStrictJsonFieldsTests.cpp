#if WITH_DEV_AUTOMATION_TESTS

#include "RobotModel/DeferredTeleopRobotModelTypes.h"

#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

namespace DeferredTeleop::StrictJsonFieldsTests
{
FString RepositoryPath(const TCHAR* RelativePath)
{
    return FPaths::ConvertRelativePathToFull(
        FPaths::ProjectDir() / TEXT("../../") / RelativePath);
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
} // namespace DeferredTeleop::StrictJsonFieldsTests

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopStrictJsonFieldsTest,
    "DeferredTeleop.M2.JsonFieldExactness.NonSchemaObjects",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopStrictJsonFieldsTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    using namespace DeferredTeleop::StrictJsonFieldsTests;

    FString FixtureJson;
    if (!TestTrue(
            TEXT("robot fixture loads"),
            FFileHelper::LoadFileToString(
                FixtureJson,
                *RepositoryPath(TEXT("robots/so101/generated/so101.kinematics.json")))))
    {
        return false;
    }

    struct FCase
    {
        const TCHAR* Name;
        const TCHAR* FirstBefore;
        const TCHAR* FirstAfter;
        bool bExpected;
        bool bAddArraySiblingVariant;
    };

    const FCase Cases[] = {
        {
            TEXT("opaque escaped uppercase key and sibling case variant accept"),
            TEXT("\"visual_id\": \"base_link.visual.0\","),
            TEXT("\"visual_id\": \"base_link.visual.0\",\n"
                 "          \"\\u0055PPER_EXTENSION\": \"braces { and comma, stay in string}\","),
            true,
            true,
        },
        {
            TEXT("opaque exact duplicate with escaped spelling accept"),
            TEXT("\"visual_id\": \"base_link.visual.0\","),
            TEXT("\"visual_id\": \"base_link.visual.0\",\n"
                 "          \"\\u0076isual_id\": \"escaped exact duplicate\","),
            true,
            false,
        },
        {
            TEXT("opaque escaped case collision reject"),
            TEXT("\"visual_id\": \"base_link.visual.0\","),
            TEXT("\"visual_id\": \"base_link.visual.0\",\n"
                 "          \"\\u0056ISUAL_ID\": \"escaped case alias\","),
            false,
            false,
        },
    };

    for (const FCase& Case : Cases)
    {
        FString MutatedJson = FixtureJson;
        const int32 FirstCount = CountExactOccurrences(MutatedJson, Case.FirstBefore);
        if (!TestEqual(
                *FString::Printf(TEXT("%s first anchor is unique"), Case.Name),
                FirstCount,
                1))
        {
            continue;
        }
        MutatedJson = MutatedJson.Replace(
            Case.FirstBefore,
            Case.FirstAfter,
            ESearchCase::CaseSensitive);

        if (Case.bAddArraySiblingVariant)
        {
            const TCHAR* SecondBefore = TEXT("\"visual_id\": \"shoulder_link.visual.0\",");
            const TCHAR* SecondAfter = TEXT("\"visual_id\": \"shoulder_link.visual.0\",\n"
                                            "          \"upper_extension\": \"different object scope\",");
            const int32 SecondCount = CountExactOccurrences(MutatedJson, SecondBefore);
            if (!TestEqual(
                    *FString::Printf(TEXT("%s second anchor is unique"), Case.Name),
                    SecondCount,
                    1))
            {
                continue;
            }
            MutatedJson = MutatedJson.Replace(
                SecondBefore,
                SecondAfter,
                ESearchCase::CaseSensitive);
        }

        FDttRobotDescription Description;
        Description.ModelId = TEXT("sentinel-before-parse");
        FString Error;
        const bool bParsed = DeferredTeleop::RobotModel::ParseRobotDescriptionJson(
            MutatedJson,
            Description,
            Error);
        if (Case.bExpected)
        {
            TestTrue(*FString::Printf(TEXT("%s accepts"), Case.Name), bParsed);
            TestEqual(
                *FString::Printf(TEXT("%s keeps the canonical model"), Case.Name),
                Description.ModelId,
                FString(TEXT("so101_new_calib")));
        }
        else
        {
            TestFalse(*FString::Printf(TEXT("%s rejects"), Case.Name), bParsed);
            TestTrue(
                *FString::Printf(TEXT("%s reports a diagnostic"), Case.Name),
                !Error.IsEmpty());
            TestEqual(
                *FString::Printf(TEXT("%s preserves prior output"), Case.Name),
                Description.ModelId,
                FString(TEXT("sentinel-before-parse")));
        }
    }
    return true;
}

#endif
