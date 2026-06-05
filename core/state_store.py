import json
import re
import shutil
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"


def safe_name(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name or "未命名项目"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_projects_dir() -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def get_project_dir(project_name: str) -> Path:
    return PROJECTS_DIR / safe_name(project_name)


def get_state_file(project_name: str) -> Path:
    return get_project_dir(project_name) / "story_state.json"


def list_projects() -> list[str]:
    ensure_projects_dir()
    return sorted([p.name for p in PROJECTS_DIR.iterdir() if p.is_dir()])


def project_exists(project_name: str) -> bool:
    return get_state_file(project_name).exists()


def load_state(project_name: str) -> dict | None:
    state_file = get_state_file(project_name)
    if not state_file.exists():
        return None
    with open(state_file, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(project_name: str, state: dict) -> None:
    project_dir = get_project_dir(project_name)
    project_dir.mkdir(parents=True, exist_ok=True)
    state.setdefault("project", {})
    state["project"]["updated_at"] = now_text()
    with open(get_state_file(project_name), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def delete_project(project_name: str) -> bool:
    ensure_projects_dir()
    project_dir = get_project_dir(project_name)
    if not project_dir.exists():
        return False

    root = PROJECTS_DIR.resolve()
    target = project_dir.resolve()

    if target == root or root not in target.parents:
        raise ValueError("拒绝删除：目标不在 data/projects 目录内。")

    shutil.rmtree(target)
    return True


def get_project_preview(project_name: str) -> dict:
    state = load_state(project_name)

    if not state:
        return {
            "project_name": project_name,
            "display_name": project_name,
            "genre": "未知",
            "current_chapter": "无",
            "chapter_count": 0,
            "final_count": 0,
            "total_chars": 0,
            "updated_at": "未知",
        }

    project = state.get("project", {})
    bible = state.get("book_bible", {})
    chapters = state.get("chapters", [])

    current_chapter_id = project.get("current_chapter_id")
    current_chapter_title = "未选择章节"

    final_count = 0
    for chapter in chapters:
        if chapter.get("status") == "final":
            final_count += 1
        if chapter.get("chapter_id") == current_chapter_id:
            current_chapter_title = f"第 {chapter.get('chapter_number', '?')} 章｜{chapter.get('title', '未命名')}"

    state_file = get_state_file(project_name)
    updated_at = (
        datetime.fromtimestamp(state_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        if state_file.exists()
        else "未知"
    )

    return {
        "project_name": project_name,
        "display_name": project.get("name", project_name),
        "genre": bible.get("genre", "未设置"),
        "current_chapter": current_chapter_title,
        "chapter_count": len(chapters),
        "final_count": final_count,
        "total_chars": project.get("total_chars", 0),
        "updated_at": updated_at,
    }
