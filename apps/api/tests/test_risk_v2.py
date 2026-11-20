"""Risk model v2: a calibrated logistic regression, versioned as a JSON file, chosen by config."""

import dataclasses
import datetime as dt
import json
from pathlib import Path

import pytest
from api_testkit import Builder, make_test_settings, staff_headers
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_risk import QUIET, high_risk_booking

from prefix_api.core.config import Settings
from prefix_api.db.models import RiskScore
from prefix_api.domain.enums import RiskLevel, Role
from prefix_api.domain.risk import RiskInputs
from prefix_api.domain.risk import score as score_v1
from prefix_api.domain.risk_v2 import FEATURES, LogisticModel, load_model, probability, score
from prefix_api.main import create_app
from prefix_api.services.risk import RULES, active_model, refresh

MODEL = load_model()
WORRYING = dataclasses.replace(
    QUIET,
    unconfirmed_steps=4,
    logistics_unconfirmed=True,
    problem_replies=1,
    open_escalations=1,
    hours_to_procedure=12,
)


def test_the_bundled_model_matches_the_code() -> None:
    assert MODEL.version == "lr-v2-synthetic-1"
    assert MODEL.features == tuple(FEATURES)
    assert 0 < MODEL.medium_from < MODEL.high_from < 1


def test_a_worrying_procedure_is_high_and_says_why_in_plain_words() -> None:
    result = score(MODEL, WORRYING)

    assert result.level is RiskLevel.HIGH
    labels = [f.label for f in result.factors]
    assert labels[0] == "4 prep steps not yet confirmed"
    assert "Escort or logistics not confirmed" in labels
    assert "Procedure within 24 hours with steps outstanding" in labels
    assert len(labels) <= 5
    assert all(f.points > 0 for f in result.factors)
    # The same words as version 1, so coordinators read the reasons the same way.
    v1_labels = {f.label for f in score_v1(WORRYING).factors}
    assert set(labels) - {"Colonoscopy: bowel prep is often incomplete"} <= v1_labels


def test_a_quiet_procedure_is_low_and_lists_nothing_absent() -> None:
    result = score(MODEL, QUIET)

    assert result.level is RiskLevel.LOW
    assert all(f.feature not in {"logistics", "problem_replies", "soon"} for f in result.factors)
    assert result.score < 15


def test_more_outstanding_steps_never_lower_the_risk() -> None:
    risks = [
        probability(MODEL, dataclasses.replace(QUIET, unconfirmed_steps=n))[0] for n in range(7)
    ]

    assert risks == sorted(risks)
    assert risks[-1] > 2 * risks[0]


def test_a_model_file_that_does_not_fit_the_code_is_refused(tmp_path: Path) -> None:
    good = json.loads(MODEL.to_json())
    for broken, message in [
        (good | {"features": good["features"][:-1]}, "features"),
        (good | {"means": good["means"][:-1]}, "lengths"),
        (good | {"scales": [0.0] * len(good["scales"])}, "scale"),
    ]:
        path = tmp_path / "broken.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            load_model(path)


def test_the_model_is_chosen_by_config(tmp_path: Path) -> None:
    assert active_model(make_test_settings()) is RULES
    v2 = active_model(make_test_settings(risk_model="v2"))
    assert (v2.name, v2.version, v2.label) == ("v2", MODEL.version, "v2 ML")

    other = dataclasses.replace(MODEL, version="lr-v2-hospital-3")
    path = tmp_path / "hospital.json"
    path.write_text(other.to_json(), encoding="utf-8")
    custom = active_model(make_test_settings(risk_model="v2", risk_model_file=path))
    assert custom.version == "lr-v2-hospital-3"


def test_stored_scores_say_which_model_made_them(
    build: Builder, db_session: Session, session_factory: sessionmaker[Session]
) -> None:
    booked = high_risk_booking(build, db_session)
    now = dt.datetime.now(dt.UTC)

    refresh(session_factory, now=now, model=active_model(make_test_settings(risk_model="v2")))

    stored = db_session.scalars(
        select(RiskScore).where(RiskScore.appointment_id == booked.appointment.id)
    ).one()
    assert stored.model_version == MODEL.version
    assert stored.factors[0]["label"]


@pytest.mark.parametrize(("name", "label"), [("v1", "v1 rules"), ("v2", "v2 ML")])
def test_the_dashboard_can_show_the_model_in_use(
    session_factory: sessionmaker[Session], build: Builder, name: str, label: str
) -> None:
    settings: Settings = make_test_settings(risk_model=name)
    staff = build.user(build.organization(), Role.COORDINATOR)
    with TestClient(create_app(settings, session_factory=session_factory)) as client:
        response = client.get("/risk/model", headers=staff_headers(settings, staff))

    assert response.status_code == 200
    assert response.json()["label"] == label


def test_a_broken_v2_file_stops_the_api_at_start_up(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    path = tmp_path / "missing.json"

    with pytest.raises(FileNotFoundError):
        create_app(
            make_test_settings(risk_model="v2", risk_model_file=path),
            session_factory=session_factory,
        )


def test_training_on_synthetic_history_beats_chance_and_matches_scikit_learn() -> None:
    np = pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    from prefix_api.ml.synthetic import generate
    from prefix_api.ml.train import fit

    model, ev = fit(generate(4000, seed=3), seed=3, version="test")

    assert ev.auc_v2 > 0.7
    assert ev.brier_v2 < ev.brier_base
    assert sum(n for *_, n in ev.calibration) == ev.n_test
    tp, fp, fn, tn = ev.confusion_v2
    assert tp + fp + fn + tn == ev.n_test
    # Our pure-Python prediction is the regression scikit-learn fitted, then Platt scaling.
    inputs: RiskInputs = WORRYING
    p, _ = probability(model, inputs)
    x = (np.array([read(inputs) for read in FEATURES.values()]) - model.means) / model.scales
    z = model.intercept + float(np.dot(model.coefficients, x))
    assert p == pytest.approx(1 / (1 + np.exp(-(model.calibration_a * z + model.calibration_b))))
    assert LogisticModel.from_json(model.to_json()) == model
