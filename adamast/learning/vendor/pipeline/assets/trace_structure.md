Analyze these system traces to extract TRACE FORMAT and ARCHITECTURE.

PRE-EXTRACTED AGENTS (found via pattern matching):
${agents_list}

AGENT ROLE CLASSIFICATION (LLM-assigned based on trace behavior):
${agent_to_role}

DISCOVERED ROLE DEFINITIONS:
${role_defs_text}

SAMPLE TRACES:
${traces_text}

Analyze the ACTUAL trace content (not our wrapper format). Look for:
1. How agents communicate (markers, formatting)
2. Key fields in the ACTUAL trace (not [TASK], [META] - those are our wrapper)
3. Architecture patterns

OUTPUT JSON:
{
  "trace_format": {
    "agent_markers": ["Actual patterns used to mark agents in traces"],
    "key_fields": [
      {
        "field_name": "actual_field_name",
        "description": "What this field contains",
        "location": "Where to find it in the trace"
      }
    ],
    "output_structure": "How traces are actually structured",
    "example_patterns": [
      "Regex or text pattern to find important content"
    ]
  },
  "architecture": {
    "topology": "sequential | parallel | hierarchical | debate | hybrid",
    "topology_details": "How agents actually interact based on trace evidence",
    "verification_pattern": "self-verify | peer-verify | dedicated-checker | consensus | none",
    "verification_details": "Who verifies what (based on observed agent interactions)",
    "termination_owner": "Who decides when workflow ends",
    "critical_handoffs": [
      {
        "from_agent": "ActualAgentName",
        "to_agent": "ActualAgentName",
        "what_is_passed": "solution/feedback/verdict",
        "failure_risk": "What can go wrong"
      }
    ]
  },
  "agent_role_corrections": {
    "AgentName": "corrected_role if my auto-classification was wrong"
  }
}
