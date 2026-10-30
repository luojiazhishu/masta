---
name: modeling-pipeline
description: When converting a natural-language security-protocol description into a Tamarin (.spthy) model, follow this multi-step modeling pipeline and produce the listed artifacts. Use this skill whenever a `document.txt` containing a protocol description is present in the working directory together with this skill folder.
---

# Modeling pipeline

**Your task, every run, is exactly one thing: read `./document.txt`, follow the modeling pipeline below, and write `./model.spthy`.** Nothing else is graded.

When you need to invoke `tamarin-prover`, see the `tamarin-operations` skill.
When you are uncertain about any Tamarin construct, syntax, or builtin
semantics, see the `tamarin-manual` skill before guessing.

---

## 1. Inputs and outputs

**Input** (already in the working directory):

- `document.txt` — the natural-language protocol description.
- `skills/modeling-pipeline/SKILL.md` — this file.
- `skills/tamarin-operations/SKILL.md` — Tamarin commands and verdict semantics.
- `skills/tamarin-manual/SKILL.md` — index of the local Tamarin manual.

**Required output** (write under the same working directory):

- `model.spthy` — the complete Tamarin theory (format in section 5).

That is the **only** file the downstream evaluator reads from you.

After you exit, the runner re-runs Tamarin on `model.spthy` to (a) extract the action-block sequence from the proof trace of your `executable` lemma and (b) verify every safety lemma you embedded. Those re-runs are authoritative for scoring. So `model.spthy` MUST contain a well-named `executable` lemma (section 5.C) and one safety-property lemma per security goal (section 5.B).

Intermediate scratch (e.g. a draft `action_blocks.yaml`, `threat_model.md`, planning notes) is allowed under `./.scratch/` if you find it useful. The runner ignores everything except `model.spthy`.

**You SHOULD run `tamarin-prover` during development to validate your model and revise it before exiting.** The exact commands and verdict semantics live in the `tamarin-operations` skill — read it before your first invocation.

---

## 2. Pipeline order

Execute these five steps in order: serialize → rules → threat model → properties → validate.

For each step:

1. **Produce** the artifact for this step.
2. **Self-review** the artifact against the constraints listed in this section. If you find an issue, revise before moving on.

### 2.A Serialize operations (internal step)

