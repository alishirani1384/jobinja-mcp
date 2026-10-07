from conftest import fixture_text

from jobinja_mcp.parsing import (
    parse_company_about,
    parse_company_categories,
    parse_company_jobs,
    parse_company_list,
    parse_company_overview,
    parse_job_detail,
    parse_job_search,
    parse_top_companies,
)


def test_job_search_page():
    r = parse_job_search(fixture_text("jobs.html"))
    assert r["total_results"] == 16774
    assert (r["page"], r["total_pages"], r["has_next"]) == (1, 839, True)
    assert r["count"] == len(r["jobs"]) == 20
    first = r["jobs"][0]
    assert first["job_id"] == "tNcX"
    assert first["company_slug"] == "vira-449"
    assert first["location"] == "تهران، تهران"
    assert first["posted_days_ago"] == 21
    assert first["salary_hidden"] is True and first["salary"] is None
    assert r["jobs"][1]["premium"] is True
    assert all(j["url"].startswith("https://jobinja.ir/companies/") for j in r["jobs"])


def test_job_detail_page():
    d = parse_job_detail(fixture_text("job.html"), "https://jobinja.ir/companies/hesaba/jobs/tEk6/x")
    assert d["job_id"] == "tEk6"
    assert d["url"] == "https://jobinja.ir/companies/hesaba/jobs/tEk6"
    assert d["apply_url"] == d["url"] + "/apply"
    assert d["title"] == "ReactJs) Senior Front-End Developer)"
    assert d["company"]["slug"] == "hesaba"
    assert d["company"]["name_en"] == "Hesaba"
    assert d["company"]["website"] == "https://www.hesaba.co"
    assert d["category"] == ["وب،‌ برنامه‌نویسی و نرم‌افزار"]
    assert d["skills"] == ["ReactJS", "Front-end", "Javascript"]
    assert d["military_status"] == ["معافیت تحصیلی", "معافیت دائم", "پایان خدمت"]
    assert d["salary"] == "توافقی"
    assert d["employment_type_code"] == "FULL_TIME"
    # The page's JSON-LD claims TELECOMMUTE; the visible info box says in-person.
    assert d["remote"] is False
    assert d["already_applied"] is False
    assert d["description"].startswith("شرح شغل")
    assert d["similar_jobs"] and d["similar_jobs"][0]["job_id"]


def test_company_directory_and_categories():
    html = fixture_text("companies.html")
    r = parse_company_list(html)
    assert r["count"] == len(r["companies"]) > 0
    first = r["companies"][0]
    assert first["slug"] == "batterybuzz"
    assert first["name_en"] == "BatteryBuzz"
    assert first["hiring"] is True
    datatejarat = next(c for c in r["companies"] if c["slug"] == "datatejarat")
    assert datatejarat["location"] is None and datatejarat["category"] == "بانکداری"

    cats = parse_company_categories(html)
    assert cats[0]["name"] == "کامپیوتر، فناوری اطلاعات و اینترنت"
    assert cats[0]["company_count"] == 28296
    assert any(c["slug"] == "other" for c in cats)


def test_company_search_results():
    r = parse_company_list(fixture_text("csearch.html"))
    assert r["companies"]
    assert all(c["slug"] and c["url"] for c in r["companies"])


def test_top_companies():
    top = parse_top_companies(fixture_text("top50.html"))
    assert len(top) == 50
    assert top[0]["rank"] == 1 and top[0]["slug"] == "rirais"
    assert top[0]["rating"] == 9.7
    assert top[0]["scores"]["محبوبیت میان کارجویان"] == 10


def test_company_overview():
    c = parse_company_overview(fixture_text("company.html"), "batterybuzz")
    assert c["name_en"] == "BatteryBuzz"
    assert c["premium"] is True
    assert c["open_jobs_count"] == 4
    assert c["website"] == "https://batterybuzz.ir"
    assert c["cover_image"].endswith("/main.png")
    assert c["coordinates"] == {"lat": 35.69095199534768, "lng": 51.4646790858083}
    assert c["members"][0]["role"] == "مدیر مارکتینگ"


def test_company_about():
    a = parse_company_about(fixture_text("company_about.html"), "batterybuzz")
    titles = [s["title"] for s in a["sections"]]
    assert titles[:2] == ["معرفی شرکت باتری باز", "فرهنگ کاری شرکت باتری باز"]
    assert all(s["body"] for s in a["sections"])


def test_company_jobs():
    j = parse_company_jobs(fixture_text("company_jobs.html"), "batterybuzz")
    assert j["open_jobs_count"] == 4
    assert len(j["open_jobs"]) == 4
    assert len(j["expired_jobs"]) == 3
    assert j["open_jobs"][0]["job_id"] == "tuJQ"
    assert j["open_jobs"][0]["posted_days_ago"] == 0
    assert all(job["company_slug"] == "batterybuzz" for job in j["open_jobs"] + j["expired_jobs"])
