---
name: tamarin-manual
description: Where the local Tamarin reference manual lives in the working directory and how to navigate it. Use this skill whenever you are uncertain about Tamarin syntax, builtin semantics (`hashing`, `signing`, `asymmetric-encryption`, `diffie-hellman`, `xor`, `symmetric-encryption`), multiset-rewriting rule shape, action-fact binding inside `--[ ... ]->`, restriction syntax, lemma quantifier shape, equational theories, or the meaning of any parser / wellformedness error message.
---

# Tamarin manual

The Tamarin Prover Manual is mirrored under

    ./tamarin_manual/

inside the working directory. Treat it as the **single source of truth**
for everything Tamarin-specific. Do NOT rely on training memory for
Tamarin syntax or semantics — read the manual.

When to consult it (non-exhaustive):

- before the first `files.write model.spthy`, to confirm rule shape,
  action-fact binding, and which builtins the protocol needs;
- in the middle of writing a rule, when you are unsure about pattern
  matching, restrictions, let-bindings, or equational theories;
- when interpreting a parse-error or wellformedness-error `stderr`
  produced by `tamarin-prover` (see the `tamarin-operations` skill);
- when deciding whether a canonical lemma template (secrecy,
  aliveness, weak / non-injective / injective agreement) applies as
  written to the protocol at hand;
- before introducing `xor` or `diffie-hellman`, since both interact
  with Tamarin's equational unifier in subtle ways.

---

## How to navigate

Start at the top-level index:

    ./tamarin_manual/index.md

then follow the cross-references it lists. The manual pages live in the
same directory (one Markdown file per chapter); every internal link in
`index.md` resolves to one of them.

### Page index

Each page below is a Markdown file under `./tamarin_manual/`. Read the
page whose topic matches the question you have right now — do not read
the whole manual end-to-end.

- `intro.md` — what Tamarin is, the symbolic model, what counts as a
  "protocol" in this framework.
- `prelims.md` — preliminaries: terms, sorts, equational theories, the
  message language Tamarin reasons over.
- `cryptographic_messages.md` — the cryptographic builtins
  (`hashing`, `signing`, `asymmetric-encryption`,
  `symmetric-encryption`, `diffie-hellman`, `xor`,
  `multiset`/`bilinear-pairing`) and the equations each one ships
  with. Read this before adding `builtins:`.
- `protocol_specification_rules.md` — how to write multiset-rewriting
  rules: linear vs persistent facts (`!`), `Fr`/`In`/`Out` builtins,
  let-bindings, restrictions, action-fact lists. The primary
  reference for everything between `theory ... begin` and the lemma
  block.
- `protocol_specification_processes.md` — the alternative process
  calculus front-end (SAPIC). You are *not* using this in this
  pipeline — the rule front-end is required — but consult it if a
  reference cites a process construct.
- `property_specification.md` — lemma quantifier syntax (`All`, `Ex`,
  `==>`, `&`, `|`, `not`, `<`, `=`), `exists-trace` vs default trace
  mode, and the meaning of action-fact patterns inside lemma bodies.
  Read this when shaping a safety-property lemma or the `executable`
  lemma.
- `precomputation.md` — how Tamarin's case distinction / source-lemma
  precomputation works. Skim if a proof seems unexpectedly slow.
- `advanced_features.md` — destructors, function declarations,
  restrictions in detail, oracle integration. Consult on demand.
- `modeling_issues.md` — common modelling mistakes (state-fact
  threading, fresh-name placement, wellformedness pitfalls). High-yield
  reading when `verify_executable` falsifies and you cannot see why.
- `accountability.md` — the accountability extension. Skip unless the
  protocol description explicitly asks for accountability properties.
- `case_studies.md` — published Tamarin case studies. Useful as
  worked examples when the protocol resembles one.
- `example.md` — a single hand-walked Naxos-style example.
- `installation.md`, `toolchains.md`, `limitations.md` — environment /
  tooling chapters; usually irrelevant inside this skill.
- `syntax_description.md` — the grammar reference. Use as a final
  arbiter when two other pages disagree.

---

## How to use it efficiently

- Read `index.md` first to orient yourself.
- Open the *most specific* page for your question (e.g.
  `cryptographic_messages.md` for builtin semantics,
  `property_specification.md` for lemma syntax). Do not load the
  entire manual.
- Treat the manual as authoritative when it conflicts with anything
  you "remember" about Tamarin.
- The manual is read-only. Do not write to `./tamarin_manual/`.
