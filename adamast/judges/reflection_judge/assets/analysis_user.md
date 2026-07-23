## INPUT

task_id:        $task_id
candidate_id:   $candidate_id
run_id:         $run_id

### task_objective
$task_prompt

### expected_output (optional)
$expected_output

### candidate_output
$candidate_output

### score (optional, do not use to assign blame directly)
$score

### trace
$trace

## PROCESS (stages 1-7)

Stage 1 — TRACE EVENT RECONSTRUCTION
  Reconstruct the IMPORTANT events. Not a full transcript: only events that
  matter for success, failure, recovery, or final output. Each event needs:
    event_id (E1, E2, ...), summary, trace_location, stage, agent_role
    (if available), evidence (short quote or paraphrase from the trace).

Stage 2 — FAILURE POINT IDENTIFICATION
  From the reconstructed events, identify CONCRETE failure points. A failure
  point is created only with trace-grounded evidence. Possible kinds include
  wrong reasoning, unsupported assumption, ignored requirement, bad
  decomposition, failed tool call, poor recovery, premature termination,
  weak verification, checker rubber-stamping, refiner missing an error,
  coordination breakdown, context loss, invalid final output, cost blowup,
  unproductive loop, format violation, external/environmental failure.

  After backward identification, do ONE FORWARD SWEEP for absent-expected
  steps (e.g. planning without verification, tool call without error
  handling, multi-step problem without decomposition). Add those as failure
  points too, with the absence itself as evidence.

Stage 3 — EVIDENCE & LOCAL MECHANISM
  For each failure point, fill:
    observed_evidence (what the trace directly shows),
    inferred_mechanism (what you infer the local cause is),
    reason_observed_or_inferred ($reason_observed_or_inferred),
    evidence_strength ($evidence_strength),
    judge_confidence (float in [0.0, 1.0] — how sure you are the failure
    point EXISTS at all; NOT how well a label fits).

Stage 4 — BACKWARD CAUSE ANALYSIS
  For each failure point, search EARLIER in the trace for direct causes.
  When a parent exists, emit a relation with parent → child semantics
  ($relation_type).
  Only link when evidence supports it. Do not speculate.

Stage 5 — CAUSAL ROLE
  Assign EXACTLY ONE causal_role per failure point:
    root_cause                — true root cause of downstream failures
    upstream_cause            — causes other failures but is itself caused
    intermediate_cause        — both caused and causes
    downstream_symptom        — caused by upstream; not a fresh failure
    terminal_symptom          — the visible final symptom in the output
    recovered_failure         — happened but was repaired before output
    isolated_irrelevant       — real failure, did NOT contribute to task
                                failure or any other failure (low value)
    isolated_terminal_root    — the LAST upstream failure that bent the
                                rest of the run wrong; trace AFTER may
                                look clean (internally consistent) but is
                                on the wrong basis. HIGH value root.
                                REQUIRED: also fill
                                'downstream_clean_rationale' explaining
                                why downstream looked clean.
    external_condition        — environmental, not the candidate's behavior
    unclear                   — evidence does not support a confident pick

  An isolated_irrelevant call REQUIRES you to state which earlier events
  you considered as candidate parents and ruled out, in
  'ruled_out_parent_events'. Do NOT default to "isolated" — only when you
  actively looked and found nothing.

Stage 6 — RECOVERY & FINAL-PRESENCE
  For each failure point:
    recovery_status     ($recovery_status)
    recovery_source     ($recovery_source)
    present_in_final_output ($present_in_final_output)

Stage 7 — RELEVANCE, SEVERITY, OUTCOME LINKAGE, ATTRIBUTION, ACTIONABILITY
    objective_relevance       ($objective_relevance)
    severity                  ($severity)
    outcome_link              ($outcome_link)  Be conservative.
    candidate_attribution     ($candidate_attribution)  How much was this the
                              candidate/system's fault? External failure that
                              the candidate handled correctly = none/low.
                              External failure the candidate failed to
                              respond to = candidate-attributable.
    external_attribution      ($external_attribution)  Environmental fault.
    actionability             ($actionability)  How fixable
                              by changing the candidate's behavior.
    suggested_intervention    (short text, optional)

## OUTPUT (JSON ONLY)

{
  "trace_summary": {
    "task_objective": "...",
    "final_output_summary": "...",
    "score": null,
    "overall_judgment": "success | failure | partial | unknown",
    "summary": "one-paragraph what-happened"
  },
  "events": [
    { "event_id": "E1", "summary": "...", "trace_location": "...",
       "stage": "planning|...|other", "agent_role": "...", "evidence": "..." }
  ],
  "failure_points": [
    {
      "failure_point_id": "F1",
      "event_ids": ["E2"],
      "summary": "...",
      "observed_evidence": "...",
      "inferred_mechanism": "...",
      "reason_observed_or_inferred": "observed|inferred|mixed|unclear",
      "evidence_strength": "low|medium|high|direct",
      "judge_confidence": 0.0,
      "stage": "...",
      "trace_location": "...",
      "responsible_agent": "...",
      "responsible_role": "...",
      "causal_role": "root_cause|...|unclear",
      "ruled_out_parent_events": [
        { "event_id": "E1", "reason": "no causal link because ..." }
      ],
      "downstream_clean_rationale": "REQUIRED for isolated_terminal_root only",
      "recovery_status": "...",
      "recovery_source": "...",
      "present_in_final_output": "...",
      "objective_relevance": "...",
      "severity": "...",
      "outcome_link": "...",
      "candidate_attribution": "...",
      "external_attribution": "...",
      "actionability": "...",
      "suggested_intervention": "..."
    }
  ],
  "relations": [
    { "source_failure_point_id": "F1", "target_failure_point_id": "F2",
       "relation_type": "caused|...|made_irrelevant", "evidence": "...",
       "confidence": 0.0 }
  ]
}

Constraints:
  1. Every failure point MUST have non-empty observed_evidence.
  2. Every relation MUST connect two existing failure points.
  3. Relations MUST be backward-grounded (source earlier than target).
  4. Causal role isolated_irrelevant REQUIRES non-empty
     'ruled_out_parent_events' explaining which earlier events you ruled out.
  5. Causal role isolated_terminal_root REQUIRES
     'downstream_clean_rationale'.
  6. Do NOT include taxonomy codes or 'taxonomy_mappings' in this stage.
  7. If no failure point is supported by the trace, return an empty
     'failure_points' list AND set 'trace_summary.overall_judgment' = success
     (or partial / unknown as appropriate).

Return ONLY the JSON object. No commentary.
