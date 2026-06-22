import difflib
import json
from pathlib import Path

from lesson_builder.pipeline.lesson_export import lesson_from_export, lesson_to_export
from lesson_builder.schema import ExportedLesson

ROOT = Path(__file__).resolve().parents[2]
LESSON_EXPORT_FIXTURES = ROOT / "tests/fixtures/lesson_exports"


def _canon(d: dict) -> dict:
    return ExportedLesson.model_validate(d).model_dump(mode="json")


def test_lesson_export_fixtures_given_import_then_re_export_expect_all_roundtrip():
    failures = []
    for lesson_path in sorted(LESSON_EXPORT_FIXTURES.glob("*.json")):
        export_text = lesson_path.read_text()
        original = _canon(json.loads(export_text))
        exported = _canon(lesson_to_export(lesson_from_export(json.loads(export_text))))
        if exported != original:
            diff = "\n".join(
                difflib.unified_diff(
                    json.dumps(original, sort_keys=True, indent=2, ensure_ascii=False).splitlines(),
                    json.dumps(exported, sort_keys=True, indent=2, ensure_ascii=False).splitlines(),
                    lineterm="",
                    n=1,
                )
            )[:2000]
            failures.append(f"{lesson_path.stem}:\n{diff}")
    assert not failures, "non-reproducible exports:\n" + "\n---\n".join(failures[:3])
