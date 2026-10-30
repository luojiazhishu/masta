### Capability: files

Read and write artifacts in the shared workspace. Operations:

- `{"capability": "files", "args": {"action": "read", "filename": "<name>"}}` returns a confirmation string of the form `Successfully read file '<name>' from the workspace (<N> chars / <M> lines). The full content of '<name>' is shown below:` followed by the file body framed by `---BEGIN <name>---` / `---END <name>---` markers. Once you have read a file in this turn or any earlier turn of the same session, its content is preserved verbatim in CONTEXT — do NOT re-read the same file unless you have reason to believe it changed.
- `{"capability": "files", "args": {"action": "read", "filename": "<name>", "subdir": "<key>"}}` reads from a registered read-only mirror instead of the shared workspace. Available `subdir` keys: `tamarin_manual` (the local Tamarin manual mirror under `data/tamarin/`; start from `index.md`).
- `{"capability": "files", "args": {"action": "write", "filename": "<name>", "payload": "<text>"}}` overwrites the named artifact in the shared workspace and returns a confirmation string of the form `Successfully wrote <N> bytes to file '<name>' in the workspace. The file '<name>' now stores exactly the payload you supplied in this turn.` `subdir` is not honored for writes. Once a deliverable has been written and the confirmation is in CONTEXT, treat that deliverable as produced — do NOT immediately re-read it as a sanity check.
- `{"capability": "files", "args": {"action": "exists", "filename": "<name>"}}` returns a boolean. Pass `subdir` to probe a registered mirror.

The host enforces a per-role write whitelist (see RESPONSIBILITIES DESP). Reads are unrestricted across roles.
