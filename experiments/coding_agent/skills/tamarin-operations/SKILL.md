---
name: tamarin-operations
description: How to invoke the local `tamarin-prover` CLI to validate a `.spthy` model, plus how to read the verdicts and `stderr` it produces. Use this skill whenever you need to wellformedness-check a Tamarin theory, prove a single lemma, or prove every lemma in a theory.
---

# Tamarin operations

The `tamarin-prover` binary is on `$PATH` inside the working directory. Use
it freely during development; the runner allots enough wall time for several
iterations. This skill is the single source of truth for invocation and
verdict semantics — no other skill duplicates it.

If a verdict surprises you and you cannot tell whether the cause is a
modelling error or a Tamarin syntax / builtin question, consult the
`tamarin-manual` skill before guessing.

---

## 1. Time budget (hard constraint)

The dataset is engineered so that **every protocol's full `--prove` run
completes well under 5 minutes** on the verification machine. This is a
property of the data, not a hope. Use it as a sanity gate:

- If a single `tamarin-prover` invocation runs **longer than 20 minutes**
  without returning, your model is broken — typically you used `xor` /
  `diffie-hellman` in a way that explodes the equational unifier, or you
  wrote a rule whose LHS/RHS does not chain through state facts. Do NOT
  wait it out.
- When you hit that 20-minute wall: **kill the Tamarin process, leave the
  current `model.spthy` as it is, and exit.** Do not start a fourth, fifth,
  sixth iteration. The runner will pick up whatever `model.spthy` is on
  disk and score it; getting a partial verdict beats burning the timeout
  budget.
- The downstream runner kills any single `tamarin-prover` call at 5 minutes
  anyway, so a model that needs longer than that is unscorable in
  practice — your 20-minute self-check just lets you fail faster than the
  runner's own timeout.

When a model fits the dataset's profile, individual lemma proofs return in
**seconds**, not minutes. If your iteration loop is taking minutes per
`--prove`, stop and rewrite the rules — XOR/DH terms in `In(...)`
arguments and `Fr(~x)` placed outside the LHS are the two most common
offenders.

---

## 2. Commands

### 2.A Wellformedness check (no proof, just parse + sanity)

    tamarin-prover model.spthy

Look for `analyzed: 0` plus zero warnings. Any `WARNING` or `error` line
indicates a syntax / typing problem that must be fixed before proving.

### 2.B Verify a single lemma

    tamarin-prover --prove=<lemma_name> model.spthy

The tail of the output reports per-lemma verdicts in the form

    analyzed: 1
      <lemma_name> (exists-trace): verified (X steps)

### 2.C Verify every lemma at once

    tamarin-prover --prove model.spthy

Useful as the final pass once `model.spthy` stabilizes.

### 2.D Bound the proof depth

    tamarin-prover --prove=<lemma_name> --bound=10 model.spthy

Helpful when a lemma times out and you want a quick sanity bound rather
than waiting for the heuristic to give up on its own.

---

## 3. Verdict semantics

Possible per-lemma verdicts:

- `verified` — the lemma holds (or, for `exists-trace`, a witness was
  found).
- `falsified` — Tamarin found a counter-example. **Read `stderr` and the
  attack trace before reacting**: the same word covers two very
  different situations, see section 4.
- `analysis incomplete (X steps)` — the heuristic timed out / gave up
  without a verdict. Bound the proof depth (section 2.D) to triage, or
  simplify the rule premises so the unifier terminates.

---

## 4. Reading verdict + stderr together

`falsified` is overloaded. Always inspect `stderr` (and the attack-trace
block when present) to classify the case:

- **Parse / wellformedness error**. `stderr` contains markers such as
  `Configured target lemma was not found`, `malformed`, `parse error`,
  `expected`, or `wellformedness`. The verdict is `falsified` but
  Tamarin never actually proved anything — it bailed on a structural
  defect. Likely culprits: missing `end` line; an unpaired quoted lemma
  body; a rule renamed inconsistently; a lemma name that does not match
  what `--prove=` was given. Fix the structural defect, not the rule
  semantics, then rerun.
- **Genuine logical falsification**. `stderr` is clean of the markers
  above and Tamarin printed an attack-trace state graph below the
  verdict. Trace the action facts in order; the trace pinpoints which
  rule fired and which premise was satisfied. This is your primary
  signal for what to revise. For weakness-variant tasks this is the
  *expected* outcome.
- **Tooling / environment problem**. `stderr` contains
  `WARNING: 'maude --version' returned unsupported version` (or similar
  toolchain warnings). The verdict is degenerate; do NOT rewrite the
  model in response. Fix the environment first.

`verified` and `analysis incomplete` are unambiguous and need no
stderr cross-check.

---

## 5. Hard rules for invocation

- Always run `tamarin-prover` against the working directory's
  `model.spthy`. Do not edit `model.spthy` while a Tamarin process is
  reading it.
- Do not modify any file outside the current working directory.
- Tamarin output may be long; only the last ~30 lines (the verdict
  block, plus any preceding attack trace) are needed for
  decision-making.
