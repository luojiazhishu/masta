### Capability: tamarin

Run the Tamarin prover against a theory file already written to the
shared workspace. Each call passes a `filename` relative to the
workspace; the host reads the file from disk and feeds it to Tamarin —
agents MUST NOT inline the theory body in the action args. Operations:

- `{"capability": "tamarin", "args": {"action": "well_formed", "filename": "<name>"}}` runs the wellformedness check on the file.
- `{"capability": "tamarin", "args": {"action": "verify_executable", "filename": "<name>"}}` proves the `executable` lemma in the file.
- `{"capability": "tamarin", "args": {"action": "verify_lemma", "filename": "<name>", "lemma_name": "<lemma>"}}` proves a single safety lemma in the file.

The result carries `{verdict, stdout, stderr, raw_trace}`; outputs are truncated to keep the context compact, and the full trace is preserved in the verbose-result side store.

Tamarin manual is mirrored locally under `data/tamarin/`. Use the `files` capability with `subdir: "tamarin_manual"` to consult it: read `index.md` first to pick a section that matches the current prep stage (`serialization`, `rules`, `threat_model`, `properties`, `analysis`), then read that file by name to load the details. Fall back to the upstream URL <https://tamarin-prover.github.io/manual/> only when the local mirror is insufficient. Manual reads do not consume host resources beyond the file read itself.
