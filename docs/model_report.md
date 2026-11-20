# Cancellation risk model report

Two models score the risk that a procedure is cancelled for a prep reason. **Version 1** (`rules-v1`) is the default: hand-written rules with fixed points. **Version 2** is a calibrated logistic regression fitted on synthetic history, switched on with `RISK_MODEL=v2`. Neither is validated on real outcomes. Neither is for clinical decisions, and neither cancels anything. The dashboard shows which one is in use ("Model: v1 rules" or "Model: v2 ML").

## Version 1: `rules-v1` (`apps/api/src/prefix_api/domain/risk.py`)

**Status:** rules written by hand and not fitted to data. Not validated.

### What it estimates

The risk that a scheduled procedure is cancelled because the patient did not prepare: steps not done, a reported problem, a nurse escalation, or a patient factor that makes the prep harder. It is a priority score for coordinators. It does not predict cancellation and it does not cancel anything.

### How the score is made

Each rule adds points and gives a reason in plain words. The points are added and capped at 100.

| Rule | Points | Reason shown to the coordinator |
| --- | --- | --- |
| Each prep step that needs confirming and is not done | 8 each, up to 32 | "N prep steps not yet confirmed" |
| Escort or logistics step not confirmed | 12 | "Escort or logistics not confirmed" |
| Each problem reply (NO or HELP) on a step | 15 each, up to 30 | "Reported a problem with a prep step" |
| Each open escalation | 20 each, up to 40 | "Open escalation for a nurse" |
| Urgent escalation (possible red flag) | 20 | "Urgent escalation (possible red flag)" |
| Takes an anticoagulant | 10 | "Takes a blood thinner (anticoagulant)" |
| Diabetic on insulin | 8 | "Diabetic and on insulin" |
| Takes a GLP-1 agonist | 5 | "Takes a GLP-1 agonist" |
| Contrast allergy, contrast CT scan | 15 | "Contrast allergy for a contrast scan" |
| Metal implant, MRI | 15 | "Metal implant for an MRI" |
| eGFR under 30, contrast CT scan | 10 | "Low kidney function (eGFR under 30) for contrast" |
| Within 24 hours, with steps outstanding | 10 | "Procedure within 24 hours with steps outstanding" |

Levels: **HIGH** from 50, **MEDIUM** from 25, otherwise **LOW**.

### What a score does

- The coordinator dashboard lists escalated procedures first, then by score, then by readiness.
- A HIGH score opens one task, "High cancellation risk", for the coordinator, due in an hour. It stays open until someone closes it, and no second task is opened while it is open.
- Scores are recomputed every 15 minutes by the worker. Every score is stored with its reasons and the model version, so changes can be traced.
- Nothing is cancelled automatically.

### Limits

- **The weights are judgement, not measurement.** They have not been compared with real cancellations, so they may be wrong in either direction. Treat the ordering as a starting point for a person to check.
- **No calibration.** A score of 50 does not mean a 50% chance of cancellation.
- **Not tested for fairness.** The rules use age-free condition flags and do not use demographics. Their effect across groups has not been measured.
- **Negation and context are not modelled.** The rules read stored facts, such as "on anticoagulant" or "contrast allergy", as written.
- **Readiness is also a signal.** Steps that were confirmed in error (for example by staff on the phone) raise readiness just as a patient's own confirmation would.

<!-- v2:start -->
## Version 2: `lr-v2-synthetic-1` (calibrated logistic regression)

**Fitted on synthetic data.** The history comes from `prefix_api.ml.synthetic`, a made-up process, not a hospital's records. These numbers show how well the model recovers that process. They are not evidence of real-world accuracy. Version 1 stays the default (`RISK_MODEL=v1`) until the model is refitted on the hospital's own outcomes and beats version 1 on them.

- Data: 20,000 synthetic procedures (seed 20261119), split 12,000 train / 4,000 calibration / 4,000 test, stratified. Cancellation rate in the test set: 18.9%.
- Model: standardized features, logistic regression (L2, C = 1), then Platt scaling fitted on the calibration set. The test set is used only for the figures below, through the same code the API runs (`prefix_api.domain.risk_v2`).
- Levels: **HIGH** from 35% predicted probability, **MEDIUM** from 15%.

### Discrimination and calibration (test set)

| Measure | Version 2 | Version 1 rules |
| --- | ---: | ---: |
| AUC (ROC) | 0.789 | 0.772 |
| Brier score (lower is better) | 0.1201 | not a probability |
| Brier score of always predicting the base rate | 0.1530 | |

Calibration: within each band of predicted risk, the share actually cancelled.

| Predicted band | Mean predicted | Observed | Procedures |
| --- | ---: | ---: | ---: |
| 0% to 5% | 3.6% | 4.5% | 552 |
| 5% to 10% | 7.0% | 6.9% | 1,094 |
| 10% to 15% | 12.1% | 10.7% | 580 |
| 15% to 20% | 16.8% | 16.9% | 438 |
| 20% to 30% | 24.1% | 20.3% | 507 |
| 30% to 40% | 34.2% | 37.5% | 301 |
| 40% to 60% | 48.8% | 49.0% | 359 |
| 60% to 100% | 72.8% | 74.6% | 169 |

### Confusion matrices (test set)

**Version 2** (HIGH from 35%):

| | Cancelled | Not cancelled |
| --- | ---: | ---: |
| Flagged HIGH | 352 | 300 |
| Not flagged | 402 | 2946 |

Sensitivity 47%, precision 54%, specificity 91%.

**Version 1 rules** (HIGH from 50 points):

| | Cancelled | Not cancelled |
| --- | ---: | ---: |
| Flagged HIGH | 273 | 206 |
| Not flagged | 481 | 3040 |

Sensitivity 36%, precision 57%, specificity 94%.

### What the model learned

Coefficients per standard deviation of each feature, after calibration, and the odds ratio for one standard deviation more. Explanations on the dashboard list the features that push a procedure's risk up the most, in the same words as version 1.

| Feature | Coefficient | Odds ratio |
| --- | ---: | ---: |
| `unconfirmed_steps` | +0.578 | 1.78 |
| `problem_replies` | +0.494 | 1.64 |
| `logistics` | +0.379 | 1.46 |
| `soon` | +0.307 | 1.36 |
| `open_escalations` | +0.242 | 1.27 |
| `urgent_escalation` | +0.203 | 1.23 |
| `mri` | -0.195 | 0.82 |
| `metal_implant` | +0.187 | 1.21 |
| `colonoscopy` | +0.171 | 1.19 |
| `anticoagulant` | +0.155 | 1.17 |
| `low_egfr` | +0.150 | 1.16 |
| `contrast_allergy` | +0.136 | 1.15 |
| `insulin` | +0.108 | 1.11 |
| `glp1` | +0.095 | 1.10 |
| `contrast_ct` | -0.066 | 0.94 |

### Using and refitting it

- `RISK_MODEL=v2` switches the dashboard, the worker's risk refresh and the HIGH-risk tasks to this model. `RISK_MODEL_FILE` points at another model file. Every stored score records which model made it (`risk_score.model_version`).
- Refit with `uv run python -m prefix_api.ml.train`. It rewrites `apps/api/src/prefix_api/risk_models/lr-v2.json` and this section. For a hospital's own outcomes, replace `generate()` with their exported history, keep the held-out test set, and switch only if version 2 beats version 1 there.

_Generated 2026-11-19 by `prefix_api.ml.train`._
<!-- v2:end -->
