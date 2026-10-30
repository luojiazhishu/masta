### Capability: llm

The agent is itself the LLM call site. The host invokes you with the assembled prompt every turn; you must reply with **one** JSON object whose top level is exactly the wrapper below — not the bare operation, not multiple JSON objects in sequence.

```
{"operations": [
   {"capability": "<name>", "args": {...}},
   {"capability": "end", "args": {"reason": "<text>"}}
]}
```

WRONG (bare operation, no wrapper):
```
{"capability": "scheduler", "args": {...}}
```

WRONG (JSONL, two top-level objects):
```
{"capability": "scheduler", "args": {...}}
{"capability": "end", "args": {"reason": "..."}}
```

RIGHT (single wrapper, two operations inside):
```
{"operations": [
   {"capability": "scheduler", "args": {...}},
   {"capability": "end", "args": {"reason": "dispatched"}}
]}
```

Operations are dispatched in array order. The terminal action is `{"capability": "end", "args": {"reason": "<text>"}}`; once `end` appears, subsequent operations in the same array are ignored. Do not include free text outside the JSON object, do not wrap the response in Markdown code fences.
