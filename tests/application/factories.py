"""Not a check itself — factory_boy factories for incidental application operation values."""

from __future__ import annotations

import factory

from lesson_builder.application.operations.author_preview import AuthorPreview
from lesson_builder.application.operations.workbench_lessons import WorkbenchLesson
from lesson_builder.application.operations.workbench_records import ReviewRun
from lesson_builder.application.operations.workbench_records import StageApprovalRecord


class WorkbenchLessonFactory(factory.Factory):
    """One picker row for a canonical curriculum lesson."""

    class Meta:
        model = WorkbenchLesson

    lesson_id = factory.Sequence(lambda index: f"lesson_{index}")
    title = factory.Sequence(lambda index: f"Lesson {index}")
    cefr_level = "A1"
    sequence_index = factory.Sequence(lambda index: index)
    source = "canonical"


class AuthorPreviewFactory(factory.Factory):
    """A compiled lesson view with an empty packet and a clean audit."""

    class Meta:
        model = AuthorPreview

    lesson_id = "demo"
    source_label = "content/lessons/demo"
    lesson_source = ""
    exercise_source = ""
    plan_source = ""
    lesson = None
    packet = factory.LazyFunction(
        lambda: {"title": "Demo", "cefr_level": "A1", "kind": "grammar", "content": [], "sections": [], "exercises": []}
    )
    audit = factory.LazyFunction(lambda: {"findings": []})
    errors = ()


class ReviewRunFactory(factory.Factory):
    """An empty current-source review record for pane-state scenarios."""

    class Meta:
        model = ReviewRun

    digest = "source-digest"
    plan_sha256 = "plan-digest"
    reviewer_version = "reviewer-version"
    scope = "lesson"
    key = None
    created_at = "2026-10-06T00:00:00+00:00"
    findings = factory.LazyFunction(list)
    summary = "Review completed."


class StageApprovalRecordFactory(factory.Factory):
    """One source-bound workbench stage approval record."""

    class Meta:
        model = StageApprovalRecord

    lesson_id = "demo"
    stage = "plan"
    file_sha256 = "0" * 64
    upstream_approved_sha256 = None
    curriculum_slot_sha256 = None
    approved_at = "2026-10-06T00:00:00+00:00"
    code_commit = "test-commit"
    review = None
    proposals_accepted = 0
    proposals_rejected = 0
    acceptance_statement = "Owner acceptance is not qualified Norwegian review."


__all__ = [
    "AuthorPreviewFactory",
    "ReviewRunFactory",
    "StageApprovalRecordFactory",
    "WorkbenchLessonFactory",
]
