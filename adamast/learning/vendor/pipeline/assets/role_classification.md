Classify these agents into functional roles based on what they DO in the traces.

AGENTS AND THEIR BEHAVIOR:
${agents_with_samples}

COMMON ROLE TYPES (use these if they fit, but you may create new role names if needed):
${default_hint}

For each agent, determine its functional role based on its ACTUAL BEHAVIOR in the traces,
not just its name. If an agent doesn't fit any common role, create a descriptive role name
(e.g., "translator", "reasoner", "aggregator", "verifier", "planner").

Role names should be lowercase single words.

OUTPUT JSON:
{
  "agent_roles": {
    "AgentName": {
      "role": "role_name",
      "definition": "What this agent does based on trace evidence",
      "purpose": "Short purpose phrase (5-10 words)"
    }
  }
}
