# app/utils/filters.py
from datetime import date, datetime
from typing import Optional

def _age_from_dob_str(dob: Optional[str]) -> Optional[int]:
    if not dob:
        return None
    try:
        y, m, d = map(int, dob.split("-"))
        born = date(y, m, d)
        today = date.today()
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        return max(0, age)
    except Exception:
        return None

def age_from_dob(dob: Optional[str]) -> Optional[int]:
    return _age_from_dob_str(dob)

def tanaka(age: Optional[int]) -> Optional[int]:
    if age is None:
        return None
    try:
        return int(round(208 - 0.7 * int(age)))
    except Exception:
        return None

def tanaka_dob(dob: Optional[str]) -> Optional[int]:
    a = _age_from_dob_str(dob)
    return tanaka(a)

def register_template_filters(app):
    app.jinja_env.filters["age_from_dob"] = age_from_dob
    app.jinja_env.filters["tanaka"] = tanaka
    app.jinja_env.filters["tanaka_dob"] = tanaka_dob
