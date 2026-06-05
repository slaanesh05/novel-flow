from core.state_store import now_text


def next_chapter_number(state: dict) -> int:
    chapters = state.get("chapters", [])
    max_number = 0
    for chapter in chapters:
        try:
            max_number = max(max_number, int(chapter.get("chapter_number", 0)))
        except ValueError:
            pass
    return max_number + 1


def make_chapter_id(chapter_number: int) -> str:
    return f"chapter_{int(chapter_number):03d}"


def get_chapter_by_id(state: dict, chapter_id: str) -> dict | None:
    for chapter in state.get("chapters", []):
        if chapter.get("chapter_id") == chapter_id:
            return chapter
    return None


def upsert_chapter_outline(
    state: dict,
    chapter_id: str | None,
    chapter_number: int,
    title: str,
    target_words: int,
    outline: str,
    must_include: list[str],
    must_avoid: list[str],
    appearing_characters: list[str],
    items_involved: list[str],
    ending_hook: str,
) -> dict:
    state.setdefault("chapters", [])
    state.setdefault("project", {})

    if not chapter_id:
        chapter_id = make_chapter_id(chapter_number)

    chapter = get_chapter_by_id(state, chapter_id)

    if chapter is None:
        chapter = {
            "chapter_id": chapter_id,
            "created_at": now_text(),
            "status": "outline",
            "draft_text": "",
            "diagnosis": [],
            "final_text_path": "",
        }
        state["chapters"].append(chapter)

    chapter.update(
        {
            "chapter_id": chapter_id,
            "chapter_number": int(chapter_number),
            "title": title.strip() or f"第 {int(chapter_number)} 章",
            "target_words": int(target_words),
            "outline": outline.strip(),
            "must_include": must_include,
            "must_avoid": must_avoid,
            "appearing_characters": appearing_characters,
            "items_involved": items_involved,
            "ending_hook": ending_hook.strip(),
            "updated_at": now_text(),
        }
    )

    state["project"]["current_chapter_id"] = chapter_id
    return state


def list_text_to_items(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def items_to_list_text(items: list[str]) -> str:
    return "\n".join(items or [])
