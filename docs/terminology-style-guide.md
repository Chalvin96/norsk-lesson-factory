# Terminology Style-Guide (house style)

> **PRESCRIPTIVE.** One pinned term per concept. Lessons must use the pinned term;
> the LLM `terminology_consistency` reviewer compares each lesson against this guide,
> and the deterministic linter hard-blocks only phrases explicitly listed in the
> machine-readable bans block. Append a row when a terminology decision is made — this doubles
> as the decision log (ADR-style). Keep the ACTIVE RULES table compact; put any longer
> reasoning in the Rationale section so the injected reviewer context stays small.

## Active rules (the table the reviewer + authoring prompts are fed)

| Concept | Pinned term | Also OK | Guidance / hard bans | Level note |
|---|---|---|---|---|
| noun gender | **gender** | `noun gender`; `masculine`, `feminine`, `neuter`; `common gender` only for the 2-gender system; Norwegian label: `kjønn` | don't silently mix the 3-gender and common-gender SYSTEMS in one lesson | A1 |
| indefinite form | **indefinite form** | Norwegian label: `ubestemt form` | avoid `base form` for nouns where it can collide with verb/adjective base form | A1 |
| definite form | **definite form** | Norwegian label: `bestemt form` | not the same as the suffix or determiner | A1 |
| definite suffix | **definite suffix** | `definite ending` in A1/A2 explanations; Norwegian label: `bestemt suffiks` | avoid calling the noun suffix `definite article` when contrasting suffix vs determiner | A1 |
| definite article | **definite article** | `definite determiner` when contrasting word class; Norwegian label: `bestemt artikkel` | do not use `definite article` for the noun suffix in lessons that contrast the two | A2 |
| infinitive (å-form) | **infinitive** | Norwegian label: `infinitiv`; `å-form` in A1 explanations | do not call it "bare infinitive" — different concept | A1; the å-form: *å snakke* |
| infinitive marker | **infinitive marker** | `å`; Norwegian label: `infinitivsmerke` if needed | avoid "to-word" as the formal term | A1 |
| bare infinitive (post-modal) | **bare infinitive** | "plain verb form without å" / `infinitive without å` in A1/A2; Norwegian label: `infinitiv uten å` | do not call it just "infinitive" where the å/no-å contrast matters | A2; the å-LESS form: *snakke* after *kan* |
| finite verb | **finite verb** | A1/A2 first-use scaffold: **"verb that shows tense"**; Norwegian label: `finitt verb` | ❌`tense-carrying verb` (BANNED); avoid `conjugated verb`; do not repeat the scaffold as the lesson's main label after `finite verb` is introduced | A1/A2 may define once, then use `finite verb`; introduce term B1+ |
| present tense | **present tense** | Norwegian label: `presens` in tense tables | avoid unexplained `presens` in English prose; avoid bare `present` as a noun | A1 |
| past tense / preterite | **past tense** | `preterite`; Norwegian label: `preteritum` in tense tables | avoid unexplained `preteritum` in English prose | A2 |
| present perfect | **present perfect** | Norwegian label: `presens perfektum` in tense tables | avoid `perfect tense` | A2/B1 |
| future forms | **future forms** | `future expressions`; `future marker` for `skal`, `vil`, or `kommer til å`; `future tense` only after explaining Norwegian uses modal/periphrastic future forms | avoid implying Norwegian has a single inflected future tense | A2 |
| main clause | **main clause** | `independent clause`; Norwegian label: `helsetning` | avoid drifting to `independent clause` unless the contrast helps | A2 |
| subordinate clause | **subordinate clause** | `dependent clause`; Norwegian label: `leddsetning` | avoid `sub-clause` / `subclause` | A2 |
| subordinating conjunction | **subordinating conjunction** | `subordinating connector`; `subordinator` (B2+); Norwegian label: `subjunksjon` in tables | ❌`subjunction` (BANNED); avoid bare `connector` when ambiguous with coordinating *og/men* | A2+ |
| V2 rule | **V2 rule** | `verb-second rule`; `position 2`; `second position`; Norwegian label: `V2-regelen` | avoid `inversion rule` as the main label | A2 |
| first position | **first position** | `position 1`; `front field` (B1+); Norwegian label: `forfelt` | avoid `topic position` in learner prose | A1/A2 use scaffold; introduce `front field` B1+ |
| sentence adverb | **sentence adverb** | `sentence adverbial`, `clausal adverb`; Norwegian label: `setningsadverb` | avoid "middle adverb" | A2 |
| noun phrase | **noun phrase** | `noun group` only as A1/A2 scaffold; `NP` only in compact B-level tables; Norwegian label: `substantivfrase` | avoid `noun group` as the formal term in objectives, titles, and grammar rule labels | A2/B1 |
| connector | **connector** | `discourse connector`; `linking word` in A1/A2 discourse explanations | use `conjunction`, `coordinating conjunction`, or `subordinating conjunction` when teaching word class or clause word order | A2+; discourse/cohesion scope |
| reflexive pronoun | **reflexive pronoun** | `reflexive`; Norwegian label: `refleksivt pronomen` | do not use for `sin`, `si`, `sitt`, `sine` — use `reflexive possessive` | A2 |
| reflexive possessive | **reflexive possessive** | Norwegian labels: `sin`, `si`, `sitt`, `sine`; `refleksivt possessiv` | avoid calling it only a `reflexive pronoun` | A2/B1 |
| adjective agreement | **adjective agreement** | `adjective endings` in A1/A2 explanations; Norwegian label: `adjektivsamsvar` | avoid using only "endings" when gender/number/definiteness agreement is the point | A1/A2 |

