"""Not a check itself — factory_boy factories for incidental curriculum plan values."""

from __future__ import annotations

import factory

from lesson_builder.domain.curriculum.models import CurriculumPlan
from lesson_builder.domain.curriculum.models import CurriculumPlanSlot


class CurriculumPlanSlotFactory(factory.Factory):
    """One approved, placed curriculum slot with a valid lesson slug."""

    class Meta:
        model = CurriculumPlanSlot

    sequence_index = factory.Sequence(lambda index: index)
    catalog_id = factory.Sequence(lambda index: f"lesson_{index}")
    catalog_kind = "grammar"
    title = factory.Sequence(lambda index: f"Lesson {index} title")
    learner_outcome = "Use the taught form in a short exchange."
    cefr_level = "A1"
    cefr_source = "catalog"


class CurriculumPlanFactory(factory.Factory):
    """A committed curriculum plan whose slots the caller usually supplies."""

    class Meta:
        model = CurriculumPlan

    schema_version = "0.5-catalog-plan"
    run_id = "test-run"
    source_catalog = "content/catalog/approved/catalog.yaml"
    source_snapshot = "2026-01-01"
    summary = factory.LazyFunction(dict)
    slots = factory.LazyFunction(lambda: [CurriculumPlanSlotFactory()])


__all__ = ["CurriculumPlanFactory", "CurriculumPlanSlotFactory"]
