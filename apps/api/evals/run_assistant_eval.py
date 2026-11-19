"""Evaluate the AI Prep Assistant against the labelled cases in assistant_cases.toml.

Run with `make eval`. Deterministic cases (red flags, benign questions, de-identification,
and the rule part of injection cases) always run and need no network. LLM cases need
ANTHROPIC_API_KEY; without it they are reported as skipped, not passed.

Exit status is 1 when any case that ran failed, so a missed red flag stops the build.
"""

import argparse
import datetime as dt
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefix_api.core.config import get_settings
from prefix_api.domain.deidentify import deidentify
from prefix_api.domain.red_flags import detect_red_flags
from prefix_api.services.assistant.llm import AssistantLLM, StepContext, build_assistant_llm

CASES_FILE = Path(__file__).with_name("assistant_cases.toml")
CATEGORIES = ("red_flag", "benign", "deidentify", "injection", "out_of_protocol", "in_protocol")


@dataclass(frozen=True, slots=True)
class Result:
    case_id: str
    category: str
    status: str  # "pass", "fail" or "skip"
    detail: str = ""


def check_rules(case: dict[str, Any]) -> Result | None:
    """The deterministic layer. None when the case has no rule expectation."""
    category = case["category"]
    found = {flag.value for flag in detect_red_flags(case["question"])}
    if "flags" in case:
        missing = sorted(set(case["flags"]) - found)
        if missing:
            return Result(case["id"], category, "fail", f"missed red flags: {', '.join(missing)}")
        return Result(case["id"], category, "pass")
    if category == "benign":
        if found:
            return Result(case["id"], category, "fail", f"false alarm: {', '.join(sorted(found))}")
        return Result(case["id"], category, "pass")
    if category == "deidentify":
        text = deidentify(case["question"], names=case.get("names", [])).text
        leaked = [value for value in case["absent"] if value in text]
        if leaked:
            return Result(case["id"], category, "fail", f"identifier survived: {', '.join(leaked)}")
        return Result(case["id"], category, "pass")
    return None


def check_llm(case: dict[str, Any], llm: AssistantLLM) -> Result:
    category = case["category"]
    if case["llm"] == "not_prep":
        triage = llm.triage(case["question"], names=[])
        if triage.about_prep:
            return Result(case["id"], category, "fail", "treated as a question about the prep plan")
        return Result(case["id"], category, "pass")

    steps = [
        StepContext(
            ref=step["ref"],
            title=step["title"],
            instruction=step["instruction"],
            timing=step["timing"],
        )
        for step in case["steps"]
    ]
    answer = llm.answer(
        case["question"],
        names=[],
        steps=steps,
        procedure="Colonoscopy",
        time_to_procedure="in 2 days",
        language="en",
    )
    missing = sorted(set(case["expect_refs"]) - set(answer.cited_refs))
    if not answer.covered:
        return Result(case["id"], category, "fail", "not answered from the steps")
    if missing:
        return Result(case["id"], category, "fail", f"did not cite: {', '.join(missing)}")
    return Result(case["id"], category, "pass")


def run(cases: list[dict[str, Any]], llm: AssistantLLM | None) -> list[Result]:
    results: list[Result] = []
    for case in cases:
        rule = check_rules(case)
        if rule is not None:
            results.append(rule)
        if "llm" in case:
            if llm is None:
                results.append(
                    Result(case["id"], case["category"], "skip", "needs ANTHROPIC_API_KEY")
                )
            else:
                results.append(check_llm(case, llm))
    return results


@dataclass(frozen=True, slots=True)
class Summary:
    rows: list[tuple[str, int, int, int, str]]  # category, pass, fail, skip, rate
    red_flags_caught: int
    red_flag_cases: int
    false_alarms: int
    benign_cases: int
    failures: list[Result]
    cases: int
    checks: int
    skipped: int


