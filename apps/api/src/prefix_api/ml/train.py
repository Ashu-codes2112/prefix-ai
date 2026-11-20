"""Fit risk model v2 and write its report. Development only: needs the dev dependencies.

    uv run python -m prefix_api.ml.train                  # 20,000 synthetic procedures
    uv run python -m prefix_api.ml.train --n 50000 --seed 7

1. Generate synthetic history (prefix_api.ml.synthetic), split 60/20/20 into train,
   calibration and test, stratified by outcome.
2. Standardize on the training part, fit a logistic regression (scikit-learn).
3. Calibrate on the calibration part with Platt scaling: a one-feature logistic regression on
   the model's log-odds.
4. Measure on the test part, which neither step saw, through the same pure-Python
   prediction the API uses (prefix_api.domain.risk_v2): AUC, Brier score, a calibration
   table and a confusion matrix, next to version 1's rules on the same records.
5. Write the model file (prefix_api/risk_models/lr-v2.json) and the v2 part of
   docs/model_report.md.
"""

import argparse
import datetime as dt
import itertools
from dataclasses import dataclass
from importlib import resources as package_files
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.metrics import brier_score_loss, roc_auc_score  # type: ignore[import-untyped]
from sklearn.model_selection import train_test_split  # type: ignore[import-untyped]

from prefix_api.domain import risk as v1
from prefix_api.domain.enums import RiskLevel
from prefix_api.domain.risk_v2 import FEATURES, LogisticModel, feature_vector, probability
from prefix_api.ml.synthetic import SEED, Record, generate

HIGH_FROM = 0.35
MEDIUM_FROM = 0.15
REPORT_START = "<!-- v2:start -->"
REPORT_END = "<!-- v2:end -->"
REPO = Path(__file__).resolve().parents[5]


@dataclass(frozen=True, slots=True)
class Evaluation:
    n_train: int
    n_calibration: int
    n_test: int
    base_rate: float
    auc_v2: float
    auc_v1: float
    brier_v2: float
    brier_base: float
    # (band low, band high, mean predicted, observed rate, procedures)
    calibration: list[tuple[float, float, float, float, int]]
    confusion_v2: tuple[int, int, int, int]  # tp, fp, fn, tn
    confusion_v1: tuple[int, int, int, int]


def fit(records: list[Record], *, seed: int, version: str) -> tuple[LogisticModel, Evaluation]:
    x = np.array([feature_vector(r.inputs) for r in records], dtype=float)
    y = np.array([r.cancelled for r in records], dtype=int)
    idx = np.arange(len(records))
    train_idx, rest = train_test_split(idx, test_size=0.4, stratify=y, random_state=seed)
    cal_idx, test_idx = train_test_split(rest, test_size=0.5, stratify=y[rest], random_state=seed)

    means = x[train_idx].mean(axis=0)
    scales = x[train_idx].std(axis=0)
    scales[scales == 0] = 1.0
    standardized = (x - means) / scales

    regression = LogisticRegression(C=1.0, max_iter=2000)
    regression.fit(standardized[train_idx], y[train_idx])
    platt = LogisticRegression(C=1e6, max_iter=2000)
    platt.fit(regression.decision_function(standardized[cal_idx]).reshape(-1, 1), y[cal_idx])

    model = LogisticModel(
        version=version,
        features=tuple(FEATURES),
        means=tuple(round(float(v), 6) for v in means),
        scales=tuple(round(float(v), 6) for v in scales),
        coefficients=tuple(round(float(v), 6) for v in regression.coef_[0]),
        intercept=round(float(regression.intercept_[0]), 6),
        calibration_a=round(float(platt.coef_[0][0]), 6),
        calibration_b=round(float(platt.intercept_[0]), 6),
        high_from=HIGH_FROM,
        medium_from=MEDIUM_FROM,
    )
    return model, evaluate(model, [records[i] for i in test_idx], len(train_idx), len(cal_idx))


def _confusion(predicted: list[bool], actual: list[bool]) -> tuple[int, int, int, int]:
    tp = sum(p and a for p, a in zip(predicted, actual, strict=True))
    fp = sum(p and not a for p, a in zip(predicted, actual, strict=True))
    fn = sum(a and not p for p, a in zip(predicted, actual, strict=True))
    return tp, fp, fn, len(actual) - tp - fp - fn


def evaluate(
    model: LogisticModel, test: list[Record], n_train: int, n_calibration: int
) -> Evaluation:
    actual = [r.cancelled for r in test]
    p_v2 = [probability(model, r.inputs)[0] for r in test]
    rules = [v1.score(r.inputs) for r in test]
    base = sum(actual) / len(actual)
    bins: list[tuple[float, float, float, float, int]] = []
    edges = [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.6, 1.0]
    for low, high in itertools.pairwise(edges):
        members = [i for i, p in enumerate(p_v2) if low <= p < high or (high == 1.0 and p == 1.0)]
        if members:
            bins.append(
                (
                    low,
                    high,
                    sum(p_v2[i] for i in members) / len(members),
                    sum(actual[i] for i in members) / len(members),
                    len(members),
                )
            )
    return Evaluation(
        n_train=n_train,
        n_calibration=n_calibration,
        n_test=len(test),
        base_rate=base,
        auc_v2=float(roc_auc_score(actual, p_v2)),
        auc_v1=float(roc_auc_score(actual, [r.score for r in rules])),
        brier_v2=float(brier_score_loss(actual, p_v2)),
        brier_base=float(brier_score_loss(actual, [base] * len(actual))),
        calibration=bins,
        confusion_v2=_confusion([p >= model.high_from for p in p_v2], actual),
        confusion_v1=_confusion([r.level is RiskLevel.HIGH for r in rules], actual),
    )