**Prose-tells to avoid (deterministic warn):** `"X, meaning Y"` / `"X, which means Y"`
repeated (define a term once, then use it plainly); `"In this lesson, you will…"`; the
literal stacked apposition `"finite verb, the tense-carrying verb"`.

<!-- BEGIN MACHINE-READABLE BANS (parsed by the deterministic linter; edited by `lesson-data terminology ban/unban`. Keep in sync with the table above — this is the strict deterministic subset.) -->
```bans
ban: tense-carrying verb
ban: subjunction
ban: noun group
prose-tell: In this lesson, you will
prose-tell: finite verb, the tense-carrying verb
prose-tell-density: , meaning | , which means | 2
```
<!-- END MACHINE-READABLE BANS -->

**Register rule (CEFR):** This terminology guide wins over naturalness preferences. Standard
textbook terms are allowed at A1/A2 when the lesson needs the concept and the term is
introduced, scaffolded with a plain explanation, or already established in the lesson. Prefer
learner action / plain explanation near the first technical label; do not penalize the label
itself. B1/B2 may use technical labels earlier. Keep the new-grammar-label budget small in
A1/A2 (≈2–3 per lesson).

## Reference glossary (pinned, but not injected)

These rows pin ordinary learner-facing glossary terms for authors and human review. They are
not injected into the LLM reviewer prompt unless they also appear in Active rules.

| Concept | Pinned term | Also OK | Guidance / hard bans | Level note |
|---|---|---|---|---|
| noun | **noun** | Norwegian label: `substantiv` | avoid "name word" as a term | A1 |
| verb | **verb** | Norwegian label: `verb` | avoid "action word" as a term | A1 |
| adjective | **adjective** | Norwegian label: `adjektiv` | avoid "describing word" as a term | A1 |
| adverb | **adverb** | Norwegian label: `adverb` | do not confuse with `sentence adverb` | A1 |
| preposition | **preposition** | Norwegian label: `preposisjon`; abbreviation `prep` only in compact tables | avoid `particle` unless teaching particle verbs | A1 |
| pronoun | **pronoun** | Norwegian label: `pronomen` | — | A1 |
| conjunction | **conjunction** | `coordinating conjunction`; `joining word` / `linking word` in A1/A2 explanations; Norwegian label: `konjunksjon` | avoid bare `connector` when the contrast with subordinating terms matters | A2 |
| auxiliary verb | **auxiliary verb** | Norwegian label: `hjelpeverb`; `modal verb` when teaching `kan`, `må`, `vil`, `skal`, `bør` as modals | avoid "helping verb" as the formal term | A2 |
| modal verb | **modal verb** | `modal auxiliary`; Norwegian label: `modalverb` | avoid using `auxiliary verb` if the lesson specifically contrasts modals with non-modal auxiliaries | A2 |
| genitive | **genitive** | `possessive -s`; Norwegian label: `genitiv` | avoid `apostrophe-s` for Norwegian | A2 |
| compound noun | **compound noun** | `compound`; Norwegian label: `sammensatt substantiv` / `sammensatt ord` | avoid "word combo" as a term | A2/B1 |
| past perfect | **past perfect** | `pluperfect`; Norwegian label: `preteritum perfektum` | avoid "had + verb" as the term | B1 |
| future perfect | **future perfect** | Norwegian label: `futurum perfektum`; `future marker + ha + past participle` as scaffold | avoid treating `future marker` as a tense by itself | B1/B2 |
| past participle | **past participle** | Norwegian label: `perfektum partisipp` | avoid `perfect participle`, `pp`, `-t form` in learner prose | A2 |
| present participle | **present participle** | Norwegian label: `presens partisipp` | avoid `-ing form` for Norwegian `-ende` | B1 |
| imperative | **imperative** | `command form` in A1/A2 explanations; Norwegian label: `imperativ` | avoid "bare stem" as the learner-facing label | A2 |
| passive | **passive** | `passive voice`; Norwegian label: `passiv` | specify `bli-passive` or `s-passive` when relevant | B1 |
| passive subtype | **bli-passive** / **s-passive** | `bli-passive form`; `s-passive form` | lowercase and hyphenate in prose; do not replace the broad term `passive` when both subtypes are meant | B1 |
| subject | **subject** | Norwegian label: `subjekt` | avoid `doer` as a term | A1 |
| object | **object** | `direct object`, `indirect object`; Norwegian label: `objekt` | avoid "thing-word" | A1 |
| adverbial | **adverbial** | `adverbial phrase`; Norwegian label: `adverbial` | do not use `adverb` when the function, not the word class, is meant | A2 |
| predicative | **predicative** | `subject complement`, `predicative expression`; Norwegian label: `predikativ` | avoid "object of be" | A2 |
| relative clause | **relative clause** | Norwegian label: `relativsetning` | avoid "who/which clause" as the formal term | B1 |
| possessive | **possessive** | `possessive pronoun`, `possessive determiner`; Norwegian label: `possessivt pronomen` | avoid "owner word" as the formal term | A1/A2; refine determiner vs pronoun at B1 if needed |
| demonstrative | **demonstrative** | `demonstrative pronoun`, `demonstrative determiner`; Norwegian label: `demonstrativ` | avoid "pointing word" as the formal term | A2 |
| adjective base form | **base form** | `positive form`; Norwegian label: `positiv` in tables | avoid bare `positive` where learners may read it as sentiment | A1 |
| comparative | **comparative** | Norwegian label: `komparativ` | avoid "more form" as the formal term | A2 |
| superlative | **superlative** | Norwegian label: `superlativ` | avoid "most form" as the formal term | A2 |