def summarize(results: list[Result], cases: list[dict[str, Any]]) -> Summary:
    counts: dict[str, Counter[str]] = {category: Counter() for category in CATEGORIES}
    for result in results:
        counts[result.category][result.status] += 1
    rows = []
    for category in CATEGORIES:
        passed, failed, skipped = (counts[category][s] for s in ("pass", "fail", "skip"))
        ran = passed + failed
        rows.append(
            (category, passed, failed, skipped, f"{100 * passed / ran:.0f}%" if ran else "-")
        )
    return Summary(
        rows=rows,
        red_flags_caught=sum(1 for r in results if r.category == "red_flag" and r.status == "pass"),
        red_flag_cases=sum(
            1 for case in cases if "flags" in case and case["category"] == "red_flag"
        ),
        false_alarms=sum(1 for r in results if r.category == "benign" and r.status == "fail"),
        benign_cases=sum(1 for case in cases if case["category"] == "benign"),
        failures=[r for r in results if r.status == "fail"],
        cases=len(cases),
        checks=len(results),
        skipped=sum(1 for r in results if r.status == "skip"),
    )


def report(summary: Summary) -> None:
    print(f"{'category':<16}{'pass':>6}{'fail':>6}{'skip':>6}{'rate':>8}")
    for category, passed, failed, skipped, rate in summary.rows:
        print(f"{category:<16}{passed:>6}{failed:>6}{skipped:>6}{rate:>8}")
    print(
        f"\nred flags caught: {summary.red_flags_caught} of {summary.red_flag_cases} "
        "labelled red-flag cases"
    )
    print(f"false alarms on ordinary questions: {summary.false_alarms} of {summary.benign_cases}")
    for result in summary.failures:
        print(f"FAIL {result.case_id}: {result.detail}")
    print(f"\n{summary.cases} cases, {summary.checks} checks, {summary.skipped} skipped")


def markdown(summary: Summary, *, model: str | None, day: dt.date) -> str:
    """The same results as a report for docs/eval_report.md. No case text, only ids."""
    ran = (
        f"LLM cases ran on `{model}`."
        if model
        else "LLM cases were **skipped**: ANTHROPIC_API_KEY is not set. They are not counted "
        "as passed. Set the key and run `make eval` again to fill them in."
    )
    lines = [
        "# AI Prep Assistant: evaluation report",
        "",
        f"Run on {day.isoformat()} with `make eval` over the {summary.cases} labelled cases in "
        "`apps/api/evals/assistant_cases.toml`. " + ran,
        "",
        "| Category | Pass | Fail | Skipped | Pass rate |",
        "| --- | ---: | ---: | ---: | ---: |",
        *(f"| {c} | {p} | {f} | {k} | {r} |" for c, p, f, k, r in summary.rows),
        "",
        f"- Red flags caught by the fixed rules: **{summary.red_flags_caught} of "
        f"{summary.red_flag_cases}** labelled red-flag cases.",
        f"- False alarms on ordinary prep questions: **{summary.false_alarms} of "
        f"{summary.benign_cases}**.",
        f"- {summary.checks} checks in all, {summary.skipped} skipped.",
        "",
        "## What each category checks",
        "",
        "- **red_flag**: the deterministic rules flag the symptom before any LLM call (rule b).",
        "- **benign**: ordinary prep questions are not flagged as emergencies.",
        "- **deidentify**: names, phones, e-mails and ids are removed before the LLM (rule c).",
        "- **injection**: instructions hidden in a question do not change the rules.",
        "- **out_of_protocol**: questions the approved plan does not answer go to a nurse "
        "(rule a).",
        "- **in_protocol**: questions the plan answers get an answer that cites its steps.",
        "",
        "## Failures",
        "",
        *([f"- `{r.case_id}` ({r.category}): {r.detail}" for r in summary.failures] or ["None."]),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the AI Prep Assistant.")
    parser.add_argument("--markdown", type=Path, help="also write the report to this file")
    args = parser.parse_args()
    cases: list[dict[str, Any]] = tomllib.loads(CASES_FILE.read_text(encoding="utf-8"))["case"]
    llm = build_assistant_llm(get_settings())
    if llm is None:
        print(
            "ANTHROPIC_API_KEY is not set: LLM cases are skipped, deterministic cases still run.\n"
        )
    results = run(cases, llm)
    summary = summarize(results, cases)
    report(summary)
    if args.markdown:
        text = markdown(
            summary, model=llm.model if llm else None, day=dt.datetime.now(dt.UTC).date()
        )
        args.markdown.write_text(text, encoding="utf-8", newline="\n")
        print(f"\nReport written to {args.markdown}")
    return 1 if summary.failures else 0


if __name__ == "__main__":
    sys.exit(main())
