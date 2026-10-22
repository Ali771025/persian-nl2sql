from __future__ import annotations

import re
import unicodedata

from .models import Intent, QueryAnalysis

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ARABIC_TO_PERSIAN = str.maketrans(
    {"ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه", "أ": "ا", "إ": "ا", "ٱ": "ا", "ؤ": "و"}
)

_LATEST = ("آخرین", "اخرین", "جدیدترین", "نهایی", "آخر", "latest", "last")
_AGGREGATE = (
    "جمع",
    "تعداد",
    "میانگین",
    "حداکثر",
    "حداقل",
    "count",
    "sum",
    "avg",
    "max",
    "min",
)
_LIST = ("لیست", "فهرست", "نمایش بده", "همه", "تمام", "list", "show")
_COMPARE = (
    "مقایسه",
    "بیشتر از",
    "کمتر از",
    "بزرگتر",
    "کوچکتر",
    "compare",
    "greater",
    "less",
)
_DOMAIN_TERMS = (
    "ماده 100",
    "ماده صد",
    "وضعیت خلاف",
    "پرونده",
    "رأی",
    "رای",
    "جریمه",
    "خلاف",
    "کمیسیون",
    "کاربر",
)


def normalize_persian(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "")
    value = value.translate(_DIGITS).translate(_ARABIC_TO_PERSIAN)
    value = value.replace("\u200c", " ")
    value = re.sub(r"[\u064b-\u065f\u0670]", "", value)
    value = re.sub(r"\bاخرین\b", "آخرین", value)
    return re.sub(r"\s+", " ", value).strip()


def tokenize(text: str) -> list[str]:
    normalized = normalize_persian(text).lower()
    return re.findall(r"[\w]+", normalized, flags=re.UNICODE)


class QueryAnalyzer:
    def analyze(self, question: str, known_tables: list[str] | None = None) -> QueryAnalysis:
        normalized = normalize_persian(question)
        lowered = normalized.lower()
        if any(term in lowered for term in _LATEST):
            intent = Intent.LATEST
        elif any(term in lowered for term in _AGGREGATE):
            intent = Intent.AGGREGATE
        elif any(term in lowered for term in _COMPARE):
            intent = Intent.COMPARE
        elif any(term in lowered for term in _LIST):
            intent = Intent.LIST
        elif re.search(r"\d", lowered):
            intent = Intent.LOOKUP
        else:
            intent = Intent.UNKNOWN

        tables = []
        for table in known_tables or []:
            if table.lower() in lowered:
                tables.append(table)

        return QueryAnalysis(
            normalized_question=normalized,
            intent=intent,
            numbers=re.findall(r"(?<!\w)\d+(?:\.\d+)?(?!\w)", normalized),
            table_mentions=sorted(set(tables)),
            temporal_terms=[term for term in _LATEST if term in lowered],
            domain_terms=[term for term in _DOMAIN_TERMS if term in lowered],
        )
