import pytest

from jobinja_mcp.vocab import normalize, resolve, resolve_many

OPTIONS = {
    "وب،‌ برنامه‌نویسی و نرم‌افزار": "وب،‌ برنامه‌نویسی و نرم‌افزار",
    "web, software development": "وب،‌ برنامه‌نویسی و نرم‌افزار",
    "پشتیبانی و امور مشتریان": "پشتیبانی و امور مشتریان",
    "support and Customer service": "پشتیبانی و امور مشتریان",
}


def test_normalize_ignores_arabic_letters_zwnj_and_punctuation():
    assert normalize("كيفيت") == normalize("کیفیت")
    assert normalize("وب،‌ برنامه‌نویسی") == normalize("وب برنامهنویسی")


@pytest.mark.parametrize(
    "value",
    ["web, software development", "Web Software Development", "وب برنامه نویسی و نرم افزار", "software"],
)
def test_resolve_matches_loose_spellings(value):
    assert resolve(value, OPTIONS, "job category") == "وب،‌ برنامه‌نویسی و نرم‌افزار"


def test_resolve_unknown_lists_options():
    with pytest.raises(ValueError, match="Unknown job category 'astronomy'"):
        resolve("astronomy", OPTIONS, "job category")


def test_resolve_many_dedupes():
    assert resolve_many(["support", "پشتیبانی و امور مشتریان"], OPTIONS, "job category") == ["پشتیبانی و امور مشتریان"]
    assert resolve_many(None, OPTIONS, "job category") == []
