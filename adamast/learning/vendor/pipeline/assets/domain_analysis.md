Analyze these system traces to understand the DOMAIN and TASK TYPE.

TRACES:
${traces_text}

Extract:
1. What domain is this? (math, code repair, incident response, etc.)
2. What makes tasks difficult in this domain?
3. Key terminology used in this domain
4. Common error patterns you observe

OUTPUT JSON:
{
  "domain": {
    "name": "e.g., Mathematics, Code Repair, Incident Response",
    "content_type": "e.g., proofs, numerical answers, code patches",
    "task_complexity": "What makes tasks hard in this domain"
  },
  "subdomains": ["algebra", "geometry", "combinatorics"],
  "domain_terminology": [
    {
      "term": "permutation",
      "meaning": "Ordered arrangement of elements",
      "error_associations": ["confused with combination"]
    }
  ],
  "common_error_patterns": [
    {
      "name": "off_by_one",
      "description": "Counting n items but getting n-1 or n+1",
      "detection_hints": ["fence post", "inclusive vs exclusive"]
    }
  ],
  "correctness_criteria": [
    {
      "criterion": "numerical_accuracy",
      "description": "Final number must be exactly correct",
      "how_to_verify": "Compare with ground truth"
    }
  ]
}
