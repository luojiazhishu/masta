### Capability: scheduler

Available only to the Arbiter. Operations:

- `{"capability": "scheduler", "args": {"action": "schedule_initial", "target_role": "<role>", "task_label": "<label>", "brief": "<text>"}}` starts the next default-path task; the host immediately runs the new expert's first ReAct step and returns its `summary` as the observation. `task_label` is one of `serialize_actions` / `extract_security_requirements` / `model_rules` / `model_properties` / `finalize`.
- `{"capability": "scheduler", "args": {"action": "schedule_revision", "target_role": "<role>", "target_artifact": "<artifact>", "problem_type": "<label>", "brief": "<text>"}}` discards the current active expert and starts a fallback task on the expert and artifact selected by the six-class taxonomy; the host runs that new task's first step and returns its `summary`. `problem_type` is one of `rule unreachable`, `property disagrees with protocol`, `threat-model omission`, `lemma syntax error`, `executable-verification failure`, `other`. For `other`, `brief` MUST start with `subtype: <name>`.
- `{"capability": "scheduler", "args": {"action": "advance"}}` runs one more ReAct step on the current active expert and returns its fresh `summary`. Returns an error if no expert is active.

The host validates each directive against the default `(target_role, target_artifact)` map; mismatched directives are rejected and the rejection reason is delivered back through the context.
