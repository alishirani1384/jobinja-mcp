import pytest

from jobinja_mcp.utils import (
    company_url,
    job_url,
    parse_company_slug,
    parse_days_ago,
    parse_int,
    parse_job_url,
    split_bilingual_name,
    strip_tracking,
    to_ascii_digits,
)


def test_persian_and_arabic_digits():
    assert to_ascii_digits("۱۲۳٤٥") == "12345"
    assert parse_int("۴۰,۰۰۰,۰۰۰ تومان") == 40_000_000
    assert parse_int("توافقی") is None


@pytest.mark.parametrize(
    ("label", "days"),
    [("(امروز)", 0), ("دیروز", 1), ("(۲۱ روز پیش)", 21), ("۲ ماه پیش", 60), ("", None)],
)
def test_parse_days_ago(label, days):
    assert parse_days_ago(label) == days


def test_parse_job_url_variants():
    url = "https://jobinja.ir/companies/hesaba/jobs/tEk6/%D8%A7%D8%B3%D8%AA%D8%AE%D8%AF%D8%A7%D9%85?_ref=16"
    assert parse_job_url(url) == ("hesaba", "tEk6", "استخدام")
    assert parse_job_url("/companies/hesaba/jobs/tEk6") == ("hesaba", "tEk6", None)
    with pytest.raises(ValueError):
        parse_job_url("https://jobinja.ir/companies/hesaba")


def test_company_slug_and_urls():
    assert parse_company_slug("batterybuzz") == "batterybuzz"
    assert parse_company_slug("https://jobinja.ir/companies/batterybuzz/jobs") == "batterybuzz"
    assert job_url("hesaba", "tEk6") == "https://jobinja.ir/companies/hesaba/jobs/tEk6"
    assert company_url("batterybuzz", "about") == "https://jobinja.ir/companies/batterybuzz/about"


def test_strip_tracking_keeps_other_params():
    assert strip_tracking("https://jobinja.ir/jobs?_ref=1&page=2&_t=x") == "https://jobinja.ir/jobs?page=2"
    assert strip_tracking("https://jobinja.ir/jobs") == "https://jobinja.ir/jobs"


def test_split_bilingual_name():
    assert split_bilingual_name("باتری باز | BatteryBuzz") == {
        "name": "باتری باز | BatteryBuzz",
        "name_fa": "باتری باز",
        "name_en": "BatteryBuzz",
    }
    assert split_bilingual_name("حسابا")["name_en"] is None
