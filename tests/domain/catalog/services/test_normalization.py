"""Entry points: catalog-label normalization behavior tests."""

from lesson_builder.domain.catalog.services.normalization import normalize_slug
from lesson_builder.domain.catalog.services.normalization import normalize_text


def test_normalize_text_given_unicode_punctuation_and_spacing_expect_ascii_comparison_text():
    assert normalize_text(" På norsk—og på norsk! ") == "pa norskog pa norsk"


def test_normalize_slug_given_mixed_case_label_expect_stable_catalog_key():
    assert normalize_slug("På Norsk / Og") == "pa_norsk_og"
