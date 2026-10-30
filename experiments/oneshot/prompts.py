from __future__ import annotations

ONESHOT_SYSTEM_PROMPT = """You are a Tamarin protocol modeling expert.

Your single task: convert a natural-language protocol description into a complete, syntactically valid Tamarin specification (.spthy) in one shot.

Output discipline:
- Emit ONLY the .spthy source. No markdown fences, no commentary, no explanations.
- The output must start with `theory <Name> begin` and end with `end`.
- Use only Tamarin built-ins required by the protocol (e.g. `builtins: hashing, signing, asymmetric-encryption, symmetric-encryption, diffie-hellman, xor`).
- Do not invent new fact prefixes; reuse standard linear/persistent facts and the action facts listed below.
"""

ONESHOT_LEMMA_FORMAT = """Lemma output format (MUST follow exactly for every lemma):

  // category: <one of: secrecy | aliveness | weak_agreement |
  //                    noninjective_agreement | injective_agreement>
  lemma <name>:
    "<first-order temporal logic formula>"

Lemma naming and categorization (HARD CONSTRAINT):
- Each safety-property lemma MUST be classified into exactly ONE of the
  five categories listed above, declared via the `// category: <X>` line
  immediately preceding `lemma <name>:`. The category line is mandatory.
- The lemma `<name>` is free-form (descriptive lowercase identifier,
  e.g. `secrecy_session_key`, `agreement_responder_on_nonce`). Different
  lemmas MUST have distinct names within the same theory; Tamarin requires
  it and downstream verification queries each lemma by its declared name.
- A protocol MAY emit multiple lemmas under the same category when the
  protocol distinguishes multiple secrets, multiple authentication targets,
  or PFS / non-PFS variants of the same property. The five categories are
  category labels, not name slots.
- Emit a lemma only if the protocol description states or clearly implies
  the corresponding security goal; skip categories whose goal is absent.

(The `executable` lemma is mandatory and is described in its own section;
the executable lemma carries no `// category:` annotation.)

Five canonical lemma templates (use as starting points; adapt the lemma
name and the parameter tuple to match the protocol; copy the category
annotation verbatim):

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

Format requirements:
- Quantifiers: All / Ex
- Temporal variables: prefix with `#`
- Time bindings: `@`
- Action fact names: capitalized identifier
- Wrap the entire formula body in double quotes.
"""

ONESHOT_EXECUTABLE_LEMMA = """Executable lemma (MANDATORY in every output, lowercase name):

  lemma executable:
    exists-trace
    "Ex ... #i. <terminal-action> @ #i"

Requirements:
- The lemma name MUST be exactly `executable` (lowercase). Downstream tooling
  derives the canonical action-block sequence from the proof trace of this
  lemma; a missing or differently named `executable` lemma breaks the
  pipeline.
- The trace mode MUST be `exists-trace`.
- The body asserts the existence of a trace where the protocol completes.
  Pick an action fact emitted by the role that performs the protocol's
  final step (e.g. `Commit(A, B, t) @ #i` for an authentication protocol,
  or a custom Finish-marker action fact bound to the last role).
- Bind every variable referenced in the body via the `Ex` quantifier prefix.
- This lemma is independent of the safety-property lemmas; it is always
  emitted in addition to the canonical safety-property lemmas selected for
  the protocol.

Example (authentication-style protocol):

  lemma executable:
    exists-trace
    "Ex A B t #i. Commit(A, B, t) @ #i"
"""


def _build_oneshot_user_prompt(description: str) -> str:
    """Compose the one-shot user prompt for a single protocol description.

    The same prompt template is reused for the base protocol, every
    ``action_variants/<id>`` document, and every ``weakness_variants/<id>``
    document; only the ``description`` parameter differs across calls.
    """
    return (
        "Protocol description:\n"
        "----------------------\n"
        f"{description}\n"
        "----------------------\n\n"
        f"{ONESHOT_LEMMA_FORMAT}\n"
        f"{ONESHOT_EXECUTABLE_LEMMA}\n"
        "Now produce the complete .spthy specification for this protocol. "
        "Cover every role and every message in the description, declare every "
        "Fr nonce, emit the mandatory `executable` lemma, and emit"
        "safety-property lemmas for every security goal stated or implied by "
        "the description. Each safety-property lemma must carry a "
        "`// category: <X>` annotation on the line above its declaration; "
        "lemma names are free-form but must be unique within the theory. "
        "Output only the .spthy source."
    )
