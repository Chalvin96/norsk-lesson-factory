"""Not a check itself — canonical paths for test-owned fixtures."""

from pathlib import Path

K_FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"
K_CATALOG_PACKAGE_FIXTURE_ROOT = K_FIXTURES_ROOT / "catalog_package" / "fixture"
K_CARDINAL_NUMBERS_FIXTURE_ROOT = K_FIXTURES_ROOT / "lesson_packages" / "cardinal_numbers"
K_VALID_LESSON_SCHEMA_PATH = K_FIXTURES_ROOT / "schema" / "valid_lesson_v3.json"
K_ORDINAL_NUMBERS_EXPORT_PATH = K_FIXTURES_ROOT / "lesson_exports" / "ordinal_numbers.json"
