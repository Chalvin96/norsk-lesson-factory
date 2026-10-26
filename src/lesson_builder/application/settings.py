"""Not a check itself - application capability constants."""

from __future__ import annotations

from typing import Literal

from lesson_builder.clients.codex.settings import K_CODEX_WORKBENCH_CHAT_EFFORT_OPTIONS
from lesson_builder.clients.codex.settings import K_CODEX_WORKBENCH_CHAT_MODEL_OPTIONS

K_APPLICATION_MIN_PROMPTFOO_NODE_VERSION: tuple[int, int, int] = (22, 22, 0)
K_APPLICATION_AUDIO_COST_PER_MILLION_CHARS_USD = 30.0
K_APPLICATION_AUDIO_COST_ROUNDING_INCREMENT_USD = 0.01
K_APPLICATION_WORKBENCH_BUILD_SOURCE_DIR = "workbench"
K_APPLICATION_WORKBENCH_BUILD_PACKAGE_LOCKFILE = "package-lock.json"
K_APPLICATION_WORKBENCH_BUILD_INSTALLED_LOCKFILE_DIGEST_FILENAME = ".package-lock-digest.sha256"
K_APPLICATION_WORKBENCH_BUILD_SOURCE_FILES = (
    "index.html",
    "package.json",
    K_APPLICATION_WORKBENCH_BUILD_PACKAGE_LOCKFILE,
    "vite.config.ts",
)
K_APPLICATION_WORKBENCH_BUILD_METADATA_FILENAME = "workbench-build-source.sha256"
K_APPLICATION_WORKBENCH_BUILD_NODE_MODULES_DIR = "node_modules"
K_APPLICATION_WORKBENCH_BUILD_NODE_BINARY = "node"
K_APPLICATION_WORKBENCH_BUILD_NPM_BINARY = "npm"
K_APPLICATION_WORKBENCH_BUILD_INSTALL_COMMAND = ("npm", "ci")
K_APPLICATION_WORKBENCH_BUILD_COMMAND = ("npm", "run", "build")

__all__ = [
    "K_APPLICATION_MIN_PROMPTFOO_NODE_VERSION",
    "K_APPLICATION_AUDIO_COST_PER_MILLION_CHARS_USD",
    "K_APPLICATION_AUDIO_COST_ROUNDING_INCREMENT_USD",
    # Workbench app build configuration.
    "K_APPLICATION_WORKBENCH_BUILD_SOURCE_DIR",
    "K_APPLICATION_WORKBENCH_BUILD_PACKAGE_LOCKFILE",
    "K_APPLICATION_WORKBENCH_BUILD_INSTALLED_LOCKFILE_DIGEST_FILENAME",
    "K_APPLICATION_WORKBENCH_BUILD_SOURCE_FILES",
    "K_APPLICATION_WORKBENCH_BUILD_METADATA_FILENAME",
    "K_APPLICATION_WORKBENCH_BUILD_NODE_MODULES_DIR",
    "K_APPLICATION_WORKBENCH_BUILD_NODE_BINARY",
    "K_APPLICATION_WORKBENCH_BUILD_NPM_BINARY",
    "K_APPLICATION_WORKBENCH_BUILD_INSTALL_COMMAND",
    "K_APPLICATION_WORKBENCH_BUILD_COMMAND",
]

K_APPLICATION_WORKBENCH_FILES = ("plan.md", "lesson.md", "exercises.yaml")

K_APPLICATION_WORKBENCH_AUTO_FIX_RETRIES = 1
K_APPLICATION_WORKBENCH_CHAT_EXCERPT_LENGTH = 60
K_APPLICATION_WORKBENCH_DECISION_COMMENT_MAX_CHARS = 500
K_APPLICATION_WORKBENCH_REVIEW_EXERCISE_CRITERIA = (
    "answer_review",
    "attempt_surface",
    "open_rubrics",
    "open_semantics",
    "standalone",
)
K_APPLICATION_WORKBENCH_REVIEW_FALLBACK_QUOTE_LENGTH = 240
K_APPLICATION_WORKBENCH_REVIEW_VERSION = "workbench-review-v1"
K_APPLICATION_WORKBENCH_REVIEW_METADATA_TARGET = "@lesson-metadata"
K_APPLICATION_WORKBENCH_REVIEW_EXERCISE_PLACEMENT_TARGET = "@exercise-placement-order"
K_APPLICATION_WORKBENCH_REVIEW_MAX_CONCURRENT_CALLS = 6

