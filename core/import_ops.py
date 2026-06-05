import re
from copy import deepcopy

from core.chapter_ops import make_chapter_id, next_chapter_number, upsert_chapter_outline


BOOK_BIBLE_KEYS = [
    "genre",
    "core_selling_point",
    "protagonist_direction",
    "world_rules",
    "cheat_rules",
    "style_direction",
    "must_avoid",
]


def _clean_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "\n".join(str(x).strip() for x in value if str(x).strip())
    if isinstance(value, dict):
        return "\n".join(f"{k}：{v}" for k, v in value.items())
    return str(value).strip()


def _clean_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    if not text:
        return []
    parts = re.split(r"[\n；;]+", text)
    return [p.strip(" -—\t") for p in parts if p.strip(" -—\t")]


def _clean_int(value, default: int, min_value: int = 1, max_value: int | None = None) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    number = max(min_value, number)
    if max_value is not None:
        number = min(max_value, number)
    return number


def normalize_book_bible_import(data: dict) -> dict:
    """把 AI 返回的建书资料规整成稳定结构。"""
    data = data or {}
    bible_source = data.get("book_bible") if isinstance(data.get("book_bible"), dict) else data

    book_bible = {}
    for key in BOOK_BIBLE_KEYS:
        book_bible[key] = _clean_text(bible_source.get(key, ""))

    return {
        "book_bible": book_bible,
        "volume_outline": _clean_text(data.get("volume_outline", data.get("outline", ""))),
    }


def normalize_chapter_outline_import(data: dict, state: dict) -> dict:
    """把 AI 返回的单章细纲规整成 upsert_chapter_outline 可用的结构。"""
    data = data or {}
    default_number = next_chapter_number(state)
    chapter_number = _clean_int(data.get("chapter_number", 0), default=default_number, min_value=0)
    if chapter_number <= 0:
        chapter_number = default_number

    target_words = _clean_int(data.get("target_words", 3000), default=3000, min_value=500, max_value=20000)

    return {
        "chapter_id": _clean_text(data.get("chapter_id", "")) or make_chapter_id(chapter_number),
        "chapter_number": chapter_number,
        "title": _clean_text(data.get("title", "")) or f"第 {chapter_number} 章",
        "target_words": target_words,
        "outline": _clean_text(data.get("outline", "")),
        "must_include": _clean_list(data.get("must_include", [])),
        "must_avoid": _clean_list(data.get("must_avoid", [])),
        "appearing_characters": _clean_list(data.get("appearing_characters", [])),
        "items_involved": _clean_list(data.get("items_involved", [])),
        "ending_hook": _clean_text(data.get("ending_hook", "")),
    }


def normalize_state_extract_import(data: dict) -> dict:
    data = data or {}
    return {
        "characters": data.get("characters", {}) if isinstance(data.get("characters", {}), dict) else {},
        "items": data.get("items", {}) if isinstance(data.get("items", {}), dict) else {},
        "flags": data.get("flags", {}) if isinstance(data.get("flags", {}), dict) else {},
        "summary": _clean_text(data.get("summary", "")),
    }


def merge_book_import_into_state(state: dict, data: dict) -> dict:
    state = deepcopy(state)
    normalized = normalize_book_bible_import(data)
    state.setdefault("book_bible", {})
    for key, value in normalized["book_bible"].items():
        if value:
            state["book_bible"][key] = value
    if normalized.get("volume_outline"):
        state["volume_outline"] = normalized["volume_outline"]
    return state


def merge_chapter_import_into_state(state: dict, data: dict) -> dict:
    state = deepcopy(state)
    chapter = normalize_chapter_outline_import(data, state)
    return upsert_chapter_outline(
        state=state,
        chapter_id=chapter["chapter_id"],
        chapter_number=chapter["chapter_number"],
        title=chapter["title"],
        target_words=chapter["target_words"],
        outline=chapter["outline"],
        must_include=chapter["must_include"],
        must_avoid=chapter["must_avoid"],
        appearing_characters=chapter["appearing_characters"],
        items_involved=chapter["items_involved"],
        ending_hook=chapter["ending_hook"],
    )


def merge_state_extract_into_state(state: dict, data: dict) -> dict:
    state = deepcopy(state)
    normalized = normalize_state_extract_import(data)
    state.setdefault("characters", {}).update(normalized["characters"])
    state.setdefault("items", {}).update(normalized["items"])
    state.setdefault("flags", {}).update(normalized["flags"])
    if normalized["summary"]:
        state.setdefault("notes", {})["last_import_summary"] = normalized["summary"]
    return state


def split_uploaded_text_to_sections(raw_text: str) -> list[str]:
    """把一个长文本粗略切成章节段落。v0.6.3 批量导入时会用到。"""
    text = (raw_text or "").strip()
    if not text:
        return []

    pattern = r"(?m)(?=^\s*(第\s*[0-9一二三四五六七八九十百千]+\s*章|chapter\s+\d+)\b)"
    parts = [p.strip() for p in re.split(pattern, text, flags=re.IGNORECASE) if p and p.strip()]

    merged = []
    i = 0
    while i < len(parts):
        current = parts[i]
        if re.match(r"^\s*(第\s*[0-9一二三四五六七八九十百千]+\s*章|chapter\s+\d+)\b", current, flags=re.IGNORECASE):
            if i + 1 < len(parts):
                merged.append((current + "\n" + parts[i + 1]).strip())
                i += 2
            else:
                merged.append(current)
                i += 1
        else:
            merged.append(current)
            i += 1

    return merged or [text]
