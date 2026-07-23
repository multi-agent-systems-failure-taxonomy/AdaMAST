# An example run

Follow one AdaMAST-supervised task from first checkpoint to learned taxonomy.
Everything below is what the agent actually sees and produces — every shape
is the real format the runtime parses, not an illustration.

The running example is the bundled demo taxonomy (orbital task scheduling).
Try the matching dashboard locally with `python -m adamast.examples.dashboard_demo`.

## 🛑 1. A checkpoint fires

A checkpoint is a configured boundary where the agent pauses to self-check —
here, after a tool call. The harness delivers the active taxonomy plus the
recent trajectory window, and the agent responds in the required reflection
shape:

```text
Observe:   The schedule reused the cached AOS/LOS window after a newer
           ephemeris was loaded.
Correlate: The task crossed the exact boundary described by ORB-01.
Map:       ORB-01 (Stale ephemeris used for scheduling) — evidence
           supports the match.
Decide:    Recompute the window before dispatch.
```

The firing is recorded as runtime evidence with its gate, task UID, and
checkpoint id.

## ✅ 2. A clean checkpoint

`none apply` is a valid outcome and is recorded too — clean checkpoints are
part of the evidence, not a skipped step:

```text
Observe:   The agent checked the updated window and payload.
Correlate: No evidence-supported failure mode remained.
Map:       none apply (considered ORB-01, ORB-05).
Decide:    Submit without repair.
```

## 🚦 3. The final gate

Before the final answer is released, the final gate — the blocking
pre-submission checkpoint — requires this exact format and allows at most
`repair_rounds` repairs:

```text
Final AdaMAST status: READY_TO_SUBMIT
Codes checked: ORB-01, ORB-04
Evidence: The dispatched plan cites ephemeris revision 214 and all
          boundaries are expressed in UTC; validation re-ran after repair.
Repair attempts used: 1
Final decision: submit
```

!!! warning "Honest failure beats claimed success"
    A `REPAIR_REQUIRED` status blocks completion; after `repair_rounds`
    unsuccessful attempts the agent must report the unresolved issue honestly
    instead of claiming clean success.

## 📊 4. What the dashboard shows

For a standalone harness run, the program-level
[dashboard](DASHBOARD.md) makes failure-mode firings, clean checkpoints,
evidence snippets, and per-task UID filters browsable live:

![AdaMAST runtime dashboard](assets/screenshots/dashboard-demo.png)

## 🧠 5. What learning produces

At session end, one canonical trace is recorded. After enough traces
accumulate, generation (or refinement) proposes a taxonomy specialized to the
observed failures — a normal stored record that future runs can inherit:

```json
{
  "taxonomy_id": "tax-skylab-orbital-demo-001",
  "repo": "demo/skylab-control",
  "domain": "orbital-task-scheduling",
  "codes": [
    {
      "id": "ORB-01",
      "name": "Stale ephemeris used for scheduling",
      "description": "A task window is computed from cached orbital data after a new ephemeris has arrived, placing the operation outside its valid visibility interval.",
      "category": "Runtime"
    },
    {
      "id": "ORB-02",
      "name": "Resource lock released before handoff",
      "description": "Exclusive antenna or compute capacity is released before the downstream task confirms ownership, allowing overlapping operations to claim the same resource.",
      "category": "Coordination"
    }
  ]
}
```

Inherit it in a later run with `--inherit tax-skylab-orbital-demo-001`.

## 📚 Where to go next

Continue with [TRACES_AND_LEARNING.md](TRACES_AND_LEARNING.md) — the
lifecycle behind steps 4–5 — or with [INTEGRATION.md](INTEGRATION.md) for the
checkpoint and reflection contract harness authors implement.