## Rationale (human-only; NOT injected into the reviewer prompt)

- **finite verb / "verb that shows tense":** corpus audit found `finite verb` (354×),
  `tense-carrying verb` (38×), `verb that shows tense` (4×) used for the same concept —
  a concept-identity problem (a learner thinks they're different things). Pin `finite
  verb`; scaffold with the plain phrase once at A1/A2; ban the coined `tense-carrying
  verb`. Repeating `verb that shows tense` throughout a lesson is drift; define it, then use
  `finite verb`.
- **infinitive ≠ bare infinitive:** the infinitive is the å-form (*å snakke*); the bare
  infinitive is the å-less form after a modal (*kan snakke*). A lesson contrasting them is
  correct — they are NOT synonyms; an earlier glossary wrongly merged them.
- **definite form / suffix / determiner are three things:** *bilen* (the inflected noun) /
  *-en* (the suffix) / *den* (the determiner). Norwegian pedagogy often calls the suffix
  "the definite article" — avoid that label here, it collides with the determiner.
- **V2 rule ≠ front field:** V2 is a rule (finite verb is 2nd); the front field / position 1
  is a slot (before the verb). Different concepts; keep them distinct.
- **noun gender systems:** masculine/feminine/neuter is the three-gender paradigm; "common
  gender" is the two-gender system. They co-exist within their own system; mixing the two
  systems in one lesson should be a deliberate teaching choice, not accidental drift.
- **subordinating conjunction / `connector`:** external learner sources usually say
  `subordinating conjunction`; our earlier guide used `subordinating connector` to keep it
  distinct from coordinating conjunctions (*og, men*). Pin the standard learner term, allow
  the older house term, and ban only the genuinely-wrong `subjunction`.
- **`connector` is scoped, not a grammar default:** use it for discourse/cohesion lessons
  where the concept is linking ideas. In clause grammar, name the word class or pattern:
  `conjunction`, `coordinating conjunction`, or `subordinating conjunction`.
- **`noun phrase` replaces `noun group`:** external learner grammar and the existing guide
  favor `noun phrase`; the corpus had drifted to `noun group`. Keep `noun group` out of
  formal lesson text and deterministic review.
- **`definite suffix` stays pinned, `definite ending` stays learner-friendly:** A1/A2 prose
  may say `definite ending`, but objectives, titles, and contrastive grammar should use
  `definite suffix`.
- **`modal verb` and `auxiliary verb` stay separate:** learner sources often group modals as
  auxiliaries, but our course has dedicated modal lessons. Use `modal verb` for `kan`, `vil`,
  `må`, `skal`, `bør` when that subtype is the point; use `auxiliary verb` for compound
  tense/passive helpers such as `har`, `er`, `ble`.
- **`first position` is the early-level pin:** `front field` remains allowed at B1+ and in
  reference contexts; A1/A2 V2 lessons should normally use `first position` / `position 1`.

See [TERMINOLOGY_MAINTENANCE.md](TERMINOLOGY_MAINTENANCE.md) for the lesson-maintenance
workflow and operating notes behind these decisions.

> Maintenance: the deterministic hard-ban list (`tense-carrying verb`, `subjunction`,
> `noun group`, and the prose-tells) is explicit in the machine-readable bans block. Treat
> other guidance as LLM-reviewer/authoring guidance unless it is deliberately promoted into
> that block.
