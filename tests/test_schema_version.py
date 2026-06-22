from lesson_builder.schema.version import SCHEMA_VERSION


def test_schema_version_is_3_0():
    assert SCHEMA_VERSION == "3.0"
