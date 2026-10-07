"""Static vocabularies used by Jobinja's job search form.

Dynamic vocabularies (job categories, provinces, cities, company categories,
company sizes, popular skills) are fetched live from ``/api/v10/job_search_meta``.
"""

from __future__ import annotations

SORT_OPTIONS: dict[str, str] = {
    "relevance": "relevance_desc",
    "newest": "published_at_desc",
    "highest_salary": "salary_from_desc",
}

SORT_LABELS: dict[str, str] = {
    "relevance": "مرتبط بودن",
    "newest": "جدیدترین",
    "highest_salary": "بیشترین حقوق",
}

JOB_TYPES: dict[str, dict[str, str]] = {
    "full_time": {"value": "is_fulltime", "label": "تمام‌وقت"},
    "part_time": {"value": "is_parttime", "label": "پاره‌وقت"},
}

EXPERIENCE_LEVELS: dict[str, dict[str, str]] = {
    "any": {"value": "any", "label": "بدون محدودیت سابقه کار"},
    "under_3": {"value": "under_two", "label": "کمتر از سه سال"},
    "3_to_6": {"value": "3_6", "label": "سه تا شش سال"},
    "over_6": {"value": "7_plus", "label": "بیش از شش سال"},
}

# Values are monthly salaries in Toman; the site expects "<min>:" (open-ended upper bound).
SALARY_BRACKETS: list[dict[str, object]] = [
    {"min_toman": 10_300_000, "label": "حقوق پایه (وزارت کار)"},
    {"min_toman": 11_000_000, "label": "از یازده میلیون تومان"},
    {"min_toman": 15_000_000, "label": "از پانزده میلیون تومان"},
    {"min_toman": 20_000_000, "label": "از بیست میلیون تومان"},
    {"min_toman": 30_000_000, "label": "از سی میلیون تومان"},
    {"min_toman": 40_000_000, "label": "از چهل میلیون تومان"},
    {"min_toman": 50_000_000, "label": "از پنجاه میلیون تومان"},
    {"min_toman": 70_000_000, "label": "از هفتاد میلیون تومان"},
    {"min_toman": 100_000_000, "label": "از یکصد میلیون تومان"},
    {"min_toman": 150_000_000, "label": "از یکصد و پنجاه میلیون تومان"},
    {"min_toman": 200_000_000, "label": "از دویست میلیون تومان"},
    {"min_toman": 300_000_000, "label": "از سیصد میلیون تومان"},
    {"min_toman": 400_000_000, "label": "از چهارصد میلیون تومان"},
    {"min_toman": 500_000_000, "label": "از پانصد میلیون تومان"},
]

BENEFITS: dict[str, str] = {
    "usd_payment": "has_usd",
    "military_service_placement": "has_military_placement",
    "loan": "has_loan",
    "project_based": "has_project",
    "bonus": "has_bonus",
    "commission": "has_commission",
    "overtime": "has_overtime_offering",
    "night_or_evening_shift": "has_afternoon_shift",
    "promotion": "has_promotion",
    "part_time": "has_part_time",
    "disability_friendly": "has_disability_support",
    "flexible_hours": "has_flexible_hours",
    "supplementary_insurance": "has_supplementary_insurance",
    "esop": "has_esop",
    "business_trip": "has_business_trip",
}

BENEFIT_LABELS: dict[str, str] = {
    "usd_payment": "پرداخت دلاری",
    "military_service_placement": "امریه سربازی",
    "loan": "امکان دریافت وام",
    "project_based": "پروژه‌ای",
    "bonus": "دارای پاداش",
    "commission": "دارای پورسانت",
    "overtime": "امکان اضافه‌کاری",
    "night_or_evening_shift": "شیفت شب یا عصر",
    "promotion": "امکان ترفیع شغلی",
    "part_time": "پاره‌وقت",
    "disability_friendly": "امکان استخدام معلولین",
    "flexible_hours": "ساعت کاری شناور",
    "supplementary_insurance": "بیمه تکمیلی",
    "esop": "سهام تشویقی",
    "business_trip": "سفر کاری",
}

# Application statuses seen in the applied-jobs dashboard.
APPLICATION_STATUSES: dict[str, str] = {
    "pending": "در انتظار بررسی",
    "read": "دیده شده توسط کارفرما",
    "interview_accepted": "دعوت به مصاحبه",
    "hired": "استخدام شده",
    "dismissed": "رد شده",
    "applicant_canceled": "لغو شده توسط کارجو",
    "expired": "منقضی شده",
}

RESUME_EXTENSIONS = {".pdf", ".doc", ".docx"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
