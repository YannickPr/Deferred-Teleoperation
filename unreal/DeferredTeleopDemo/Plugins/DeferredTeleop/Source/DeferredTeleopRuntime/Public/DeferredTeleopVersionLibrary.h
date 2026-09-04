#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "DeferredTeleopVersionLibrary.generated.h"

UCLASS()
class DEFERREDTELEOPRUNTIME_API UDeferredTeleopVersionLibrary final
    : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintPure, Category = "Deferred Teleoperation|Version")
    static FString GetDeferredTeleopProtocolVersion();
};
