# Concept Requirements

This directory contains focused coverage guardrails for Phase 1 lesson generation.

These files are not a KB layer and are not full lesson specs. They exist only where a topic has high drift
risk, merged scope, usage traps, contrast pairs, or forms that must appear for a lesson to be complete.

Each file is keyed by active curriculum slug and may define:

- `required_anchor_forms` — forms or phrases the generated lesson should cover.
- `min_clean_examples` — minimum clean Bokmål examples for the concept.
- `notes` — concept-specific watch points for prompt rendering and human read.

Not every active topic needs a requirement file. Low-risk vocabulary/list topics, broad strategy topics, and
genre-writing tasks may be better governed by the general rubric and human read.

Currently intentional no-file topics:

- `adjective_as_adverb`
- `advanced_word_formation`
- `basic_text_genres_messages_emails`
- `cardinal_numbers`
- `coordinating_conjunctions`
- `degree_manner_place_adverbs`
- `ellipsis_anaphora`
- `ordinal_numbers`
- `personal_pronouns_subject`
- `short_explanation_opinion_writing`
- `time_frequency_adverbs`
- `topicalization`
- `word_formation`
- `zero_article`
