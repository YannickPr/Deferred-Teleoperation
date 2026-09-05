#pragma once

#include "Containers/Array.h"
#include "Containers/Set.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonTypes.h"

namespace DeferredTeleop::Json
{
namespace Private
{
struct FJsonScope
{
    EJsonNotation Notation = EJsonNotation::Error;
    // FString's UE key equality and hash are case-insensitive. Keep the first spelling so
    // an exact duplicate remains valid while a distinct case variant can be rejected.
    TSet<FString> FieldNames;
};
}

// FJsonObject may merge field names that differ only by case before a parser can inspect them.
// Scan the reader tokens first, retaining each decoded spelling. Exact duplicate names remain
// valid and keep the existing DOM last-wins behavior; only case-insensitive collisions are rejected.
inline bool ValidateFieldNameSpelling(const FString& Json, FString& OutError)
{
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Json);
    TArray<Private::FJsonScope> Scopes;
    EJsonNotation Notation = EJsonNotation::Error;

    while (Reader->ReadNext(Notation))
    {
        if (Notation == EJsonNotation::Error)
        {
            OutError = Reader->GetErrorMessage();
            return false;
        }

        // For every value token emitted while the current reader scope is an object,
        // GetIdentifier() contains that member's raw decoded field spelling. This check
        // happens before the DOM stores it and can merge case variants.
        if (Scopes.Num() > 0
            && Scopes.Last().Notation == EJsonNotation::ObjectStart
            && Notation != EJsonNotation::ObjectEnd)
        {
            const FString& RawName = Reader->GetIdentifier();
            const FString* ExistingName = Scopes.Last().FieldNames.Find(RawName);
            if (ExistingName != nullptr
                && !RawName.Equals(*ExistingName, ESearchCase::CaseSensitive))
            {
                OutError = FString::Printf(
                    TEXT("JSON field names differ only by case: '%s' and '%s'"),
                    **ExistingName,
                    *RawName);
                return false;
            }
            Scopes.Last().FieldNames.Add(RawName);
        }

        switch (Notation)
        {
        case EJsonNotation::ObjectStart:
        {
            Private::FJsonScope Scope;
            Scope.Notation = EJsonNotation::ObjectStart;
            Scopes.Add(MoveTemp(Scope));
            break;
        }
        case EJsonNotation::ArrayStart:
        {
            Private::FJsonScope Scope;
            Scope.Notation = EJsonNotation::ArrayStart;
            Scopes.Add(MoveTemp(Scope));
            break;
        }
        case EJsonNotation::ObjectEnd:
        case EJsonNotation::ArrayEnd:
        {
            const EJsonNotation ExpectedStart = Notation == EJsonNotation::ObjectEnd
                ? EJsonNotation::ObjectStart
                : EJsonNotation::ArrayStart;
            if (Scopes.Num() == 0 || Scopes.Last().Notation != ExpectedStart)
            {
                OutError = TEXT("JSON scope terminator does not match its opener");
                return false;
            }
            Scopes.Pop();
            break;
        }
        case EJsonNotation::Boolean:
        case EJsonNotation::String:
        case EJsonNotation::Number:
        case EJsonNotation::Null:
            break;
        case EJsonNotation::Error:
            // Handled above; keep the switch exhaustive for UE's enum.
            break;
        }
    }

    if (!Reader->GetErrorMessage().IsEmpty())
    {
        OutError = Reader->GetErrorMessage();
        return false;
    }
    if (Scopes.Num() != 0)
    {
        OutError = TEXT("JSON ended before all scopes were closed");
        return false;
    }
    return true;
}
} // namespace DeferredTeleop::Json
