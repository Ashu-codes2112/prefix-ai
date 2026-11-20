"""Synthetic procedure history for fitting and testing the risk model. Made up, not real.

Each record is the prep state of a procedure a day or two before it was due, as the
coordinator dashboard would have seen it, and whether it was then cancelled for a prep reason.
The outcome comes from a hidden process written below. It has interactions and thresholds
the logistic model does not contain (being close to the procedure makes outstanding steps
much worse; colonoscopy prep fails more often with problem replies; more than two problems
barely add more), so the fit is honestly imperfect rather than recovering its own formula.

Until a hospital has its own outcomes, the model shows only how well these signals *could*
rank risk if real cancellations behaved this way. docs/model_report.md says so in its first
lines.
"""

import math
import random
from dataclasses import dataclass

from prefix_api.domain.risk import RiskInputs

PROCEDURES = (
    ("COLONOSCOPY", 0.45),
    ("CT_CONTRAST", 0.2),
    ("MRI", 0.2),
    ("GENERAL_SURGERY", 0.15),
)
SEED = 20261119


@dataclass(frozen=True, slots=True)
class Record:
    inputs: RiskInputs
    cancelled: bool


def _hidden_log_odds(i: RiskInputs) -> float:
    z = -3.1
    soon = 0 <= i.hours_to_procedure <= 24
    z += (0.55 if soon else 0.3) * min(i.unconfirmed_steps, 5)
    z += 0.8 * i.logistics_unconfirmed + (0.7 if soon and i.logistics_unconfirmed else 0.0)
    problems = min(i.problem_replies, 2) + 0.2 * max(0, i.problem_replies - 2)
    z += (1.1 if i.procedure_code == "COLONOSCOPY" else 0.7) * problems
    z += 0.6 * min(i.open_escalations, 2) + 0.9 * i.urgent_escalation
    z += 0.45 * i.on_anticoagulant + 0.35 * i.diabetic_on_insulin + 0.3 * i.on_glp1_agonist
    if i.procedure_code == "CT_CONTRAST":
        z += 1.2 * i.contrast_allergy + (1.0 if i.egfr is not None and i.egfr < 30 else 0.0)
    if i.procedure_code == "MRI":
        z += 1.3 * i.metal_implant
    z += {"COLONOSCOPY": 0.35, "CT_CONTRAST": 0.0, "MRI": -0.2, "GENERAL_SURGERY": 0.1}[
        i.procedure_code
    ]
    return z


def _inputs(rng: random.Random) -> RiskInputs:
    procedure = rng.choices([p for p, _ in PROCEDURES], weights=[w for _, w in PROCEDURES])[0]
    engaged = rng.random() < 0.7  # most patients follow their plan
    unconfirmed = rng.choice([0, 0, 0, 1, 1, 2]) if engaged else rng.choice([1, 2, 3, 4, 5])
    problems = rng.choices([0, 1, 2, 3], weights=[85, 10, 4, 1])[0]
    escalations = rng.choices([0, 1, 2], weights=[88, 10, 2])[0]
    return RiskInputs(
        unconfirmed_steps=unconfirmed,
        logistics_unconfirmed=rng.random() < (0.1 if engaged else 0.45),
        problem_replies=problems,
        open_escalations=escalations,
        urgent_escalation=escalations > 0 and rng.random() < 0.3,
        on_anticoagulant=rng.random() < 0.18,
        diabetic_on_insulin=rng.random() < 0.12,
        on_glp1_agonist=rng.random() < 0.08,
        contrast_allergy=rng.random() < 0.06,
        metal_implant=rng.random() < 0.05,
        egfr=None if rng.random() < 0.4 else round(rng.uniform(15, 110), 1),
        procedure_code=procedure,
        hours_to_procedure=round(rng.uniform(2, 72), 1),
    )


def generate(n: int, *, seed: int = SEED) -> list[Record]:
    # Not for security: a fixed seed makes the synthetic history reproducible.
    rng = random.Random(seed)  # noqa: S311
    records = []
    for _ in range(n):
        inputs = _inputs(rng)
        p = 1 / (1 + math.exp(-_hidden_log_odds(inputs)))
        records.append(Record(inputs=inputs, cancelled=rng.random() < p))
    return records
