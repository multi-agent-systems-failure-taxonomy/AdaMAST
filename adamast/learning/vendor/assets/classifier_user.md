Given this failure taxonomy:

$taxonomy_desc

Classify the following agent failure trace into ONE taxonomy code.

TRACE:
$trace_text

Respond in this exact JSON format:
{"code": "<code>", "label": "<label>", "evidence": "<specific evidence from trace>", "confidence": <0.0-1.0>, "recovery_hint": "<what to try differently>"}
