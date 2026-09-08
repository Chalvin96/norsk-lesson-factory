---
package_version: 0.1-catalog-package
lesson_id: question_word_order
kind: grammar
title: "Norwegian main-clause word order: questions and fronting"
cefr_level: A1
approved: true
approver: catalog-generation-reviewer
approved_source_hash: 64a622cb4bc9b722a366d0b84bde63d3af9b6bb575741e5e32bd9b1bf1e9ab69
objectives:
  - id: obj-question-order
    statement: Form and recognise Norwegian main-clause questions and fronted statements, including kan plus the infinitive.
source_refs:
  - tests/fixtures/lesson_packages/doctor_appointment/grammar/brief.md
success_criteria:
  - The learner can form a direct yes/no question.
  - The learner keeps the finite verb in second clause position after fronting.
  - The learner uses the infinitive after kan.
coverage:
  - id: cov-question-order
    objective: obj-question-order
    claim: Form and recognise Norwegian main-clause questions and fronted statements, including kan plus the infinitive.
    scope: required
    evidence: [explanation, example, contrast_example, controlled_practice]
exclusions:
  - appointment-call interaction
  - open speaking assessment
---

# Plan - question word order (catalog-generation fixture)

## Pedagogical job

This is a reusable grammar ingredient. It explains a direct rule and gives
controlled practice; it does not borrow the doctor-appointment situation as its
teaching sequence. The compile path is the known-valid grammar fixture source.

## Boundary

This is the approved catalog-generation fixture. The three source files are
plan.md, lesson.md, and exercises.yaml. The catalog-generation runtime copies these
into a disposable output directory, compiles them, and derives transcript.yaml
from the lesson and exercises so the approved fixture is never mutated.
