#include "DeferredTeleopRuntime.h"

#include "Modules/ModuleManager.h"

DEFINE_LOG_CATEGORY(LogDeferredTeleop);

void FDeferredTeleopRuntimeModule::StartupModule()
{
    UE_LOG(LogDeferredTeleop, Display, TEXT("DeferredTeleopRuntime dtt/0 loaded"));
}

void FDeferredTeleopRuntimeModule::ShutdownModule()
{
}

IMPLEMENT_MODULE(FDeferredTeleopRuntimeModule, DeferredTeleopRuntime)
