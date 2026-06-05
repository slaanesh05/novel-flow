from core.state_store import now_text


def default_story_state(project_name: str) -> dict:
    return {
        "project": {
            "name": project_name,
            "version": "0.6",
            "created_at": now_text(),
            "updated_at": now_text(),
            "current_chapter_id": None,
            "total_chars": 0,
        },
        "book_bible": {
            "genre": "",
            "core_selling_point": "",
            "protagonist_direction": "",
            "world_rules": "",
            "cheat_rules": "",
            "style_direction": "",
            "must_avoid": "",
        },
        "volume_outline": "",
        "chapters": [],
        "characters": {},
        "items": {},
        "flags": {},
        "notes": {
            "workflow": "outline_driven",
            "main_principle": "人类给细纲，AI 生成正文，人工定稿。AI 选择项只作为辅助。",
        },
    }


def create_story_state_from_form(
    project_name: str,
    genre: str,
    core_selling_point: str,
    protagonist_direction: str,
    world_rules: str,
    cheat_rules: str,
    style_direction: str,
    must_avoid: str,
    volume_outline: str,
) -> dict:
    state = default_story_state(project_name)
    state["book_bible"] = {
        "genre": genre.strip(),
        "core_selling_point": core_selling_point.strip(),
        "protagonist_direction": protagonist_direction.strip(),
        "world_rules": world_rules.strip(),
        "cheat_rules": cheat_rules.strip(),
        "style_direction": style_direction.strip(),
        "must_avoid": must_avoid.strip(),
    }
    state["volume_outline"] = volume_outline.strip()
    return state



def create_story_state_from_import(project_name: str, import_data: dict) -> dict:
    """根据 AI / 程序整理后的导入结果创建项目状态。"""
    state = default_story_state(project_name)
    data = import_data or {}
    book_bible = data.get("book_bible", {}) if isinstance(data.get("book_bible", {}), dict) else {}

    for key in state["book_bible"].keys():
        state["book_bible"][key] = str(book_bible.get(key, "")).strip()

    state["volume_outline"] = str(data.get("volume_outline", "")).strip()
    return state
