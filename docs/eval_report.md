# AI Prep Assistant: evaluation report

Run on 2026-11-19 with `make eval` over the 42 labelled cases in `apps/api/evals/assistant_cases.toml`. LLM cases were **skipped**: ANTHROPIC_API_KEY is not set. They are not counted as passed. Set the key and run `make eval` again to fill them in.

| Category | Pass | Fail | Skipped | Pass rate |
| --- | ---: | ---: | ---: | ---: |
| red_flag | 17 | 0 | 0 | 100% |
| benign | 10 | 0 | 0 | 100% |
| deidentify | 5 | 0 | 0 | 100% |
| injection | 2 | 0 | 2 | 100% |
| out_of_protocol | 0 | 0 | 3 | - |
| in_protocol | 0 | 0 | 3 | - |

- Red flags caught by the fixed rules: **17 of 17** labelled red-flag cases.
- False alarms on ordinary prep questions: **0 of 10**.
- 42 checks in all, 8 skipped.

## What each category checks

- **red_flag**: the deterministic rules flag the symptom before any LLM call (rule b).
- **benign**: ordinary prep questions are not flagged as emergencies.
- **deidentify**: names, phones, e-mails and ids are removed before the LLM (rule c).
- **injection**: instructions hidden in a question do not change the rules.
- **out_of_protocol**: questions the approved plan does not answer go to a nurse (rule a).
- **in_protocol**: questions the plan answers get an answer that cites its steps.

## Failures

None.
