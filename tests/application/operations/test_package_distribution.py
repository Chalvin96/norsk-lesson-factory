"""Entry point: `package_distribution` archive packaging behavior.

Only distribution validation is faked so packaging runs for real against a
temporary distribution: reproducible gzip/tar bytes, normalized member metadata,
and the atomic archive replacement.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

from lesson_builder.application.operations.package_distribution import package_distribution


def _write_distribution(root: Path) -> Path:
    """Write one minimal provider-free distribution and return its root."""
    dist = root / "dist"
    (dist / "schema").mkdir(parents=True)
    (dist / "catalog.json").write_text('{"lessons": []}\n', encoding="utf-8")
    (dist / "schema" / "lesson.schema.json").write_text("{}\n", encoding="utf-8")
    return root


def _patch_validation(monkeypatch) -> None:
    monkeypatch.setattr(
        "lesson_builder.application.operations.package_distribution.validate_committed_distribution",
        lambda repo_root, *, distribution_root: {"lesson_count": 0, "audio_file_count": 0},
    )


def test_package_distribution_given_same_validated_dist_expect_reproducible_archive(
    tmp_path: Path, monkeypatch
) -> None:
    distribution_root = _write_distribution(tmp_path / "distribution")
    _patch_validation(monkeypatch)

    first = package_distribution(
        repo_root=tmp_path,
        distribution_root=distribution_root,
        output_path=tmp_path / "first.tar.gz",
    )
    second = package_distribution(
        repo_root=tmp_path,
        distribution_root=distribution_root,
        output_path=tmp_path / "second.tar.gz",
    )

    assert first["sha256"] == second["sha256"]
    assert first["include_audio"] is True
    with tarfile.open(tmp_path / "first.tar.gz", "r:gz") as archive:
        assert set(archive.getnames()) == {
            "dist",
            "dist/catalog.json",
            "dist/schema",
            "dist/schema/lesson.schema.json",
        }


def test_package_distribution_given_audio_exclusion_expect_archive_omits_wav_bytes(tmp_path: Path, monkeypatch) -> None:
    distribution_root = _write_distribution(tmp_path / "distribution")
    audio = distribution_root / "dist" / "audio" / "lessons" / "present_tense" / "clip.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"wav-bytes")
    _patch_validation(monkeypatch)

    result = package_distribution(
        repo_root=tmp_path,
        distribution_root=distribution_root,
        output_path=tmp_path / "lessons.tar.gz",
        include_audio=False,
    )

    assert result["include_audio"] is False
    with tarfile.open(tmp_path / "lessons.tar.gz", "r:gz") as archive:
        names = archive.getnames()
    assert set(names) == {
        "dist",
        "dist/catalog.json",
        "dist/schema",
        "dist/schema/lesson.schema.json",
    }
    assert audio.is_file()
