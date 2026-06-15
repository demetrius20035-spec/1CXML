"""Транслитерация кириллических идентификаторов 1С в безопасные SQL-имена."""

from __future__ import annotations

import re

_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def translit(text: str) -> str:
    out = []
    for ch in text:
        lower = ch.lower()
        if lower in _MAP:
            mapped = _MAP[lower]
            out.append(mapped.upper() if ch.isupper() else mapped)
        else:
            out.append(ch)
    return "".join(out)


def sql_identifier(name: str, transliterate: bool = True, max_len: int = 64) -> str:
    """Преобразует имя метаданных в безопасный SQL-идентификатор."""
    s = translit(name) if transliterate else name
    # допускаем буквы (включая кириллицу, если не транслитерируем), цифры, _
    s = re.sub(r"[^\w]", "_", s, flags=re.UNICODE)
    if s and s[0].isdigit():
        s = "_" + s
    if not s:
        s = "col"
    return s[:max_len]


def table_name(full_name: str, transliterate: bool = True, max_len: int = 64) -> str:
    """Справочник.Номенклатура -> Spravochnik_Nomenklatura (или с кириллицей)."""
    parts = full_name.split(".") if full_name else ["unnamed"]
    joined = "_".join(parts)
    return sql_identifier(joined, transliterate, max_len)
