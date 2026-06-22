from lesson_builder.schema.normalize import normalize_for_compare


def test_collapses_whitespace():
    assert normalize_for_compare("Huset   er  stort") == "Huset er stort"


def test_preserves_case():
    assert normalize_for_compare("Huset") != normalize_for_compare("huset")


def test_preserves_trailing_punctuation():
    assert normalize_for_compare("Huset er stort.") == "Huset er stort."


def test_nfc_normalization():
    composed = "\u00e9"
    decomposed = "e\u0301"
    assert normalize_for_compare(decomposed) == normalize_for_compare(composed)


def test_preserves_internal_hyphen():
    assert normalize_for_compare("e-post") == "e-post"
