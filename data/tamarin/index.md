# Tamarin Manual Resource Library Index

This index lists every document under `data/tamarin/`. Each entry gives
the relative path, a one-line English summary, and the prep stages it most
directly supports. The five prep stages are: `serialization`, `rules`,
`threat_model`, `properties`, `analysis`.

## Foundations

- intro.md: Tamarin tool overview, multiset rewriting model, Dolev-Yao adversary, trace properties versus observational equivalence; stages: serialization, rules, properties, analysis.
- prelims.md: Manual writing conventions, spthy file organization rules, indexing notation; stages: serialization (auxiliary).
- installation.md: Installation on macOS, Linux, Windows WSL, source compilation, command-line entry points; stages: none (environment setup).
- example.md: End-to-end walkthrough of a client-server protocol from spthy authoring to executable and secrecy lemma verification; stages: serialization, rules, properties, analysis.

## Modeling

- cryptographic_messages.md: Term construction, public and fresh constants, function symbol declaration, equation specification, builtin theories such as symmetric-encryption, asymmetric-encryption, signing, hashing, Diffie-Hellman; stages: serialization, rules.
- protocol_specification_rules.md: Multiset rewriting rules, linear and persistent and action fact semantics, Naxos protocol modeling case, message variables and sorts; stages: serialization, rules.
- protocol_specification_processes.md: SAPIC+ stateful applied pi-calculus, process syntax, isolated execution environments, translation between processes and rules; stages: serialization, rules.
- syntax_description.md: Complete BNF grammar of the spthy theory format, theory keyword, function and equation and lemma subgrammar; stages: serialization, rules, properties.

## Properties and Threat Model

- property_specification.md: Lemma keyword, All and Ex quantifiers, timepoint variables with hash prefix, at-binding, implication, trace properties via guarded first-order logic, observational equivalence via diff; stages: properties, threat_model.
- accountability.md: Accountability framework, case tests, verdict functions, verification conditions, Corrupted action fact, deviating party detection; stages: properties, threat_model.

## Analysis and Verification

- advanced_features.md: Proof heuristics with rankings such as s, S, c, C, i, I, o, O, GUI workflow, channel models, induction, internal preprocessor, benchmarking; stages: analysis.
- precomputation.md: Source refinement, partial deconstruction handling, oracle-based source lemmas, Needham-Schroeder-Lowe public-key example; stages: analysis, rules.
- modeling_issues.md: First-time modeling pitfalls, exist-trace sanity lemmas, error message diagnosis, recovery from missing state facts; stages: rules, analysis.
- limitations.md: Symbolic model boundaries, subterm-convergent equation constraints, observational equivalence approximation, undecidability and termination caveats; stages: analysis, threat_model.

## Reference and Ecosystem

- case_studies.md: Pointer to the examples directory in the Tamarin repository with paper-associated protocol models and feature illustrations; stages: serialization, rules.
- toolchains.md: Tree-sitter grammar for spthy, Alice and Bob notation translator, Tamarin-Troop multi-prover dispatcher; stages: none (auxiliary tooling).
