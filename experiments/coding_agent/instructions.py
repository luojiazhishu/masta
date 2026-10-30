"""Task brief written into the agent's working directory.

The same Markdown body is dropped at both ``AGENTS.md`` and ``CLAUDE.md``
so codex / opencode / claude code each auto-discover their own entry
file with identical content.
"""

from __future__ import annotations

BASE_BRIEF = """# Task: model the protocol in `document.txt` as a Tamarin theory

Your single job, every run, is:

1. Read `./document.txt` (the protocol description).
2. Follow the modeling pipeline in `skills/modeling-pipeline/SKILL.md`
   (serialize → rules → threat model → properties → validate).
3. Write `./model.spthy` (the complete Tamarin theory).

That is the only deliverable. The scorer reads nothing else from you.

## Working directory layout

- `document.txt` — the protocol description (your input)
- `skills/modeling-pipeline/SKILL.md` — the pipeline, output formats, and
  the `executable` lemma contract (section 5.C is mandatory reading)
- `skills/tamarin-operations/SKILL.md` — `tamarin-prover` commands,
  verdict semantics, and the time budget (mandatory reading before your
  first invocation)
- `skills/tamarin-manual/SKILL.md` — index of the local Tamarin manual
  mirrored under `./tamarin_manual/`; consult it whenever you are
  uncertain about Tamarin syntax or semantics
- `tamarin_manual/` — the manual pages (index.md plus per-chapter pages)

## What `model.spthy` MUST contain

- `theory <Name> begin` ... `end` frame (section 5.D of the pipeline skill).
- The `builtins:` line that lists exactly the cryptographic primitives the
  protocol uses.
- One Tamarin multiset-rewriting rule per action block (section 2.B). Each
  such rule MUST emit a parameterless step-marking action fact `Test<N>()`
  where `<N>` is the 1-indexed position of the action block in protocol
  order. The scorer's action-block extractor matches action facts by the
  `Test` name prefix; without these, the protocol is unscorable.
- A `lemma executable: exists-trace ...` that witnesses one complete honest
  protocol run by chaining all `Test<i>()` facts in order:
  `Ex #t1 ... #tN. Test1() @ #t1 & ... & TestN() @ #tN & #t1 < #t2 & ...`.
  Name MUST be exactly `executable`, mode MUST be `exists-trace`, NO
  `// category:` annotation. Full contract and a worked example in
  section 5.C of the pipeline skill — read it before writing the lemma.
- One safety-property lemma per security goal stated or implied by the
  description, each carrying a `// category: <X>` annotation on the line
  above its declaration. Categories: `secrecy | aliveness | weak_agreement
  | noninjective_agreement | injective_agreement`. Templates in
  section 5.B of the pipeline skill.

## Self-validation with Tamarin

You are expected to call `tamarin-prover` yourself during development:

- Wellformedness: `tamarin-prover model.spthy`
- Executable witness: `tamarin-prover --prove=executable model.spthy`
  (must `verify`; `falsified` means your rules cannot complete the
  protocol — fix the rules, do not rewrite the lemma)
- All lemmas: `tamarin-prover --prove model.spthy`

The `tamarin-operations` skill is the single source of truth for every
command and how to read each verdict (especially how to tell a parse
error apart from a real falsification on `stderr`). Iterate: when a
lemma falsifies unexpectedly, inspect the attack trace and revise either
the offending rule or the lemma, then rerun.

## What the scorer does after you exit

The scorer re-runs Tamarin on `model.spthy` for the official scoring:

- Extracts the action-block sequence from the proof trace of your
  `executable` lemma. The lemma MUST exist with that exact name in
  `exists-trace` mode, otherwise this run is unscorable.
- Verifies every safety-property lemma you embedded. Every safety lemma
  MUST carry a `// category: <X>` annotation; the scorer uses it to
  classify the verdict.

If your own Tamarin runs already show the expected verdicts, the scorer's
re-run will match.

## Hard rules

- Do NOT touch any file outside the current working directory.
- Do NOT skip running `tamarin-prover` before exiting. Submitting an
  un-validated `model.spthy` is the most common failure mode.
- **Time budget for any single `tamarin-prover` invocation: 20 minutes.**
  The dataset is engineered so that a correct model finishes in seconds.
  If a `tamarin-prover` call has not returned after 20 minutes, kill it,
  leave the current `model.spthy` on disk, and exit. The scorer kills any
  individual Tamarin call at 5 minutes anyway, so anything slower than that
  is unscorable. Section 1 of the `tamarin-operations` skill explains how
  to spot the usual culprits (XOR/DH terms in `In(...)`, `Fr(~x)` outside
  LHS).
- Stop only after `model.spthy` exists in this directory **and** your last
  `tamarin-prover --prove model.spthy` run produced verdicts you accept.

## Context

- Label: {label}"""


def build_base_brief(label: str) -> str:
    return BASE_BRIEF.format(label=label)


__all__ = ["build_base_brief"]
