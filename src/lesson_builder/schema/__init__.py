"""Schema-3.0 contract package: the single source of truth for lesson shape."""

from lesson_builder.schema.elements import Element, Exercise, Operation, Section
from lesson_builder.schema.export import ExportedLesson, to_export_dict
from lesson_builder.schema.lesson import Lesson, Objective, Pool, PoolCard, ReviewPool
from lesson_builder.schema.normalize import normalize_for_compare
from lesson_builder.schema.selection import MIN_EXERCISES_PER_OBJECTIVE, eligible_operations
from lesson_builder.schema.version import SCHEMA_VERSION

__all__ = [
    "SCHEMA_VERSION",
    "Lesson",
    "Objective",
    "ReviewPool",
    "Pool",
    "PoolCard",
    "Element",
    "Section",
    "Exercise",
    "Operation",
    "ExportedLesson",
    "to_export_dict",
    "normalize_for_compare",
    "MIN_EXERCISES_PER_OBJECTIVE",
    "eligible_operations",
]