- Read `document.txt` end-to-end.
- Sketch the protocol's action-block sequence per the schema in section 3. Keep the sketch as `./.scratch/action_blocks.yaml`; the runner does not read it but you will need it to drive section 2.B.
- Self-review checklist:
  - Every protocol role declared in the description appears as some block's `role`.
  - Block ordering follows the protocol's message order.
  - Every `send` field is present (use `"<END>"` for the terminal role's last block).

### 2.B Add rules (deliverable starts here)

- Open `./model.spthy` and write the theory frame from section 5.D: `theory <Name> begin`, `builtins: ...`, optional `functions:` / `equations:` blocks.
- For each action block in your sketch (in order, 1-indexed), append one Tamarin multiset-rewriting rule that:
  - Consumes / produces the correct linear and persistent facts (`!Ltk`, `!Pk`, role-state facts, etc.).
  - Issues `Fr(~name)` for each name in `generate_nonces`.
  - Encodes each `compute` line as a let-binding or in-line term construction.
  - Encodes each `assert` line as a pattern match on the rule's left-hand side.
  - Sends / receives messages via `Out(...)` / `In(...)`.
  - **Emits exactly one parameterless step-marking fact `Test<N>()`** in its action-fact list, where `<N>` is the 1-indexed position of the action block in the protocol's natural execution order. This is the contract between rules and the `executable` lemma; the runner's action-block extractor walks the proof graph and picks every node whose action-fact name starts with `Test` (case-insensitive). See section 5.C.
  - Emits the safety-lemma action facts (`Create`, `Running`, `Commit`, `Secret`, `Honest`, `LtkReveal`) that section 5.A and section 5.B refer to.
- After each rule, self-review:
  - Action facts match the conventions in section 5.A.
  - Free variables on the right-hand side are bound on the left-hand side or by `Fr(...)`.
  - Run a wellformedness check early and often (`tamarin-operations` skill, "wellformedness" section); fix any warning before adding more rules.
- After all rules are in, append the `executable` lemma (section 5.C) and prove it via the `tamarin-operations` skill ("verify a single lemma" section, target `executable`). It MUST `verify`. If it `falsified`, your rules cannot complete the protocol — fix the rules before continuing.

### 2.C Generate threat model (internal step)

- From the description, articulate the adversary capabilities and security requirements. Keep them as `./.scratch/threat_model.md`; the runner does not read it but you will need it to drive section 2.D.
- Template in section 4.
- Self-review checklist:
  - Adversary capabilities cover at least the network model (Dolev-Yao) plus any reveal / compromise allowed by the description (long-term key reveal, ephemeral reveal, session-state reveal, etc.).
  - Each security requirement names: which key or payload, under what session scope, which agreement direction, which agreement strength (aliveness / weak / non-injective / injective).

### 2.D Add safety properties

- For each security requirement from 2.C, append one lemma to `model.spthy`. Use the templates in section 5.B as starting points; adapt parameter tuples to the protocol.
- After each lemma:
  - Verify the `// category:` annotation matches the formula.
  - Prove that single lemma via the `tamarin-operations` skill and inspect the verdict. The verdict the description intends (`verified` for a real protocol, `falsified` for a weakness variant) is what you should observe; if it does not match, revise either the lemma (section 5.B) or the offending rule (section 2.B), then rerun.
  - All referenced action facts must be emitted somewhere in the rules; if `falsified` traces show a fact never fires, fix the rule.

### 2.E Validate end-to-end and finalize

- Prove every lemma at once via the `tamarin-operations` skill ("verify every lemma"). Expect `analyzed: N` lines for every lemma, with verdicts you accept.
- For any lemma that `falsified` unexpectedly, the typical root causes are: (1) missing `Honest` / `LtkReveal` exemption clause on the conclusion side, (2) parameter mismatch between `Running` and `Commit` (must be identical in order, content, types), (3) action fact emitted in the wrong rule. Revise and rerun.
- For any lemma that times out (`analysis incomplete`), simplify the quantifier structure or strengthen the rule premises so Tamarin's heuristic terminates; the bounded-proof variant in `tamarin-operations` is helpful for triage.
- Re-read `model.spthy` end-to-end. Confirm: `theory ... begin` opens it, `end` closes it, every safety lemma carries a `// category:` line, the `executable` lemma is present and uses `exists-trace`.

---

## 3. action_blocks.yaml schema

Top-level YAML value: a sequence of action blocks. Each action block is a mapping with exactly two keys: `role` (string) and `operations` (sequence of single-key mappings, one per operation).

Operation entries use these keys, in this fixed order when present: `receive` → `generate_nonces` → `compute` → `assert` → `send`. The first four are optional; omit the entire entry when absent. `send` is mandatory in every action block.

Operation value types:

- `receive`: list of message-term strings.
- `generate_nonces`: list of fresh-variable name strings.
- `compute`: list of `name = expression` strings.
- `assert`: list of `lhs = rhs` constraint strings.
- `send`: list of message-term strings, or the literal string `"<END>"` for the final action block.

Message-term conventions (used inside the strings):

- bound variable: lowercase identifier (e.g. `m`, `na`)
- public variable: `$` prefix (e.g. `$A`)
- fresh variable: `~` prefix (e.g. `~k`)
- literal constant: single-quoted (e.g. `'init'`)
- function application: `symbol(arg1, arg2, ...)` (e.g. `senc(m, k)`)
- concatenation (hash input): `|` (e.g. `message1|nonce`)
- tuple: `<a, b, c>`

Skeleton:

```yaml
- role: <participant role>
  operations:
    - receive: [<term>, ...]
    - generate_nonces: [<name>, ...]
    - compute:
        - <name = expr>
    - assert:
        - <lhs = rhs>
    - send: [<term>, ...]
```

All text in the YAML document must be English. Output only the YAML document, no Markdown fences.

---

## 4. threat_model.md template

```markdown
# Threat Model

## Adversary Capabilities
- <capability 1>
- <capability 2>

## Security Requirements
- <secrecy requirement: which key or payload, under what session scope>
- <authentication requirement: who authenticates whom on which message; agreement strength>
```

One bullet per sentence, natural English, no extra sections.

---

## 5. Tamarin output rules

### 5.A Action fact conventions (MUST follow exactly)

#### 5.A.1 Step-marking facts (consumed by `executable`)

```
Test1()
Test2()
...
TestN()
```

- One per action block. Numbered 1..N in the protocol's natural execution order.
- **Parameterless** (no arguments). Do not parameterize them; the extractor matches by name prefix only and arguments are dead weight.
- Emit each `Test<i>()` in exactly one rule (the rule that implements action block `i`). Two rules emitting the same `Test<i>()` makes the extracted sequence non-deterministic.
- Action facts only — they appear inside the `--[ ... ]->` arrow of a rule, never on the LHS or RHS.

#### 5.A.2 Safety-lemma action facts

```
Create(A, tid)                  -- A creates session instance tid
Running(A, B, <role, params>)   -- A in role believes it is running with B
Commit(A, B, <role, params>)    -- A in role agrees with B on params
Secret(A, B, x)                 -- x is secret with respect to {A, B}
Honest(X)                       -- emitted in the same rule as Commit/Secret
LtkReveal(X)                    -- adversary reveals X's long-term private key
```

The parameter tuples of `Running` and `Commit` MUST be identical in order, content, and types. The role identifier MUST appear inside the parameter tuple.

### 5.B Safety-property lemma format

Every safety-property lemma MUST carry a `// category: <X>` annotation on the line immediately before `lemma <name>:`. Categories:

```
secrecy | aliveness | weak_agreement | noninjective_agreement | injective_agreement
```

Names are free-form lowercase identifiers, unique within the theory. Multiple lemmas may share a category when the protocol distinguishes multiple secrets or authentication targets.

Canonical templates (adapt parameter tuples to the protocol):

```tamarin
// category: secrecy
lemma secrecy:
  "All A B x #i.
     Secret(A, B, x) @ i
   ==>
       not (Ex #j. K(x) @ j)
     | (Ex X #r. LtkReveal(X) @ r & Honest(X) @ i)"

// category: aliveness
lemma aliveness:
  "All a b t #i.
     Commit(a, b, t) @ i
   ==>
       (Ex id #j. Create(b, id) @ j)
     | (Ex X #r. LtkReveal(X) @ r & Honest(X) @ i)"

// category: weak_agreement
lemma weak_agreement:
  "All a b t #i.
     Commit(a, b, t) @ i
   ==>
       (Ex t2 #j. Running(b, a, t2) @ j)
     | (Ex X #r. LtkReveal(X) @ r & Honest(X) @ i)"

// category: noninjective_agreement
lemma noninjective_agreement:
  "All a b t #i.
     Commit(a, b, t) @ i
   ==>
       (Ex #j. Running(b, a, t) @ j)
     | (Ex X #r. LtkReveal(X) @ r & Honest(X) @ i)"

// category: injective_agreement
lemma injective_agreement:
  "All a b t #i.
     Commit(a, b, t) @ i
   ==>
       ( (Ex #j. Running(b, a, t) @ j
             & j < i
             & not (Ex a2 b2 #i2. Commit(a2, b2, t) @ i2
                                & not (#i2 = #i))) )
     | (Ex X #r. LtkReveal(X) @ r & Honest(X) @ i)"
```

Emit a lemma only if the protocol description states or clearly implies the corresponding goal.

### 5.C Mandatory `executable` lemma

The skeleton, with one conjunct per action block:

```tamarin
lemma executable:
  exists-trace
  "Ex #t1 #t2 ... #tN.
       Test1() @ #t1 & Test2() @ #t2 & ... & TestN() @ #tN
     & #t1 < #t2 & #t2 < #t3 & ... & #t(N-1) < #tN"
```

**What this lemma is for.** The runner extracts the canonical action-block sequence from `executable`'s proof graph. The extractor walks the graph in chronological order and picks every node whose action-fact name starts with `Test` (case-insensitive); each such node maps to one action block. So:

- Every rule corresponding to an action block MUST emit exactly one `Test<i>()` (section 5.A.1).
- The `executable` lemma MUST reference all `Test<i>()` facts and order them with `#t<i> < #t<i+1>` so Tamarin returns a witness that fires them in protocol order.
- If the trace lacks any `Test<i>()`, that action block is silently dropped from the extraction; if `executable` cannot be proved at all, this protocol is unscorable.

**Hard requirements.**

- Lemma name MUST be exactly `executable` (lowercase).
- Trace mode MUST be `exists-trace`.
- NO `// category:` annotation (categories are for safety lemmas only).
- The body's `Ex` prefix MUST bind every temporal variable referenced (`#t1 ... #tN`).
- One `Test<i>() @ #t<i>` per action block; consecutive `#t<i> < #t<i+1>` constraints chaining all of them.

**Worked example: 3-step challenge–response.**

Suppose the protocol has three action blocks: reader generates challenge, tag responds, reader verifies.

Rules:

```tamarin
rule Reader_Start:
  [ Fr(~r1) ]
  --[ Test1() ]->
  [ Out(~r1), St_R_1($R, $T, ~r1) ]

rule Tag_Respond:
  [ In(r1), !Ltk(k), !Id(id) ]
  --[ Test2() ]->
  [ Out(<id, h(r1, k)>) ]

rule Reader_Finish:
  [ St_R_1($R, $T, ~r1), !Ltk(k), In(<id, hv>) ]
  --[ Test3(), Eq(hv, h(~r1, k)), Commit($R, $T, <'reader', ~r1, id>) ]->
  [ ]
```

Executable lemma:

```tamarin
lemma executable:
  exists-trace
  "Ex #t1 #t2 #t3.
       Test1() @ #t1 & Test2() @ #t2 & Test3() @ #t3
     & #t1 < #t2 & #t2 < #t3"
```

That witness chain forces Tamarin to find a trace that fires every step in order; the runner reads exactly the three `Test*` nodes from the proof graph and emits a 3-element action-block sequence.

**Common mistakes.**

- Using `Commit`, `Finish`, or any name not starting with `Test` → `extract_action_blocks` returns empty.
- Parameterizing `Test1(...)` instead of leaving it bare → still parsed (matched by name prefix), but the arguments are dead weight; keep them empty.
- Forgetting to emit a `Test<i>()` in some rule → that block is missing from the extracted sequence.
- Two rules emitting the same `Test<i>()` → extracted sequence is non-deterministic; assign unique indices.
- Skipping the `#t<i> < #t<i+1>` ordering → Tamarin may return a witness where steps fire out of protocol order, producing a wrong action-block sequence.
- Forgetting to bind `#t<i>` under `Ex` → wellformedness error.
- Adding `// category:` above `executable` → the runner mistakes it for a safety lemma.

### 5.D Theory frame

```tamarin
theory <Name>
begin

builtins: <only what the protocol uses>

// (optional) function declarations
// (optional) equations

<rules>

<safety lemmas with // category: ...>

<executable lemma>

end
```

`<Name>` is a single CamelCase identifier. Use only built-ins required by the protocol (`hashing`, `signing`, `asymmetric-encryption`, `symmetric-encryption`, `diffie-hellman`, `xor`).

If you are unsure which builtins the protocol needs, or how a specific
construct (let-bindings, restrictions, equational theories, multiset
rewriting nuances) should be written, consult the `tamarin-manual` skill
rather than guessing.

---

## 6. Variant runs

When the working directory's `document.txt` describes an **action variant** or **weakness variant** of a base protocol (the description will say so explicitly), the same pipeline applies and the same single output (`model.spthy`) is required. The variant's input description supersedes the base protocol; do not reuse outputs from any other directory.

For a **weakness variant** the expected verdict for the targeted lemma is `falsified` (the variant injects a deliberate weakness); use Tamarin (via the `tamarin-operations` skill) to confirm the falsification reflects a genuine attack and not a modeling mistake before exiting.
