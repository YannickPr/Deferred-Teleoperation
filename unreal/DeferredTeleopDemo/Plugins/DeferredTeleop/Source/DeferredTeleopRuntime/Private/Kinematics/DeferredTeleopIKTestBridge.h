#pragma once

#if WITH_DEV_AUTOMATION_TESTS

#include "Kinematics/DeferredTeleopIKLibrary.h"

namespace DeferredTeleop::Kinematics::IKTestBridge
{
/**
 * Test-only view of the task rows produced by the private DLS Jacobian path.
 * This header is private and is not compiled into the runtime API surface.
 */
struct FDeferredTeleopIKTestJacobian
{
    TArray<FName> ActiveJointNames;
    TArray<double> TaskError;
    /** Columns are in ActiveJointNames order; each column has 3 or 5 rows. */
    TArray<TArray<double>> Jacobian;
    FVector3d CurrentApproachAxis = FVector3d::ZeroVector;
    FVector3d TargetApproachAxis = FVector3d::ZeroVector;
    FVector3d ApproachBasisU = FVector3d::ZeroVector;
    FVector3d ApproachBasisV = FVector3d::ZeroVector;
    double PositionResidualMetres = 0.0;
    double ApproachResidualRadians = 0.0;
};

/**
 * Synthetic candidate values used only to exercise the production
 * convergence-priority policy.  The production solver has already evaluated
 * these residuals and cost before invoking the policy helper.
 */
struct FDeferredTeleopIKTestCandidate
{
    TArray<double> JointValues;
    double PositionResidualMetres = 0.0;
    double ApproachResidualRadians = 0.0;
    double WeightedCost = 0.0;
};

struct FDeferredTeleopIKTestCandidateSelection
{
    TArray<double> SelectedJointValues;
    bool bHasCandidate = false;
    bool bConvergedCandidate = false;
    bool bAcceptedCandidate = false;
    double SelectedPositionResidualMetres = 0.0;
    double SelectedApproachResidualRadians = 0.0;
    double SelectedWeightedCost = 0.0;
};

/**
 * Rebuild private snapshots from already evaluated tool transforms and call
 * the production task evaluation and Jacobian helpers without duplicating
 * their numerical implementation.
 */
bool BuildTaskJacobianForTest(
    const FDttRobotDescription& Description,
    const FDttIKRequest& Request,
    const FDttIKSettings& Settings,
    const FDttCanonicalTransform& CurrentToolTransform,
    const TArray<FDttCanonicalTransform>& PlusToolTransforms,
    const TArray<FDttCanonicalTransform>& MinusToolTransforms,
    FDeferredTeleopIKTestJacobian& OutResult,
    FString& OutError);

/** Call the same candidate-selection policy used by the production loop. */
bool EvaluateCandidateSelectionPolicyForTest(
    const FDttIKRequest& Request,
    const FDttIKSettings& Settings,
    const FDeferredTeleopIKTestCandidate& ExistingCandidate,
    const FDeferredTeleopIKTestCandidate& Candidate,
    FDeferredTeleopIKTestCandidateSelection& OutResult,
    FString& OutError);
} // namespace DeferredTeleop::Kinematics::IKTestBridge

#endif // WITH_DEV_AUTOMATION_TESTS