K_APPLICATION_WORKBENCH_STAGES = (("plan", "plan.md"), ("lesson", "lesson.md"), ("exercises", "exercises.yaml"))
K_APPLICATION_WORKBENCH_STAGE_DEPENDENCIES = (("plan", None), ("lesson", "plan"), ("exercises", "lesson"))
K_APPLICATION_WORKBENCH_ACCEPTANCE_STATEMENT: Literal["Owner acceptance is not qualified Norwegian review."] = (
    "Owner acceptance is not qualified Norwegian review."
)


K_APPLICATION_WORKBENCH_DRAFT_REPAIRS = 2

K_APPLICATION_NEW_LESSON_PACKAGE_VERSION = "0.1-catalog-batch"
K_APPLICATION_NEW_LESSON_FAMILY_PLACEHOLDER = "Unassigned is an approvable family choice. You may select a family later by learner outcome from catalog_families.yaml in the catalog registry."
K_APPLICATION_NEW_LESSON_SIZE_TARGETS = {
    "A1": "11-14 sections, 2167-2852 source words, and 10-16 exercises",
    "A2": "11-15 sections, 2217-2657 source words, and 12-16 exercises",
    "B1": "10-15 sections, 2350-2874 source words, and 10-16 exercises",
    "B2": "10-15 sections, 2381-3254 source words, and 10-19 exercises",
}

K_APPLICATION_WORKBENCH_CURRICULUM_CACHE_SIZE = 8
K_APPLICATION_WORKBENCH_REVIEW_REASKS = 2
K_APPLICATION_WORKBENCH_UNORDERED_PLAN_KEYS = ("helpful_prerequisites", "helpful_prerequisites_later")

K_APPLICATION_WORKBENCH_OPENCODE_MODEL_OPTIONS = (
    "openai/gpt-6.1-sol",
    "openai/gpt-6-luna",
    "openai/gpt-6-astra",
    "zai-coding-plan/glm-5.3",
    "opencode-go/glm-5.3",
    "openai/gpt-5.6-luna",
    "openai/gpt-5.6-sol",
    "anthropic/claude-sonnet-5-5",
)
K_APPLICATION_WORKBENCH_OPENCODE_EFFORT_OPTIONS = ("low", "medium", "high", "xhigh")
K_APPLICATION_WORKBENCH_DEFAULT_SECOND_OPINION_MODEL = "anthropic/claude-sonnet-5-5"
K_APPLICATION_WORKBENCH_JOB_BY_ROLE = {
    "generation": "workbench_author",
    "lesson_review": "workbench_reviewer",
    "exercise_review": "workbench_reviewer",
}
K_APPLICATION_WORKBENCH_SETTINGS_MODEL_OPTIONS = {
    "chat": K_CODEX_WORKBENCH_CHAT_MODEL_OPTIONS,
    "generation": K_APPLICATION_WORKBENCH_OPENCODE_MODEL_OPTIONS,
    "lesson_review": K_APPLICATION_WORKBENCH_OPENCODE_MODEL_OPTIONS,
    "exercise_review": K_APPLICATION_WORKBENCH_OPENCODE_MODEL_OPTIONS,
    "second_opinion": K_APPLICATION_WORKBENCH_OPENCODE_MODEL_OPTIONS,
}
K_APPLICATION_WORKBENCH_SETTINGS_EFFORT_OPTIONS = {
    "chat": K_CODEX_WORKBENCH_CHAT_EFFORT_OPTIONS,
    "generation": K_APPLICATION_WORKBENCH_OPENCODE_EFFORT_OPTIONS,
    "lesson_review": K_APPLICATION_WORKBENCH_OPENCODE_EFFORT_OPTIONS,
    "exercise_review": K_APPLICATION_WORKBENCH_OPENCODE_EFFORT_OPTIONS,
}