def _matrix(name: str, threshold: str, counts: tuple[int, int, int, int]) -> list[str]:
    tp, fp, fn, tn = counts
    sensitivity = tp / (tp + fn) if tp + fn else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    return [
        f"**{name}** (HIGH from {threshold}):",
        "",
        "| | Cancelled | Not cancelled |",
        "| --- | ---: | ---: |",
        f"| Flagged HIGH | {tp} | {fp} |",
        f"| Not flagged | {fn} | {tn} |",
        "",
        f"Sensitivity {sensitivity:.0%}, precision {precision:.0%}, specificity {specificity:.0%}.",
        "",
    ]


def report(model: LogisticModel, ev: Evaluation, *, n: int, seed: int) -> str:
    lines = [
        REPORT_START,
        f"## Version 2: `{model.version}` (calibrated logistic regression)",
        "",
        "**Fitted on synthetic data.** The history comes from `prefix_api.ml.synthetic`, a made-up "
        "process, not a hospital's records. These numbers show how well the model recovers that "
        "process. They are not evidence of real-world accuracy. Version 1 stays the default "
        "(`RISK_MODEL=v1`) until the model is refitted on the hospital's own outcomes and beats "
        "version 1 on them.",
        "",
        f"- Data: {n:,} synthetic procedures (seed {seed}), split {ev.n_train:,} train / "
        f"{ev.n_calibration:,} calibration / {ev.n_test:,} test, stratified. Cancellation rate "
        f"in the test set: {ev.base_rate:.1%}.",
        "- Model: standardized features, logistic regression (L2, C = 1), then Platt scaling "
        "fitted on the calibration set. The test set is used only for the figures below, through "
        "the same code the API runs (`prefix_api.domain.risk_v2`).",
        f"- Levels: **HIGH** from {model.high_from:.0%} predicted probability, **MEDIUM** from "
        f"{model.medium_from:.0%}.",
        "",
        "### Discrimination and calibration (test set)",
        "",
        "| Measure | Version 2 | Version 1 rules |",
        "| --- | ---: | ---: |",
        f"| AUC (ROC) | {ev.auc_v2:.3f} | {ev.auc_v1:.3f} |",
        f"| Brier score (lower is better) | {ev.brier_v2:.4f} | not a probability |",
        f"| Brier score of always predicting the base rate | {ev.brier_base:.4f} | |",
        "",
        "Calibration: within each band of predicted risk, the share actually cancelled.",
        "",
        "| Predicted band | Mean predicted | Observed | Procedures |",
        "| --- | ---: | ---: | ---: |",
        *[
            f"| {low:.0%} to {high:.0%} | {pred:.1%} | {obs:.1%} | {count:,} |"
            for low, high, pred, obs, count in ev.calibration
        ],
        "",
        "### Confusion matrices (test set)",
        "",
        *_matrix("Version 2", f"{model.high_from:.0%}", ev.confusion_v2),
        *_matrix("Version 1 rules", f"{v1.HIGH_FROM} points", ev.confusion_v1),
        "### What the model learned",
        "",
        "Coefficients per standard deviation of each feature, after calibration, and the odds "
        "ratio for one standard deviation more. Explanations on the dashboard list the features "
        "that push a procedure's risk up the most, in the same words as version 1.",
        "",
        "| Feature | Coefficient | Odds ratio |",
        "| --- | ---: | ---: |",
        *[
            f"| `{name}` | {model.calibration_a * coef:+.3f} | "
            f"{float(np.exp(model.calibration_a * coef)):.2f} |"
            for name, coef in sorted(
                zip(model.features, model.coefficients, strict=True), key=lambda item: -abs(item[1])
            )
        ],
        "",
        "### Using and refitting it",
        "",
        "- `RISK_MODEL=v2` switches the dashboard, the worker's risk refresh and the HIGH-risk "
        "tasks to this model. `RISK_MODEL_FILE` points at another model file. Every stored score "
        "records which model made it (`risk_score.model_version`).",
        "- Refit with `uv run python -m prefix_api.ml.train`. It rewrites "
        "`apps/api/src/prefix_api/risk_models/lr-v2.json` and this section. For a hospital's own "
        "outcomes, replace `generate()` with their exported history, keep the held-out test set, "
        "and switch only if version 2 beats version 1 there.",
        "",
        f"_Generated {dt.datetime.now(dt.UTC).date().isoformat()} by `prefix_api.ml.train`._",
        REPORT_END,
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fit risk model v2 on synthetic history.")
    parser.add_argument("--n", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--version", default="lr-v2-synthetic-1")
    parser.add_argument("--no-write", action="store_true", help="Print the figures only")
    args = parser.parse_args()

    model, ev = fit(generate(args.n, seed=args.seed), seed=args.seed, version=args.version)
    print(  # noqa: T201
        f"AUC v2 {ev.auc_v2:.3f} vs v1 {ev.auc_v1:.3f}; Brier {ev.brier_v2:.4f} "
        f"(base {ev.brier_base:.4f}); test n={ev.n_test}"
    )
    if args.no_write:
        return
    model_file = Path(str(package_files.files("prefix_api.risk_models").joinpath("lr-v2.json")))
    model_file.write_text(model.to_json() + "\n", encoding="utf-8")
    report_file = REPO / "docs" / "model_report.md"
    text = report_file.read_text(encoding="utf-8")
    start, end = text.index(REPORT_START), text.index(REPORT_END) + len(REPORT_END)
    report_file.write_text(
        text[:start] + report(model, ev, n=args.n, seed=args.seed) + text[end:], encoding="utf-8"
    )
    print(f"Wrote {model_file} and {report_file}")  # noqa: T201


if __name__ == "__main__":
    main()
