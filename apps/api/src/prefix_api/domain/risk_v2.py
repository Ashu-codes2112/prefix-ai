"""Cancellation risk, version 2: a calibrated logistic regression. Pure functions, no I/O.

The model is a JSON file of numbers, never a pickle: feature means and scales, coefficients,
an intercept, and Platt calibration (``p = sigmoid(a * z + b)``, where ``z`` is the
regression's log-odds). ``prefix_api.ml.train`` fits it; this module only reads it, so the API
needs neither scikit-learn nor numpy.

Each prediction explains itself. A feature's share of the log-odds, compared with an average
procedure, is ``a * coefficient * (value - mean) / scale``. The features that push the risk up
the most are shown with the same plain words as version 1.

The model is fitted on **synthetic** history (``prefix_api.ml.synthetic``) until a hospital has
its own outcomes: docs/model_report.md reports what that does and does not show.
"""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources as package_files
from pathlib import Path

from prefix_api.domain.enums import RiskLevel
from prefix_api.domain.risk import SOON, Factor, RiskInputs, RiskResult

DEFAULT_MODEL = "lr-v2.json"
MAX_FACTORS = 5

# Feature name -> how to read it from the inputs. Training and prediction share this table.
FEATURES: dict[str, Callable[[RiskInputs], float]] = {
    "unconfirmed_steps": lambda i: float(min(i.unconfirmed_steps, 6)),
    "logistics": lambda i: float(i.logistics_unconfirmed),
    "problem_replies": lambda i: float(min(i.problem_replies, 3)),
    "open_escalations": lambda i: float(min(i.open_escalations, 3)),
    "urgent_escalation": lambda i: float(i.urgent_escalation),
    "anticoagulant": lambda i: float(i.on_anticoagulant),
    "insulin": lambda i: float(i.diabetic_on_insulin),
    "glp1": lambda i: float(i.on_glp1_agonist),
    "contrast_allergy": lambda i: float(i.contrast_allergy and i.procedure_code == "CT_CONTRAST"),
    "metal_implant": lambda i: float(i.metal_implant and i.procedure_code == "MRI"),
    "low_egfr": lambda i: float(
        i.egfr is not None and i.egfr < 30 and i.procedure_code == "CT_CONTRAST"
    ),
    "soon": lambda i: float(
        (i.unconfirmed_steps > 0 or i.logistics_unconfirmed) and 0 <= i.hours_to_procedure <= SOON
    ),
    "colonoscopy": lambda i: float(i.procedure_code == "COLONOSCOPY"),
    "contrast_ct": lambda i: float(i.procedure_code == "CT_CONTRAST"),
    "mri": lambda i: float(i.procedure_code == "MRI"),
}


def _label(feature: str, inputs: RiskInputs) -> str:
    n = inputs.unconfirmed_steps
    labels = {
        "unconfirmed_steps": f"{n} prep step{'s' if n != 1 else ''} not yet confirmed",
        "logistics": "Escort or logistics not confirmed",
        "problem_replies": "Reported a problem with a prep step",
        "open_escalations": "Open escalation for a nurse",
        "urgent_escalation": "Urgent escalation (possible red flag)",
        "anticoagulant": "Takes a blood thinner (anticoagulant)",
        "insulin": "Diabetic and on insulin",
        "glp1": "Takes a GLP-1 agonist",
        "contrast_allergy": "Contrast allergy for a contrast scan",
        "metal_implant": "Metal implant for an MRI",
        "low_egfr": "Low kidney function (eGFR under 30) for contrast",
        "soon": "Procedure within 24 hours with steps outstanding",
        "colonoscopy": "Colonoscopy: bowel prep is often incomplete",
        "contrast_ct": "Contrast scan",
        "mri": "MRI",
    }
    return labels[feature]


def feature_vector(inputs: RiskInputs) -> list[float]:
    return [read(inputs) for read in FEATURES.values()]


@dataclass(frozen=True, slots=True)
class LogisticModel:
    version: str
    features: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    calibration_a: float
    calibration_b: float
    # Probability thresholds for the coordinator's levels.
    high_from: float
    medium_from: float

    @classmethod
    def from_json(cls, text: str) -> LogisticModel:
        data = json.loads(text)
        model = cls(
            version=str(data["version"]),
            features=tuple(data["features"]),
            means=tuple(float(v) for v in data["means"]),
            scales=tuple(float(v) for v in data["scales"]),
            coefficients=tuple(float(v) for v in data["coefficients"]),
            intercept=float(data["intercept"]),
            calibration_a=float(data["calibration"]["a"]),
            calibration_b=float(data["calibration"]["b"]),
            high_from=float(data["levels"]["high_from"]),
            medium_from=float(data["levels"]["medium_from"]),
        )
        if model.features != tuple(FEATURES):
            raise ValueError("The model file's features do not match this version of the code")
        n = len(model.features)
        if not (len(model.means) == len(model.scales) == len(model.coefficients) == n):
            raise ValueError("The model file's arrays have different lengths")
        if any(scale <= 0 for scale in model.scales):
            raise ValueError("The model file has a non-positive scale")
        return model

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "features": list(self.features),
                "means": list(self.means),
                "scales": list(self.scales),
                "coefficients": list(self.coefficients),
                "intercept": self.intercept,
                "calibration": {"a": self.calibration_a, "b": self.calibration_b},
                "levels": {"high_from": self.high_from, "medium_from": self.medium_from},
            },
            indent=2,
        )


def load_model(path: Path | None = None) -> LogisticModel:
    """The bundled model, or the file at ``path`` (RISK_MODEL_FILE)."""
    if path is not None:
        return LogisticModel.from_json(path.read_text(encoding="utf-8"))
    bundled = package_files.files("prefix_api.risk_models").joinpath(DEFAULT_MODEL)
    return LogisticModel.from_json(bundled.read_text(encoding="utf-8"))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def probability(model: LogisticModel, inputs: RiskInputs) -> tuple[float, list[float]]:
    """The calibrated probability, and each feature's share of the log-odds."""
    x = feature_vector(inputs)
    standardized = [(v - m) / s for v, m, s in zip(x, model.means, model.scales, strict=True)]
    shares = [
        model.calibration_a * c * z for c, z in zip(model.coefficients, standardized, strict=True)
    ]
    z = model.intercept + sum(c * v for c, v in zip(model.coefficients, standardized, strict=True))
    return _sigmoid(model.calibration_a * z + model.calibration_b), shares


def score(model: LogisticModel, inputs: RiskInputs) -> RiskResult:
    p, shares = probability(model, inputs)
    if p >= model.high_from:
        level = RiskLevel.HIGH
    elif p >= model.medium_from:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW
    # A feature that is present and raises the risk; "points" are its log-odds share x 10.
    present = [
        (share, name)
        for share, name, read in zip(shares, model.features, FEATURES.values(), strict=True)
        if share > 0.05 and read(inputs) > 0
    ]
    present.sort(key=lambda item: (-item[0], item[1]))
    factors = tuple(
        Factor(feature=name, points=max(1, round(share * 10)), label=_label(name, inputs))
        for share, name in present[:MAX_FACTORS]
    )
    return RiskResult(score=min(100, max(0, round(p * 100))), level=level, factors=factors)
