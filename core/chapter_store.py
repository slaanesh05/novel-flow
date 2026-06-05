from pathlib import Path
from core.state_store import get_project_dir


def get_chapters_dir(project_name: str) -> Path:
    chapters_dir = get_project_dir(project_name) / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    return chapters_dir


def chapter_filename(chapter_number: int) -> str:
    return f"chapter_{int(chapter_number):03d}.md"


def get_chapter_file(project_name: str, chapter_number: int) -> Path:
    return get_chapters_dir(project_name) / chapter_filename(chapter_number)


def save_chapter_text(project_name: str, chapter_number: int, title: str, text: str) -> str:
    file_path = get_chapter_file(project_name, chapter_number)
    content = f"# 第 {int(chapter_number)} 章 {title}\n\n{text.strip()}\n"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"chapters/{chapter_filename(chapter_number)}"


def load_chapter_text(project_name: str, chapter_number: int) -> str:
    file_path = get_chapter_file(project_name, chapter_number)
    if not file_path.exists():
        return ""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def build_full_manuscript(project_name: str, state: dict) -> str:
    chapters = sorted(state.get("chapters", []), key=lambda c: int(c.get("chapter_number", 0)))
    if not chapters:
        return "暂无章节。"

    parts = []
    for chapter in chapters:
        if chapter.get("status") != "final":
            continue
        number = int(chapter.get("chapter_number", 0))
        text = load_chapter_text(project_name, number)
        if text.strip():
            parts.append(text.strip())

    if not parts:
        return "暂无正式正文。"

    return "\n\n---\n\n".join(parts)


def count_final_chars(project_name: str, state: dict) -> int:
    text = build_full_manuscript(project_name, state)
    if text in ["暂无章节。", "暂无正式正文。"]:
        return 0
    return len(text)
