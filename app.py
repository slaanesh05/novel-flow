import json
from pathlib import Path
from typing import Any

import streamlit as st

from core.chapter_ops import (
    get_chapter_by_id,
    items_to_list_text,
    list_text_to_items,
    make_chapter_id,
    next_chapter_number,
    upsert_chapter_outline,
)
from core.chapter_store import (
    build_full_manuscript,
    count_final_chars,
    load_chapter_text,
    save_chapter_text,
)
from core.import_ops import (
    merge_chapter_import_into_state,
    merge_state_extract_into_state,
    normalize_book_bible_import,
    normalize_chapter_outline_import,
    normalize_state_extract_import,
)
from core.models import create_story_state_from_form, create_story_state_from_import
from core.state_store import (
    delete_project,
    get_project_dir,
    get_project_preview,
    list_projects,
    load_state,
    now_text,
    project_exists,
    save_state,
)
from llm.client import call_llm
from llm.import_prompts import (
    build_book_import_prompt,
    build_chapter_outline_import_prompt,
    build_state_extract_prompt,
)
from llm.parsers import parse_json_array, parse_json_object
from llm.prompts import (
    build_ai_taste_diagnosis_prompt,
    build_author_rough_polish_prompt,
    build_chapter_diagnosis_prompt,
    build_chapter_draft_prompt,
    build_de_ai_rewrite_prompt,
    build_local_rewrite_prompt,
    build_next_outline_ideas_prompt,
    build_scene_draft_prompt,
    build_scene_plan_prompt,
    build_style_compaction_prompt,
    build_style_learning_prompt,
)


st.set_page_config(
    page_title="NovelFlow v0.6.8",
    page_icon="📚",
    layout="wide",
)

st.title("📚 NovelFlow v0.6.8：项目隔离修复 + 作者粗稿润色")
st.caption("主流程：导入资料 → AI 整理细纲 → 分场景草稿/作者粗稿润色 → 主稿编辑 → 自动保存。已修复多书切换污染问题。")


# -----------------------------
# 通用工具函数
# -----------------------------

def read_uploaded_text(uploaded_file) -> str:
    """读取 Streamlit 上传的 txt/md 文件。"""
    if uploaded_file is None:
        return ""

    raw = uploaded_file.getvalue()
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def json_text_to_dict(text: str, fallback: dict) -> dict:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        st.error("JSON 必须是对象。")
        return fallback
    except Exception as e:
        st.error("JSON 解析失败。")
        st.exception(e)
        return fallback


def safe_json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def require_api_key() -> bool:
    if not api_key.strip():
        st.error("请先在左侧填写 API Key。")
        return False
    return True


def show_raw_output_expander(title: str, raw_text: str, expanded: bool = False) -> None:
    if raw_text:
        with st.expander(title, expanded=expanded):
            st.text_area("原始输出", value=raw_text, height=280)


# -----------------------------
# 草稿安全保存工具
# -----------------------------

def get_draft_backup_file(project_name: str, chapter_id: str) -> Path:
    """每章一个独立草稿备份文件，避免长文只存在 Streamlit 控件里。"""
    draft_dir = get_project_dir(project_name) / "drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)
    return draft_dir / f"{chapter_id}_draft_autosave.md"


def read_draft_backup(project_name: str, chapter_id: str) -> str:
    path = get_draft_backup_file(project_name, chapter_id)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def write_draft_backup(project_name: str, chapter_id: str, text: str) -> None:
    """写入独立草稿文件。即使 story_state.json 后续出问题，草稿也还有一份。"""
    path = get_draft_backup_file(project_name, chapter_id)
    path.write_text(text or "", encoding="utf-8")


def get_author_polish_backup_file(project_name: str, chapter_id: str, kind: str) -> Path:
    """保存作者粗稿/AI润色稿的独立备份，避免中间稿丢失。"""
    draft_dir = get_project_dir(project_name) / "drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)
    safe_kind = "rough" if kind == "rough" else "polished"
    return draft_dir / f"{chapter_id}_author_{safe_kind}.md"


def read_author_polish_backup(project_name: str, chapter_id: str, kind: str) -> str:
    path = get_author_polish_backup_file(project_name, chapter_id, kind)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def write_author_polish_backup(project_name: str, chapter_id: str, kind: str, text: str) -> None:
    path = get_author_polish_backup_file(project_name, chapter_id, kind)
    path.write_text(text or "", encoding="utf-8")


def persist_author_polish_texts(
    project_name: str,
    chapter_id: str,
    rough_text: str | None = None,
    polished_text: str | None = None,
) -> bool:
    """把作者粗稿和 AI 润色稿同时保存到 story_state.json 与 drafts 备份文件。"""
    latest_state = load_state(project_name)
    if latest_state is None:
        return False

    target = get_chapter_by_id(latest_state, chapter_id)
    if target is None:
        return False

    saved_at = now_text()
    if rough_text is not None:
        target["author_rough_text"] = rough_text or ""
        target["author_rough_saved_at"] = saved_at
        write_author_polish_backup(project_name, chapter_id, "rough", rough_text or "")

    if polished_text is not None:
        target["polished_from_author_text"] = polished_text or ""
        target["author_polish_saved_at"] = saved_at
        write_author_polish_backup(project_name, chapter_id, "polished", polished_text or "")

    latest_state.setdefault("project", {})["current_chapter_id"] = chapter_id
    save_state(project_name, latest_state)
    return True


def persist_chapter_draft(project_name: str, chapter_id: str, text: str) -> bool:
    """
    强制把主稿保存到两处：
    1. story_state.json 的 chapter["draft_text"]
    2. data/projects/书名/drafts/chapter_xxx_draft_autosave.md

    这样可以避免“编辑器显示改了，但下次打开又回退”的灾难。
    """
    latest_state = load_state(project_name)
    if latest_state is None:
        return False

    target = get_chapter_by_id(latest_state, chapter_id)
    if target is None:
        return False

    target["draft_text"] = text or ""
    target["draft_saved_at"] = now_text()
    if target.get("status") == "outline":
        target["status"] = "draft"

    latest_state.setdefault("project", {})["current_chapter_id"] = chapter_id
    save_state(project_name, latest_state)
    write_draft_backup(project_name, chapter_id, text or "")

    return True


def autosave_draft_from_editor(project_name: str, chapter_id: str, editor_key: str) -> None:
    """Streamlit text_area 的 on_change 回调：离开输入框或点击按钮时自动落盘。"""
    text = str(st.session_state.get(editor_key, ""))
    ok = persist_chapter_draft(project_name, chapter_id, text)
    if ok:
        st.session_state[f"draft_last_saved_{project_name}_{chapter_id}"] = now_text()


# -----------------------------
# 项目切换与会话隔离工具
# -----------------------------

def persist_project_open_buffers(project_name: str | None) -> None:
    """切换项目之前，尽量把当前项目已经打开的编辑器内容写入对应项目。

    Streamlit 的 st.session_state 是全局的，不会因为切换书籍自动清空。
    因此切换书籍前必须先保存当前项目的主稿、作者粗稿和润色稿。
    """
    if not project_name:
        return

    draft_prefix = f"draft_editor_{project_name}_"
    rough_prefix = f"author_rough_text_{project_name}_"
    polished_prefix = f"author_polished_text_{project_name}_"

    for key, value in list(st.session_state.items()):
        try:
            if key.startswith(draft_prefix):
                chapter_id = key[len(draft_prefix):]
                if chapter_id:
                    persist_chapter_draft(project_name, chapter_id, str(value))

            elif key.startswith(rough_prefix):
                chapter_id = key[len(rough_prefix):]
                if chapter_id:
                    persist_author_polish_texts(project_name, chapter_id, rough_text=str(value))

            elif key.startswith(polished_prefix):
                chapter_id = key[len(polished_prefix):]
                if chapter_id:
                    persist_author_polish_texts(project_name, chapter_id, polished_text=str(value))
        except Exception as e:
            st.session_state["project_switch_save_warning"] = f"切换项目前保存 {key} 时失败：{e}"


def clear_project_ui_state(project_name: str | None) -> None:
    """清理某本书留在 st.session_state 里的页面控件缓存。

    这一步只清 UI 缓存，不删除 data/projects 里的真实项目数据。
    目的是避免旧书的章节、AI 整理结果、场景草稿、主稿编辑器内容污染新书流程。
    """
    if not project_name:
        return

    protected_keys = {
        "active_project_name",
        "pending_project_switch",
        "book_shelf_selectbox",
        "project_switch_save_warning",
    }

    for key in list(st.session_state.keys()):
        if key in protected_keys:
            continue

        # 绝大多数项目相关 key 都采用 xxx_{project_name} 或 xxx_{project_name}_{chapter_id}。
        # 只清这些明确带项目名边界的 key，降低误删全局设置的风险。
        if key.endswith(f"_{project_name}") or f"_{project_name}_" in key:
            del st.session_state[key]


def request_project_switch(new_project: str) -> None:
    """安全切换当前置入流程的小说。"""
    old_project = st.session_state.get("active_project_name")

    if old_project and old_project != new_project:
        persist_project_open_buffers(old_project)
        clear_project_ui_state(old_project)

    st.session_state["pending_project_switch"] = new_project
    st.rerun()




STYLE_PROFILE_KEYS = [
    "sentence_rhythm",
    "word_choice",
    "dialogue",
    "description",
    "pacing",
    "taboos",
    "rewrite_rules",
]

PROFILE_LABELS = {
    "sentence_rhythm": "句式节奏",
    "word_choice": "遣词用字",
    "dialogue": "对白规则",
    "description": "动作 / 环境 / 物件",
    "pacing": "叙事节奏",
    "taboos": "禁忌写法",
    "rewrite_rules": "改稿规则",
}

# v0.6.6：生成正文时只带入精简文风档案，避免 prompt 越来越长。
STYLE_PROMPT_ITEM_LIMIT = 5
STYLE_MEMORY_STORE_LIMIT = 12
STYLE_SAMPLE_STORE_LIMIT = 30


def default_style_memory() -> dict:
    return {
        "profile": {
            "summary": "",
            "sentence_rhythm": [],
            "word_choice": [],
            "dialogue": [],
            "description": [],
            "pacing": [],
            "taboos": [],
            "rewrite_rules": [],
        },
        "samples": [],
        "updated_at": "",
    }


def _clean_text_item(value: Any) -> str:
    return str(value or "").strip()


def _dedupe_keep_latest(items: list, limit: int = STYLE_MEMORY_STORE_LIMIT) -> list:
    """去重并保留最近规则。作者会进步，所以越新的规则权重越高。"""
    result = []
    seen = set()
    for item in items or []:
        text = _clean_text_item(item)
        if not text:
            continue
        key = text.replace(" ", "")
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result[-limit:]


def normalize_style_profile(profile: dict | None) -> dict:
    """把各种旧格式 / AI 返回格式整理成稳定的文风档案结构。"""
    profile = profile if isinstance(profile, dict) else {}
    normalized = {"summary": _clean_text_item(profile.get("summary", ""))[-700:]}

    for key in STYLE_PROFILE_KEYS:
        value = profile.get(key, [])
        if isinstance(value, str):
            value = list_text_to_items(value)
        elif not isinstance(value, list):
            value = []
        normalized[key] = _dedupe_keep_latest(value)

    return normalized


def defaulted_style_memory(memory: dict | None = None) -> dict:
    base = default_style_memory()
    if isinstance(memory, dict):
        base.update(memory)

    base["profile"] = normalize_style_profile(base.get("profile", {}))

    samples = base.get("samples", [])
    if not isinstance(samples, list):
        samples = []
    base["samples"] = samples[-STYLE_SAMPLE_STORE_LIMIT:]

    base["updated_at"] = _clean_text_item(base.get("updated_at", ""))
    return base


def get_style_memory(state: dict) -> dict:
    memory = defaulted_style_memory(state.get("style_memory", {}))
    state["style_memory"] = memory
    return memory


def _merge_unique_text_list(old_items: list, new_items: list, limit: int = STYLE_MEMORY_STORE_LIMIT) -> list:
    return _dedupe_keep_latest([*(old_items or []), *(new_items or [])], limit=limit)


def merge_style_profile_into_state(
    state: dict,
    learned_profile: dict,
    chapter_id: str = "",
    ai_excerpt: str = "",
    human_excerpt: str = "",
) -> dict:
    memory = get_style_memory(state)
    profile = memory["profile"]
    learned_profile = normalize_style_profile(learned_profile)

    new_summary = _clean_text_item(learned_profile.get("summary", ""))
    if new_summary:
        old_summary = _clean_text_item(profile.get("summary", ""))
        profile["summary"] = new_summary if not old_summary else f"{old_summary}；{new_summary}"[-700:]

    for key in STYLE_PROFILE_KEYS:
        profile[key] = _merge_unique_text_list(profile.get(key, []), learned_profile.get(key, []))

    if ai_excerpt.strip() or human_excerpt.strip():
        memory["samples"].append({
            "created_at": now_text(),
            "chapter_id": chapter_id,
            "ai_excerpt": ai_excerpt.strip()[:700],
            "human_excerpt": human_excerpt.strip()[:700],
        })
        memory["samples"] = memory["samples"][-STYLE_SAMPLE_STORE_LIMIT:]

    memory["updated_at"] = now_text()
    state["style_memory"] = memory
    return memory


def style_profile_to_prompt_text(state: dict) -> str:
    """
    返回真正会放进正文生成 prompt 的短文风档案。
    注意：这里故意只取每类最近几条，避免文风记忆无限膨胀。
    """
    memory = get_style_memory(state)
    profile = memory.get("profile", {})

    lines = []
    summary = _clean_text_item(profile.get("summary", ""))
    if summary:
        lines.append(f"文风概括：{summary}")

    for key, label in PROFILE_LABELS.items():
        items = [_clean_text_item(i) for i in profile.get(key, []) if _clean_text_item(i)]
        items = items[-STYLE_PROMPT_ITEM_LIMIT:]
        if items:
            lines.append(f"{label}：" + "；".join(items))

    return "\n".join(lines).strip()


def build_style_profile_from_editor(summary_text: str, field_texts: dict) -> dict:
    profile = {"summary": _clean_text_item(summary_text)}
    for key in STYLE_PROFILE_KEYS:
        profile[key] = _dedupe_keep_latest(list_text_to_items(field_texts.get(key, "")))
    return normalize_style_profile(profile)


# -----------------------------
# 侧边栏
# -----------------------------

# -----------------------------
with st.sidebar:
    st.header("📚 书库")

    projects = list_projects()

    # 先处理由“置入流程”按钮或侧边栏选择触发的待切换项目。
    # 注意：必须在 selectbox 创建之前同步 widget key，否则 Streamlit 会保留旧选择。
    pending_project = st.session_state.pop("pending_project_switch", None)
    if pending_project in projects:
        st.session_state["active_project_name"] = pending_project

    if "active_project_name" not in st.session_state:
        st.session_state["active_project_name"] = projects[0] if projects else None

    if st.session_state["active_project_name"] not in projects:
        st.session_state["active_project_name"] = projects[0] if projects else None

    current_project = st.session_state["active_project_name"]

    # 同步侧边栏 selectbox 的显示值，避免“业务状态已经切换，但控件仍停在旧书”。
    if projects and st.session_state.get("book_shelf_selectbox") != current_project:
        st.session_state["book_shelf_selectbox"] = current_project

    if projects:
        current_index = projects.index(current_project) if current_project in projects else 0

        selected_project = st.selectbox(
            "当前置入流程的小说",
            projects,
            index=current_index,
            key="book_shelf_selectbox",
        )

        if selected_project != current_project:
            request_project_switch(selected_project)

        preview = get_project_preview(current_project)
        st.caption(f"当前书名：{preview['display_name']}")
        st.caption(f"进度：{preview['current_chapter']}")
        st.caption(f"正式章节：{preview['final_count']} / {preview['chapter_count']}")
        st.caption(f"最后保存：{preview['updated_at']}")
    else:
        current_project = None
        st.info("书库为空。请先在「建书」里创建项目。")

    st.divider()

    st.header("🤖 AI 设置")

    base_url = st.text_input(
        "Base URL",
        value="https://api.deepseek.com",
    )

    api_key = st.text_input(
        "API Key",
        value="",
        type="password",
    )

    planner_model = st.text_input(
        "大纲/诊断模型",
        value="deepseek-v4-flash",
    )

    writer_model = st.text_input(
        "正文生成模型",
        value="deepseek-v4-flash",
    )

    st.caption("建议：大纲整理和诊断用快模型，正文生成用质量更好的模型。")


if current_project:
    state = load_state(current_project)
else:
    state = None

if st.session_state.get("project_switch_save_warning"):
    st.warning(st.session_state.pop("project_switch_save_warning"))


tab_shelf, tab_create, tab_outline, tab_write, tab_state, tab_raw = st.tabs(
    [
        "① 书架",
        "② 建书",
        "③ 大纲工作台",
        "④ 章节写作",
        "⑤ 状态库",
        "⑥ 原始 JSON",
    ]
)


# -----------------------------
# ① 书架
# -----------------------------
with tab_shelf:
    st.subheader("① 书架")

    projects = list_projects()

    if not projects:
        st.warning("当前没有本地项目。请进入「② 建书」创建一本新书。")
    else:
        for project_name in projects:
            preview = get_project_preview(project_name)
            is_active = project_name == current_project

            with st.expander(
                f"{'📖 当前使用中｜' if is_active else '📚 '}{preview['display_name']}｜{preview['genre']}｜正式章节 {preview['final_count']}",
                expanded=is_active,
            ):
                col1, col2, col3 = st.columns(3)

                with col1:
                    st.write("项目文件夹：", preview["project_name"])
                    st.write("当前章节：", preview["current_chapter"])
                    st.write("最后保存：", preview["updated_at"])

                with col2:
                    st.write("章节细纲数：", preview["chapter_count"])
                    st.write("正式章节数：", preview["final_count"])
                    st.write("正文字数：", preview["total_chars"])

                with col3:
                    if not is_active:
                        if st.button("置入流程", key=f"activate_{project_name}"):
                            request_project_switch(project_name)
                    else:
                        st.success("这本书正在当前流程中。")

                book_state = load_state(project_name)

                st.divider()
                st.markdown("### 正文阅读器")

                manuscript_text = build_full_manuscript(project_name, book_state) if book_state else "项目状态缺失。"

                st.text_area(
                    "正式正文",
                    value=manuscript_text,
                    height=420,
                    key=f"reader_{project_name}",
                )

                st.download_button(
                    "下载全文 Markdown",
                    data=manuscript_text,
                    file_name=f"{preview['display_name']}_全文.md",
                    mime="text/markdown",
                    key=f"download_{project_name}",
                )

                st.divider()

                with st.expander("危险操作：彻底删除这本书", expanded=False):
                    st.warning("删除后会移除整个项目文件夹，不能撤销。")

                    confirm_name = st.text_input(
                        "请输入项目文件夹名以确认删除",
                        value="",
                        key=f"delete_name_{project_name}",
                    )

                    confirm_check = st.checkbox(
                        "我确认要彻底删除这本书",
                        key=f"delete_check_{project_name}",
                    )

                    can_delete = confirm_name.strip() == project_name and confirm_check

                    if st.button(
                        "确认彻底删除",
                        key=f"delete_btn_{project_name}",
                        disabled=not can_delete,
                    ):
                        try:
                            deleted = delete_project(project_name)

                            if deleted:
                                remaining = list_projects()
                                if st.session_state.get("active_project_name") == project_name:
                                    clear_project_ui_state(project_name)
                                    st.session_state["pending_project_switch"] = remaining[0] if remaining else None
                                st.success(f"已删除项目：{project_name}")
                                st.rerun()
                            else:
                                st.warning("项目不存在，可能已经被删除。")
                        except Exception as e:
                            st.error("删除失败。")
                            st.exception(e)


# -----------------------------
# ② 建书
# -----------------------------
with tab_create:
    st.subheader("② 建书")
    st.info("推荐使用“智能导入建书”：你只需要粘贴或上传设定文档，AI 会自动整理成内部结构。")

    st.success("当前页面是极简模式：只需要填写书名，然后上传或粘贴一整段资料。那些细分字段会由 AI 自动整理到后台。")
    create_mode = "智能导入建书（推荐）"

    if create_mode == "智能导入建书（推荐）":
        project_name = st.text_input("书名 / 项目名", value="", placeholder="例如：青炉问道", key="import_project_name")

        uploaded = st.file_uploader(
            "上传设定文档，可选。支持 .txt / .md",
            type=["txt", "md"],
            key="book_import_file",
        )
        uploaded_text = read_uploaded_text(uploaded)

        raw_book_text = st.text_area(
            "粘贴设定资料",
            value="",
            height=260,
            placeholder="可以直接粘贴：题材、主角、世界观、金手指、禁忌、卷纲、已有想法……不需要按表格填写。",
            key="book_import_text",
        )

        combined_book_text = "\n\n".join([x for x in [uploaded_text, raw_book_text] if x.strip()]).strip()

        col_ai, col_local = st.columns(2)

        with col_ai:
            if st.button("AI 整理设定", key="btn_ai_import_book"):
                if not combined_book_text:
                    st.error("请先上传或粘贴设定资料。")
                elif require_api_key():
                    prompt = build_book_import_prompt(combined_book_text)
                    with st.spinner("正在整理设定资料..."):
                        raw = call_llm(
                            base_url=base_url,
                            api_key=api_key,
                            model=planner_model,
                            prompt=prompt,
                            system_prompt="你是中文网文设定整理助手。你必须只输出严格 JSON 对象，不要输出解释、标题、Markdown。",
                            temperature=0.2,
                            max_tokens=4000,
                        )
                    st.session_state["book_import_raw"] = raw
                    try:
                        data = parse_json_object(raw)
                        result = normalize_book_bible_import(data)
                        st.session_state["book_import_result"] = result
                        st.success("设定整理完成。请检查下方结果，然后确认创建项目。")
                    except Exception as e:
                        st.session_state["book_import_result"] = None
                        st.warning("AI 返回内容不是严格 JSON，已保留原始输出。你可以复制原文手动整理，或重新点击整理。")
                        st.exception(e)

        with col_local:
            if st.button("本地简单整理（不调用 AI）", key="btn_local_import_book"):
                if not combined_book_text:
                    st.error("请先上传或粘贴设定资料。")
                else:
                    result = normalize_book_bible_import(
                        {
                            "book_bible": {
                                "core_selling_point": combined_book_text[:1200],
                                "style_direction": "传统中文网文，叙事清楚，减少 AI 腔。",
                            },
                            "volume_outline": combined_book_text,
                        }
                    )
                    st.session_state["book_import_result"] = result
                    st.session_state["book_import_raw"] = ""
                    st.success("已生成本地简版整理。质量不如 AI，但可以先建项目。")

        import_result = st.session_state.get("book_import_result")
        raw_output = st.session_state.get("book_import_raw", "")

        show_raw_output_expander("查看 AI 原始设定整理输出", raw_output, expanded=not bool(import_result))

        if import_result:
            st.markdown("### 整理结果预览")
            bible_preview = import_result.get("book_bible", import_result)
            st.markdown(f"""
**题材**：{bible_preview.get('genre', '')}  
**核心卖点**：{bible_preview.get('core_selling_point', '')}  
**主角方向**：{bible_preview.get('protagonist_direction', '')}  
**世界规则**：{bible_preview.get('world_rules', '')}  
**金手指规则**：{bible_preview.get('cheat_rules', '')}  
**文风方向**：{bible_preview.get('style_direction', '')}  
**全书禁忌**：{bible_preview.get('must_avoid', '')}
""")
            if import_result.get("volume_outline"):
                st.markdown("**卷纲**")
                st.info(import_result.get("volume_outline", ""))

            with st.expander("高级：查看 / 修改后台 JSON，不懂可以不打开", expanded=False):
                edited_import_json = st.text_area(
                    "后台结构化数据 JSON",
                    value=safe_json_dumps(import_result),
                    height=360,
                    key="book_import_edit_json",
                )

            if st.button("确认创建项目", key="btn_create_from_import"):
                if not project_name.strip():
                    st.error("书名不能为空。")
                elif project_exists(project_name):
                    st.error("这个项目已经存在。请换一个书名，或先到书架删除旧项目。")
                else:
                    edited_data = json_text_to_dict(
                        st.session_state.get("book_import_edit_json", safe_json_dumps(import_result)),
                        import_result,
                    )
                    edited_data = normalize_book_bible_import(edited_data)
                    new_state = create_story_state_from_import(project_name, edited_data)
                    save_state(project_name, new_state)
                    st.success(f"已创建新书：{project_name}")
                    request_project_switch(project_name)

    else:
        # 保留旧表单代码作为开发回退路径；极简模式下不会显示。
        with st.form("create_book_form"):
            project_name = st.text_input("书名 / 项目名", value="青炉问道")
            genre = st.text_input("题材", value="凡人流修仙 + 门派成长 + 丹道探索")
            core_selling_point = st.text_area(
                "核心卖点",
                value="底层杂役在谨慎求生中逐步接触丹道隐秘，靠观察、忍耐和小心试错向上攀爬。",
                height=90,
            )
            protagonist_direction = st.text_area(
                "主角方向",
                value="谨慎、能忍、观察力强，前期弱小，不开局无敌，不轻易暴露底牌。",
                height=90,
            )
            world_rules = st.text_area(
                "世界观限制",
                value="修仙体系残酷，宗门等级森严，底层弟子获得资源很难。",
                height=90,
            )
            cheat_rules = st.text_area(
                "金手指限制",
                value="残破丹炉只能辅助分析药性，不能直接战斗秒杀，也不能开局完整揭秘。",
                height=90,
            )
            style_direction = st.text_area(
                "文风方向",
                value="传统中文网文，叙事清楚，克制，不文青，不 AI 腔，重视具体动作和环境细节。",
                height=90,
            )
            must_avoid = st.text_area(
                "全书禁忌",
                value="不要热血打脸；不要主角突然无敌；不要一次性解释世界观；不要提前揭秘金手指；不要写成说明书。",
                height=100,
            )
            volume_outline = st.text_area(
                "第一卷粗纲",
                value="第一卷：外门求生。主角从药田杂役起步，发现药田异常和旧案有关，在执事压迫与资源匮乏中寻找修行机会。",
                height=120,
            )

            submitted = st.form_submit_button("创建新书")

        if submitted:
            if project_exists(project_name):
                st.error("这个项目已经存在。请换一个书名，或先到书架删除旧项目。")
            else:
                new_state = create_story_state_from_form(
                    project_name=project_name,
                    genre=genre,
                    core_selling_point=core_selling_point,
                    protagonist_direction=protagonist_direction,
                    world_rules=world_rules,
                    cheat_rules=cheat_rules,
                    style_direction=style_direction,
                    must_avoid=must_avoid,
                    volume_outline=volume_outline,
                )
                save_state(project_name, new_state)
                st.success(f"已创建新书：{project_name}")
                request_project_switch(project_name)


# -----------------------------
# ③ 大纲工作台
# -----------------------------
with tab_outline:
    st.subheader("③ 大纲工作台")

    if state is None:
        st.warning("请先在「② 建书」创建或在「① 书架」选择一本书。")
    else:
        st.markdown("### 小说圣经")
        st.caption("默认只展示摘要。需要手改时再展开高级编辑。")

        bible = state.get("book_bible", {})
        col_a, col_b = st.columns(2)
        with col_a:
            st.write("题材：", bible.get("genre", ""))
            st.write("核心卖点：", bible.get("core_selling_point", ""))
            st.write("主角方向：", bible.get("protagonist_direction", ""))
        with col_b:
            st.write("世界规则：", bible.get("world_rules", ""))
            st.write("金手指规则：", bible.get("cheat_rules", ""))
            st.write("全书禁忌：", bible.get("must_avoid", ""))

        with st.expander("高级：编辑小说圣经和卷纲", expanded=False):
            bible_text = st.text_area(
                "book_bible JSON",
                value=safe_json_dumps(state.get("book_bible", {})),
                height=280,
                key=f"bible_editor_{current_project}",
            )

            volume_outline_edit = st.text_area(
                "第一卷 / 当前卷粗纲",
                value=state.get("volume_outline", ""),
                height=160,
                key=f"volume_outline_{current_project}",
            )

            if st.button("保存小说圣经和卷纲"):
                state["book_bible"] = json_text_to_dict(bible_text, state.get("book_bible", {}))
                state["volume_outline"] = volume_outline_edit
                save_state(current_project, state)
                st.success("已保存。")
                st.rerun()

        st.divider()

        st.markdown("### 章节细纲列表")

        chapters = sorted(
            state.get("chapters", []),
            key=lambda c: int(c.get("chapter_number", 0)),
        )

        if chapters:
            for chapter in chapters:
                status = chapter.get("status", "outline")
                st.write(
                    f"{chapter.get('chapter_id')}｜第 {chapter.get('chapter_number')} 章｜"
                    f"{chapter.get('title', '未命名')}｜状态：{status}"
                )
        else:
            st.info("还没有章节细纲。")

        st.divider()

        st.markdown("### 智能导入单章细纲（推荐）")
        st.caption("把你脑子里的章节想法直接粘贴进来。AI 会自动拆成标题、细纲、必须包含、必须避免、出场人物、道具和钩子。")

        uploaded_chapter = st.file_uploader(
            "上传单章资料，可选。支持 .txt / .md",
            type=["txt", "md"],
            key=f"chapter_import_file_{current_project}",
        )
        uploaded_chapter_text = read_uploaded_text(uploaded_chapter)

        raw_chapter_text = st.text_area(
            "粘贴单章资料",
            value="",
            height=220,
            placeholder="例：第十二章，主角回到边城，发现赵家逼迫妹妹退婚……结尾让城主府的人登场。不要暴露真实战力。",
            key=f"chapter_import_text_{current_project}",
        )

        combined_chapter_text = "\n\n".join([x for x in [uploaded_chapter_text, raw_chapter_text] if x.strip()]).strip()

        col_import_ai, col_import_local = st.columns(2)

        with col_import_ai:
            if st.button("AI 整理为章节细纲", key=f"btn_ai_import_chapter_{current_project}"):
                if not combined_chapter_text:
                    st.error("请先上传或粘贴章节资料。")
                elif require_api_key():
                    prompt = build_chapter_outline_import_prompt(combined_chapter_text, state)
                    with st.spinner("正在整理章节细纲..."):
                        raw = call_llm(
                            base_url=base_url,
                            api_key=api_key,
                            model=planner_model,
                            prompt=prompt,
                            system_prompt="你是中文网文章节细纲整理助手。你必须只输出严格 JSON 对象，不要输出解释、标题、Markdown。",
                            temperature=0.25,
                            max_tokens=3500,
                        )
                    st.session_state[f"chapter_import_raw_{current_project}"] = raw
                    try:
                        data = parse_json_object(raw)
                        result = normalize_chapter_outline_import(data, state)
                        st.session_state[f"chapter_import_result_{current_project}"] = result
                        st.success("章节细纲整理完成。请检查下方结果，然后确认入库。")
                    except Exception as e:
                        st.session_state[f"chapter_import_result_{current_project}"] = None
                        st.warning("AI 返回内容不是严格 JSON，已保留原始输出。你可以重新整理，或复制原文到手动编辑区。")
                        st.exception(e)

        with col_import_local:
            if st.button("本地简单整理（不调用 AI）", key=f"btn_local_import_chapter_{current_project}"):
                if not combined_chapter_text:
                    st.error("请先上传或粘贴章节资料。")
                else:
                    number = next_chapter_number(state)
                    result = normalize_chapter_outline_import(
                        {
                            "chapter_number": number,
                            "title": f"第 {number} 章",
                            "target_words": 3000,
                            "outline": combined_chapter_text,
                            "must_include": [],
                            "must_avoid": [],
                            "appearing_characters": [],
                            "items_involved": [],
                            "ending_hook": "",
                        },
                        state,
                    )
                    st.session_state[f"chapter_import_result_{current_project}"] = result
                    st.session_state[f"chapter_import_raw_{current_project}"] = ""
                    st.success("已生成本地简版细纲。")

        chapter_import_result = st.session_state.get(f"chapter_import_result_{current_project}")
        chapter_import_raw = st.session_state.get(f"chapter_import_raw_{current_project}", "")

        show_raw_output_expander("查看 AI 原始章节整理输出", chapter_import_raw, expanded=not bool(chapter_import_result))

        if chapter_import_result:
            st.markdown("### 章节整理结果预览")
            st.markdown(f"""
**第 {chapter_import_result.get('chapter_number', '')} 章：{chapter_import_result.get('title', '')}**  
**目标字数**：{chapter_import_result.get('target_words', 3000)}  
**章末钩子**：{chapter_import_result.get('ending_hook', '')}
""")
            st.markdown("**章节细纲**")
            st.info(chapter_import_result.get("outline", ""))

            with st.expander("高级：查看 AI 拆出的后台字段，不懂可以不打开", expanded=False):
                st.markdown("**必须包含**")
                st.write(chapter_import_result.get("must_include", []))
                st.markdown("**必须避免**")
                st.write(chapter_import_result.get("must_avoid", []))
                st.markdown("**出场人物**")
                st.write(chapter_import_result.get("appearing_characters", []))
                st.markdown("**涉及道具**")
                st.write(chapter_import_result.get("items_involved", []))
                edited_chapter_json = st.text_area(
                    "后台结构化数据 JSON",
                    value=safe_json_dumps(chapter_import_result),
                    height=320,
                    key=f"chapter_import_edit_json_{current_project}",
                )

            if st.button("确认保存这章细纲", key=f"btn_save_import_chapter_{current_project}"):
                edited_data = json_text_to_dict(
                    st.session_state.get(f"chapter_import_edit_json_{current_project}", safe_json_dumps(chapter_import_result)),
                    chapter_import_result,
                )
                state = merge_chapter_import_into_state(state, edited_data)
                save_state(current_project, state)
                st.success("章节细纲已入库。")
                st.rerun()

        st.divider()

        with st.expander("不推荐：旧版手动字段编辑", expanded=False):
            chapter_options = ["新建章节"] + [
                f"{c.get('chapter_id')}｜第 {c.get('chapter_number')} 章｜{c.get('title', '未命名')}"
                for c in chapters
            ]

            selected_outline_option = st.selectbox(
                "选择要编辑的章节",
                chapter_options,
                key=f"outline_select_{current_project}",
            )

            editing_chapter = None

            if selected_outline_option != "新建章节":
                chapter_id = selected_outline_option.split("｜")[0]
                editing_chapter = get_chapter_by_id(state, chapter_id)

            default_number = (
                int(editing_chapter.get("chapter_number"))
                if editing_chapter
                else next_chapter_number(state)
            )

            chapter_number = st.number_input(
                "章节序号",
                min_value=1,
                value=default_number,
                step=1,
                key=f"chapter_number_{current_project}",
            )

            chapter_title = st.text_input(
                "章节标题",
                value=editing_chapter.get("title", "") if editing_chapter else "",
                key=f"chapter_title_{current_project}",
            )

            target_words = st.number_input(
                "目标字数",
                min_value=500,
                value=int(editing_chapter.get("target_words", 3000)) if editing_chapter else 3000,
                step=100,
                key=f"target_words_{current_project}",
            )

            outline = st.text_area(
                "章节细纲",
                value=editing_chapter.get("outline", "") if editing_chapter else "",
                height=220,
                key=f"chapter_outline_{current_project}",
                placeholder="写清楚本章目标、主要剧情点、情绪变化、章末钩子。",
            )

            must_include_text = st.text_area(
                "本章必须包含，一行一个",
                value=items_to_list_text(editing_chapter.get("must_include", [])) if editing_chapter else "",
                height=100,
                key=f"must_include_{current_project}",
            )

            must_avoid_text = st.text_area(
                "本章必须避免，一行一个",
                value=items_to_list_text(editing_chapter.get("must_avoid", [])) if editing_chapter else "",
                height=100,
                key=f"must_avoid_{current_project}",
            )

            appearing_characters_text = st.text_area(
                "出场人物，一行一个",
                value=items_to_list_text(editing_chapter.get("appearing_characters", [])) if editing_chapter else "",
                height=80,
                key=f"appearing_characters_{current_project}",
            )

            items_involved_text = st.text_area(
                "涉及道具，一行一个",
                value=items_to_list_text(editing_chapter.get("items_involved", [])) if editing_chapter else "",
                height=80,
                key=f"items_involved_{current_project}",
            )

            ending_hook = st.text_area(
                "章末钩子",
                value=editing_chapter.get("ending_hook", "") if editing_chapter else "",
                height=80,
                key=f"ending_hook_{current_project}",
            )

            col_save, col_ideas = st.columns(2)

            with col_save:
                if st.button("保存章节细纲"):
                    chapter_id = editing_chapter.get("chapter_id") if editing_chapter else make_chapter_id(chapter_number)

                    state = upsert_chapter_outline(
                        state=state,
                        chapter_id=chapter_id,
                        chapter_number=chapter_number,
                        title=chapter_title,
                        target_words=target_words,
                        outline=outline,
                        must_include=list_text_to_items(must_include_text),
                        must_avoid=list_text_to_items(must_avoid_text),
                        appearing_characters=list_text_to_items(appearing_characters_text),
                        items_involved=list_text_to_items(items_involved_text),
                        ending_hook=ending_hook,
                    )
                    save_state(current_project, state)
                    st.success("章节细纲已保存。")
                    st.rerun()

            with col_ideas:
                if st.button("AI 辅助生成下一章方案"):
                    if require_api_key():
                        user_note = outline or "请基于当前卷纲和已有章节，给出下一章推进方案。"
                        prompt = build_next_outline_ideas_prompt(state, user_note)

                        with st.spinner("正在生成细纲方案..."):
                            raw = call_llm(
                                base_url=base_url,
                                api_key=api_key,
                                model=planner_model,
                                prompt=prompt,
                                system_prompt="你是中文网文大纲助手。你必须只输出严格 JSON 数组，不要输出解释、标题、Markdown。",
                                temperature=0.35,
                                max_tokens=3000,
                            )
                        st.session_state[f"outline_ideas_raw_{current_project}"] = raw
                        try:
                            ideas = parse_json_array(raw)
                            st.session_state[f"outline_ideas_{current_project}"] = ideas
                            st.success("已生成方案。")
                        except Exception as e:
                            st.session_state[f"outline_ideas_{current_project}"] = []
                            st.warning("AI 返回内容不是严格 JSON，已保存原始输出。你可以查看原始文本人工参考。")
                            st.exception(e)

            ideas = st.session_state.get(f"outline_ideas_{current_project}", [])
            raw_ideas = st.session_state.get(f"outline_ideas_raw_{current_project}", "")

            if ideas:
                st.markdown("### AI 细纲方案参考")
                st.json(ideas)

            show_raw_output_expander("查看 AI 原始细纲方案输出", raw_ideas, expanded=not bool(ideas))


# -----------------------------
# ④ 章节写作
# -----------------------------
with tab_write:
    st.subheader("④ 章节写作")

    if state is None:
        st.warning("请先选择一本书。")
    else:
        chapters = sorted(
            state.get("chapters", []),
            key=lambda c: int(c.get("chapter_number", 0)),
        )

        if not chapters:
            st.warning("还没有章节细纲。请先去「③ 大纲工作台」导入或创建章节细纲。")
        else:
            chapter_labels = [
                f"{c.get('chapter_id')}｜第 {c.get('chapter_number')} 章｜{c.get('title', '未命名')}｜{c.get('status', 'outline')}"
                for c in chapters
            ]

            selected_chapter_label = st.selectbox(
                "选择章节",
                chapter_labels,
                key=f"write_chapter_select_{current_project}",
            )

            selected_chapter_id = selected_chapter_label.split("｜")[0]
            chapter = get_chapter_by_id(state, selected_chapter_id)

            # 主稿编辑器采用“JSON + 独立草稿文件 + session_state”三重保护。
            # 优先级：当前会话编辑器 > 独立草稿备份文件 > story_state.json。
            draft_editor_key = f"draft_editor_{current_project}_{selected_chapter_id}"
            stored_draft_text = chapter.get("draft_text", "") if chapter else ""
            backup_draft_text = read_draft_backup(current_project, selected_chapter_id)

            if draft_editor_key not in st.session_state:
                st.session_state[draft_editor_key] = backup_draft_text if backup_draft_text.strip() else stored_draft_text
            elif stored_draft_text.strip() and not str(st.session_state.get(draft_editor_key, "")).strip():
                st.session_state[draft_editor_key] = backup_draft_text if backup_draft_text.strip() else stored_draft_text

            st.markdown("### 当前章节细纲摘要")
            st.write(f"**第 {chapter.get('chapter_number')} 章：{chapter.get('title', '未命名')}**")
            st.write(chapter.get("outline", ""))
            if chapter.get("ending_hook"):
                st.caption(f"章末钩子：{chapter.get('ending_hook')}")

            with st.expander("高级：查看完整结构化细纲", expanded=False):
                st.json(chapter)

            previous_summary = st.text_area(
                "上一章 / 近期剧情摘要",
                value="",
                height=100,
                placeholder="可选。为了省 token，建议只写最近 1-3 章摘要，不要粘全文。",
                key=f"previous_summary_{current_project}_{selected_chapter_id}",
            )

            st.markdown("### 文风学习系统 v0.6.6：作者可控的文风记忆")
            st.caption("这一版的重点不是让 AI 自作主张，而是让你能查看、编辑、删除文风规则；生成正文时只带入精简文风摘要。")

            style_memory = get_style_memory(state)
            style_prompt_text = style_profile_to_prompt_text(state)
            style_profile = style_memory.get("profile", {})

            with st.expander("文风记忆管理：查看 / 编辑 / 删除规则", expanded=False):
                st.markdown("**生成正文时实际带入的精简文风摘要：**")
                if style_prompt_text:
                    st.text_area(
                        "精简文风摘要，只读预览",
                        value=style_prompt_text,
                        height=180,
                        key=f"style_compact_preview_{current_project}_{selected_chapter_id}",
                        disabled=True,
                    )
                else:
                    st.info("当前还没有文风记忆。你可以在下面添加“AI 原稿 → 作者修正版”样本。")

                st.caption(
                    f"样本数：{len(style_memory.get('samples', []))}｜"
                    f"更新时间：{style_memory.get('updated_at', '未记录')}｜"
                    f"每类规则生成时最多带入 {STYLE_PROMPT_ITEM_LIMIT} 条"
                )

                edited_summary = st.text_area(
                    "文风概括",
                    value=str(style_profile.get("summary", "")),
                    height=90,
                    placeholder="例：克制、具体，少心理说明，多用动作和物件承载情绪。",
                    key=f"style_edit_summary_{current_project}_{selected_chapter_id}",
                )

                edited_field_texts = {}
                for field_key, field_label in PROFILE_LABELS.items():
                    edited_field_texts[field_key] = st.text_area(
                        f"{field_label}，一行一条",
                        value=items_to_list_text(style_profile.get(field_key, [])),
                        height=95,
                        key=f"style_edit_{field_key}_{current_project}_{selected_chapter_id}",
                    )

                col_save_style, col_compact_style, col_clear_style = st.columns(3)

                with col_save_style:
                    if st.button("保存文风档案修改", key=f"save_style_profile_{current_project}_{selected_chapter_id}"):
                        style_memory["profile"] = build_style_profile_from_editor(edited_summary, edited_field_texts)
                        style_memory["updated_at"] = now_text()
                        state["style_memory"] = style_memory
                        save_state(current_project, state)
                        st.success("文风档案已保存。")
                        st.rerun()

                with col_compact_style:
                    if st.button("AI 压缩整理文风档案", key=f"compact_style_profile_{current_project}_{selected_chapter_id}"):
                        if not style_prompt_text and not style_memory.get("samples"):
                            st.warning("当前文风档案为空，暂时不需要压缩。")
                        elif require_api_key():
                            prompt = build_style_compaction_prompt(state=state, style_memory=style_memory)
                            with st.spinner("正在压缩整理文风档案..."):
                                try:
                                    raw = call_llm(
                                        base_url=base_url,
                                        api_key=api_key,
                                        model=planner_model,
                                        prompt=prompt,
                                        system_prompt="你是中文网文作者文风档案整理助手。你必须只输出严格 JSON 对象。",
                                        temperature=0.15,
                                        max_tokens=2600,
                                    )
                                    compacted_profile = parse_json_object(raw)
                                    style_memory["profile"] = normalize_style_profile(compacted_profile)
                                    style_memory["updated_at"] = now_text()
                                    state["style_memory"] = style_memory
                                    save_state(current_project, state)
                                    st.success("文风档案已压缩整理。")
                                    st.rerun()
                                except Exception as e:
                                    st.error("文风档案压缩失败。AI 可能没有返回严格 JSON。")
                                    st.exception(e)

                with col_clear_style:
                    clear_confirm = st.checkbox(
                        "确认清空",
                        key=f"clear_style_confirm_{current_project}_{selected_chapter_id}",
                    )
                    if st.button(
                        "清空文风档案",
                        key=f"clear_style_profile_{current_project}_{selected_chapter_id}",
                        disabled=not clear_confirm,
                    ):
                        state["style_memory"] = default_style_memory()
                        save_state(current_project, state)
                        st.success("已清空文风档案。")
                        st.rerun()

                with st.expander("最近学习样本，只用于回顾，不会全部塞进生成提示词", expanded=False):
                    samples = style_memory.get("samples", [])
                    if not samples:
                        st.info("暂无学习样本。")
                    else:
                        for sample in reversed(samples[-10:]):
                            st.markdown(
                                f"**{sample.get('created_at', '')}｜{sample.get('chapter_id', '')}**"
                            )
                            st.caption("AI 原稿摘录")
                            st.write(sample.get("ai_excerpt", ""))
                            st.caption("作者修正版摘录")
                            st.write(sample.get("human_excerpt", ""))
                            st.divider()

            with st.expander("添加学习样本：AI 原稿 → 作者修正版", expanded=False):
                st.info("建议每次投喂 300-1500 字。样本会保留摘要，真正生成时只引用上面的精简文风档案。")

                style_ai_sample = st.text_area(
                    "AI 原稿 / 旧稿片段",
                    value="",
                    height=180,
                    placeholder="粘贴 AI 生成的原片段，建议 300-1500 字。",
                    key=f"style_ai_sample_{current_project}_{selected_chapter_id}",
                )
                style_human_sample = st.text_area(
                    "你的人工修正版",
                    value="",
                    height=180,
                    placeholder="粘贴你改过后的版本。系统会对比两者，提炼你的句式、对白、节奏和禁忌。",
                    key=f"style_human_sample_{current_project}_{selected_chapter_id}",
                )

                col_style_learn, col_style_json = st.columns(2)
                with col_style_learn:
                    if st.button("从这组改稿学习文风", key=f"learn_style_{current_project}_{selected_chapter_id}"):
                        if not style_ai_sample.strip() or not style_human_sample.strip():
                            st.error("请同时粘贴 AI 原稿和你的人工修正版。")
                        elif require_api_key():
                            prompt = build_style_learning_prompt(
                                state=state,
                                ai_text=style_ai_sample,
                                human_text=style_human_sample,
                                old_style_profile=style_memory.get("profile", {}),
                            )
                            with st.spinner("正在学习你的文风偏好..."):
                                try:
                                    raw = call_llm(
                                        base_url=base_url,
                                        api_key=api_key,
                                        model=planner_model,
                                        prompt=prompt,
                                        system_prompt="你是中文网文文风分析编辑。你必须只输出严格 JSON 对象。",
                                        temperature=0.18,
                                        max_tokens=3500,
                                    )
                                    learned = parse_json_object(raw)
                                    merge_style_profile_into_state(
                                        state,
                                        learned,
                                        chapter_id=selected_chapter_id,
                                        ai_excerpt=style_ai_sample,
                                        human_excerpt=style_human_sample,
                                    )
                                    save_state(current_project, state)
                                    st.success("文风学习完成，已写入本书文风档案。")
                                    st.rerun()
                                except Exception as e:
                                    st.error("文风学习失败。AI 可能没有返回严格 JSON。")
                                    st.exception(e)

                with col_style_json:
                    if st.button("查看完整文风 JSON", key=f"show_style_json_{current_project}_{selected_chapter_id}"):
                        st.session_state[f"show_style_json_flag_{current_project}_{selected_chapter_id}"] = True

                if st.session_state.get(f"show_style_json_flag_{current_project}_{selected_chapter_id}"):
                    st.json(get_style_memory(state))

            st.markdown("### 反 AI 味：分场景草稿工作流")
            st.caption("建议先用这里生成 3-6 个短场景，再由你人工拼接和重写关键段。不要再让 AI 一次写完整章。")

            default_style_note = "传统中文网文；句子不要过度工整；少总结，多动作、多物件、多场景压力；对白短，不解释设定。"
            if style_prompt_text:
                default_style_note += "\n\n【本书已学习到的作者文风】\n" + style_prompt_text

            anti_ai_style_note = st.text_area(
                "作者笔触要求，可选",
                value=default_style_note,
                height=150 if style_prompt_text else 90,
                key=f"anti_ai_style_note_{current_project}_{selected_chapter_id}",
            )

            scene_plan_key = f"scene_plan_{current_project}_{selected_chapter_id}"
            scene_raw_key = f"scene_plan_raw_{current_project}_{selected_chapter_id}"
            scene_drafts_key = f"scene_drafts_{current_project}_{selected_chapter_id}"
            if scene_drafts_key not in st.session_state:
                st.session_state[scene_drafts_key] = {}

            col_scene_plan, col_scene_merge = st.columns(2)
            with col_scene_plan:
                if st.button("① 拆成场景卡"):
                    if require_api_key():
                        prompt = build_scene_plan_prompt(state, chapter, previous_summary)
                        with st.spinner("正在拆分场景卡..."):
                            try:
                                raw = call_llm(
                                    base_url=base_url,
                                    api_key=api_key,
                                    model=planner_model,
                                    prompt=prompt,
                                    system_prompt="你是中文网文分场景策划编辑。你必须只输出严格 JSON 数组。",
                                    temperature=0.25,
                                    max_tokens=3500,
                                )
                                st.session_state[scene_raw_key] = raw
                                scenes = parse_json_array(raw)
                                st.session_state[scene_plan_key] = scenes
                                chapter["scene_plan"] = scenes
                                save_state(current_project, state)
                                st.success("已拆成场景卡。")
                                st.rerun()
                            except Exception as e:
                                st.warning("场景卡不是严格 JSON，已保存原始输出。")
                                st.exception(e)

            with col_scene_merge:
                if st.button("③ 合并场景草稿到主稿编辑器"):
                    scene_drafts = st.session_state.get(scene_drafts_key, {})
                    if not scene_drafts:
                        st.error("还没有场景草稿。")
                    else:
                        merged = "\n\n".join(
                            scene_drafts[k].strip()
                            for k in sorted(scene_drafts.keys(), key=lambda x: int(str(x).split("_")[-1]))
                            if str(scene_drafts[k]).strip()
                        )
                        if merged.strip():
                            chapter["draft_text"] = merged
                            chapter["raw_ai_draft_text"] = merged
                            chapter["status"] = "draft"
                            save_state(current_project, state)
                            st.session_state[draft_editor_key] = merged
                            write_draft_backup(current_project, selected_chapter_id, merged)
                            st.session_state[f"draft_last_saved_{current_project}_{selected_chapter_id}"] = now_text()
                            st.success("已合并到主稿编辑器，并已保存草稿备份。")
                            st.rerun()

            scenes = st.session_state.get(scene_plan_key) or chapter.get("scene_plan", [])
            raw_scenes = st.session_state.get(scene_raw_key, "")
            if scenes:
                with st.expander("查看 / 生成场景草稿", expanded=True):
                    for idx, scene in enumerate(scenes, start=1):
                        st.markdown(f"**场景 {idx}：{scene.get('scene_title', '未命名场景')}**")
                        st.caption(f"目标：{scene.get('scene_goal', '')}｜冲突：{scene.get('conflict', '')}｜出口：{scene.get('exit_hook', '')}")
                        with st.expander(f"场景 {idx} 完整场景卡", expanded=False):
                            st.json(scene)

                        if st.button(f"② 生成场景 {idx} 草稿", key=f"gen_scene_{current_project}_{selected_chapter_id}_{idx}"):
                            if require_api_key():
                                written_scenes = "\n\n".join(
                                    st.session_state.get(scene_drafts_key, {}).get(f"scene_{i}", "")
                                    for i in range(1, idx)
                                )
                                prompt = build_scene_draft_prompt(
                                    state=state,
                                    chapter=chapter,
                                    scene=scene,
                                    previous_summary=previous_summary,
                                    written_scenes=written_scenes,
                                    user_style_note=anti_ai_style_note,
                                )
                                with st.spinner(f"正在生成场景 {idx} 草稿..."):
                                    try:
                                        scene_text = call_llm(
                                            base_url=base_url,
                                            api_key=api_key,
                                            model=writer_model,
                                            prompt=prompt,
                                            system_prompt="你是中文网文场景草稿作者。只输出当前场景正文。",
                                            temperature=0.82,
                                            max_tokens=2200,
                                        ).strip()
                                        st.session_state[scene_drafts_key][f"scene_{idx}"] = scene_text
                                        st.success(f"场景 {idx} 草稿已生成。")
                                        st.rerun()
                                    except Exception as e:
                                        st.error("生成场景草稿失败。")
                                        st.exception(e)

                        scene_text = st.session_state.get(scene_drafts_key, {}).get(f"scene_{idx}", "")
                        if scene_text:
                            edited_scene = st.text_area(
                                f"场景 {idx} 草稿，可直接人工改",
                                value=scene_text,
                                height=260,
                                key=f"scene_editor_{current_project}_{selected_chapter_id}_{idx}",
                            )
                            st.session_state[scene_drafts_key][f"scene_{idx}"] = edited_scene
            elif raw_scenes:
                show_raw_output_expander("查看 AI 原始场景卡输出", raw_scenes, expanded=True)

            st.divider()

            col_gen, col_diag = st.columns(2)

            with col_gen:
                if st.button("根据细纲生成章节主稿"):
                    if require_api_key():
                        prompt = build_chapter_draft_prompt(state, chapter, previous_summary)

                        with st.spinner("正在生成章节主稿..."):
                            try:
                                draft = call_llm(
                                    base_url=base_url,
                                    api_key=api_key,
                                    model=writer_model,
                                    prompt=prompt,
                                    system_prompt="你是中文长篇网文正文作者，只输出正文。",
                                    temperature=0.72,
                                    max_tokens=6000,
                                ).strip()
                                if not draft:
                                    st.error("AI 返回了空正文。请检查模型名、API 额度，或换一个正文生成模型。")
                                else:
                                    chapter["draft_text"] = draft
                                    chapter["raw_ai_draft_text"] = draft
                                    chapter["status"] = "draft"
                                    state["project"]["current_chapter_id"] = selected_chapter_id
                                    save_state(current_project, state)

                                    # 关键修复：同步更新编辑器 widget 的 session_state，
                                    # 这样生成结果会立刻出现在下方“主稿编辑器”。
                                    st.session_state[draft_editor_key] = draft
                                    write_draft_backup(current_project, selected_chapter_id, draft)
                                    st.session_state[f"draft_last_saved_{current_project}_{selected_chapter_id}"] = now_text()
                                    st.success("章节主稿已生成，已自动填入下方主稿编辑器，并已保存草稿备份。")
                            except Exception as e:
                                st.error("生成失败。")
                                st.exception(e)

            with col_diag:
                if st.button("AI 诊断当前主稿"):
                    current_draft_for_diag = str(st.session_state.get(draft_editor_key, chapter.get("draft_text", "")))
                    if not current_draft_for_diag.strip():
                        st.error("当前章节没有主稿。")
                    elif require_api_key():
                        chapter["draft_text"] = current_draft_for_diag
                        if chapter.get("status") == "outline":
                            chapter["status"] = "draft"
                        save_state(current_project, state)
                        prompt = build_chapter_diagnosis_prompt(state, chapter, current_draft_for_diag)

                        with st.spinner("正在诊断主稿..."):
                            raw = call_llm(
                                base_url=base_url,
                                api_key=api_key,
                                model=planner_model,
                                prompt=prompt,
                                system_prompt="你是中文网文审稿编辑。你必须只输出严格 JSON 数组，不要输出解释、标题、Markdown。",
                                temperature=0.2,
                                max_tokens=4000,
                            )
                        chapter["diagnosis_raw"] = raw
                        try:
                            diagnosis = parse_json_array(raw)
                            chapter["diagnosis"] = diagnosis
                            save_state(current_project, state)
                            st.success("诊断完成。")
                            st.rerun()
                        except Exception as e:
                            chapter["diagnosis"] = []
                            save_state(current_project, state)
                            st.warning("AI 诊断返回内容不是严格 JSON，已保存原始输出。")
                            st.exception(e)

            st.divider()

            st.markdown("### ✍️ 作者粗稿润色：把手打粗稿整理成可编辑主稿")
            st.caption("当 AI 生成的草稿质量太差时，你可以先自己手打一版粗稿，再让 AI 在不改剧情方向的前提下润色。润色结果不会自动覆盖主稿，必须由你确认。")

            rough_key = f"author_rough_text_{current_project}_{selected_chapter_id}"
            polished_key = f"author_polished_text_{current_project}_{selected_chapter_id}"
            rough_saved_key = f"author_rough_last_saved_{current_project}_{selected_chapter_id}"
            polished_saved_key = f"author_polish_last_saved_{current_project}_{selected_chapter_id}"
            last_saved_key = f"draft_last_saved_{current_project}_{selected_chapter_id}"

            stored_rough_text = str(chapter.get("author_rough_text", ""))
            backup_rough_text = read_author_polish_backup(current_project, selected_chapter_id, "rough")
            if rough_key not in st.session_state:
                st.session_state[rough_key] = backup_rough_text if backup_rough_text.strip() else stored_rough_text

            stored_polished_text = str(chapter.get("polished_from_author_text", ""))
            backup_polished_text = read_author_polish_backup(current_project, selected_chapter_id, "polished")
            if polished_key not in st.session_state:
                st.session_state[polished_key] = backup_polished_text if backup_polished_text.strip() else stored_polished_text

            with st.expander("作者粗稿润色工作区", expanded=False):
                author_rough_text = st.text_area(
                    "作者手打粗稿",
                    height=260,
                    placeholder="把你自己先写出来的粗糙版本粘在这里。可以是半章，也可以是一个场景。建议一次 500-3000 字。",
                    key=rough_key,
                )

                col_polish_mode, col_polish_strength = st.columns(2)
                with col_polish_mode:
                    polish_mode = st.selectbox(
                        "润色模式",
                        [
                            "轻度润色：保留原文结构，只修顺句子",
                            "网文化精修：增强动作、画面、节奏和可读性",
                            "降 AI 腔：删除空泛总结、套话和说明书感",
                            "贴近文风记忆：优先按本书文风档案处理",
                        ],
                        key=f"author_polish_mode_{current_project}_{selected_chapter_id}",
                    )
                with col_polish_strength:
                    polish_strength = st.selectbox(
                        "改动幅度",
                        ["小：尽量保留原句", "中：可调整句式和段落", "大：可明显重组表达，但不改剧情"],
                        index=1,
                        key=f"author_polish_strength_{current_project}_{selected_chapter_id}",
                    )

                author_polish_note = st.text_area(
                    "额外要求，可选",
                    height=80,
                    placeholder="例：不要改对白意思；不要新增设定；多写动作，少写心理活动；保留粗粝感。",
                    key=f"author_polish_note_{current_project}_{selected_chapter_id}",
                )

                col_save_rough, col_polish_rough = st.columns(2)
                with col_save_rough:
                    if st.button("保存作者粗稿", key=f"save_author_rough_{current_project}_{selected_chapter_id}"):
                        current_rough_text = str(st.session_state.get(rough_key, author_rough_text))
                        ok = persist_author_polish_texts(
                            current_project,
                            selected_chapter_id,
                            rough_text=current_rough_text,
                        )
                        if ok:
                            chapter["author_rough_text"] = current_rough_text
                            chapter["author_rough_saved_at"] = now_text()
                            st.session_state[rough_saved_key] = chapter["author_rough_saved_at"]
                            st.success("作者粗稿已保存到 story_state.json，并备份到 drafts 文件夹。")
                        else:
                            st.error("保存失败：没有找到当前项目或章节。")

                with col_polish_rough:
                    if st.button("AI 润色作者粗稿", key=f"polish_author_rough_{current_project}_{selected_chapter_id}"):
                        current_rough_text = str(st.session_state.get(rough_key, author_rough_text)).strip()
                        if not current_rough_text:
                            st.error("请先输入作者手打粗稿。")
                        elif len(current_rough_text) > 9000:
                            st.warning("这段粗稿较长，建议分段润色；当前仍会尝试处理，但效果可能下降。")
                        elif require_api_key():
                            prompt = build_author_rough_polish_prompt(
                                state=state,
                                chapter=chapter,
                                rough_text=current_rough_text,
                                polish_mode=polish_mode,
                                polish_strength=polish_strength,
                                user_note=author_polish_note,
                                previous_summary=previous_summary,
                            )
                            with st.spinner("正在润色作者粗稿..."):
                                try:
                                    polished_text = call_llm(
                                        base_url=base_url,
                                        api_key=api_key,
                                        model=writer_model,
                                        prompt=prompt,
                                        system_prompt="你是中文网文润色编辑。只输出润色后的正文，不解释，不输出 Markdown。",
                                        temperature=0.45,
                                        max_tokens=6000,
                                    ).strip()
                                    if not polished_text:
                                        st.error("AI 返回了空润色结果。")
                                    else:
                                        st.session_state[polished_key] = polished_text
                                        persist_author_polish_texts(
                                            current_project,
                                            selected_chapter_id,
                                            rough_text=current_rough_text,
                                            polished_text=polished_text,
                                        )
                                        chapter["author_rough_text"] = current_rough_text
                                        chapter["polished_from_author_text"] = polished_text
                                        chapter["author_polish_saved_at"] = now_text()
                                        st.session_state[polished_saved_key] = chapter["author_polish_saved_at"]
                                        st.success("润色完成，已保存粗稿和润色稿。请在下方预览后再决定是否放入主稿编辑器。")
                                        st.rerun()
                                except Exception as e:
                                    st.error("润色失败。")
                                    st.exception(e)

                if st.session_state.get(rough_saved_key):
                    st.caption(f"作者粗稿最近保存：{st.session_state[rough_saved_key]}")
                elif chapter.get("author_rough_saved_at"):
                    st.caption(f"作者粗稿最近保存：{chapter.get('author_rough_saved_at')}")

                polished_preview = st.text_area(
                    "AI 润色结果预览，可人工微调",
                    height=320,
                    key=polished_key,
                )

                if st.session_state.get(polished_saved_key):
                    st.caption(f"润色稿最近保存：{st.session_state[polished_saved_key]}")
                elif chapter.get("author_polish_saved_at"):
                    st.caption(f"润色稿最近保存：{chapter.get('author_polish_saved_at')}")

                col_put_main, col_append_main, col_save_polished = st.columns(3)
                with col_put_main:
                    if st.button("放入主稿编辑器（替换当前主稿）", key=f"put_polished_to_main_{current_project}_{selected_chapter_id}"):
                        current_polished = str(st.session_state.get(polished_key, polished_preview)).strip()
                        if not current_polished:
                            st.error("还没有润色结果。")
                        else:
                            ok = persist_chapter_draft(current_project, selected_chapter_id, current_polished)
                            if ok:
                                st.session_state[draft_editor_key] = current_polished
                                chapter["draft_text"] = current_polished
                                chapter["status"] = "draft"
                                st.session_state[last_saved_key] = now_text()
                                st.success("润色稿已放入主稿编辑器，并保存为当前主稿。")
                                st.rerun()
                            else:
                                st.error("写入主稿失败：没有找到当前项目或章节。")

                with col_append_main:
                    if st.button("追加到主稿末尾", key=f"append_polished_to_main_{current_project}_{selected_chapter_id}"):
                        current_polished = str(st.session_state.get(polished_key, polished_preview)).strip()
                        current_main = str(st.session_state.get(draft_editor_key, "")).strip()
                        if not current_polished:
                            st.error("还没有润色结果。")
                        else:
                            merged_text = (current_main + "\n\n" + current_polished).strip() if current_main else current_polished
                            ok = persist_chapter_draft(current_project, selected_chapter_id, merged_text)
                            if ok:
                                st.session_state[draft_editor_key] = merged_text
                                chapter["draft_text"] = merged_text
                                chapter["status"] = "draft"
                                st.session_state[last_saved_key] = now_text()
                                st.success("润色稿已追加到主稿末尾，并保存为当前主稿。")
                                st.rerun()
                            else:
                                st.error("追加失败：没有找到当前项目或章节。")

                with col_save_polished:
                    if st.button("只保存润色结果", key=f"save_polished_only_{current_project}_{selected_chapter_id}"):
                        current_polished = str(st.session_state.get(polished_key, polished_preview))
                        ok = persist_author_polish_texts(
                            current_project,
                            selected_chapter_id,
                            polished_text=current_polished,
                        )
                        if ok:
                            chapter["polished_from_author_text"] = current_polished
                            chapter["author_polish_saved_at"] = now_text()
                            st.session_state[polished_saved_key] = chapter["author_polish_saved_at"]
                            st.success("润色结果已保存，但没有写入主稿编辑器。")
                        else:
                            st.error("保存润色结果失败。")

            st.divider()

            st.markdown("### 主稿编辑器")

            draft_text = st.text_area(
                "你可以直接人工修改主稿",
                height=560,
                key=draft_editor_key,
                on_change=autosave_draft_from_editor,
                args=(current_project, selected_chapter_id, draft_editor_key),
            )

            last_saved_key = f"draft_last_saved_{current_project}_{selected_chapter_id}"
            if st.session_state.get(last_saved_key):
                st.caption(f"最近自动保存：{st.session_state[last_saved_key]}")
            elif chapter.get("draft_saved_at"):
                st.caption(f"最近保存：{chapter.get('draft_saved_at')}")
            else:
                st.caption("提示：修改主稿后，点击页面其他按钮或离开输入框会自动保存；也可以手动点击下方保存。")

            col_save_draft, col_learn_from_editor = st.columns(2)
            with col_save_draft:
                if st.button("保存主稿修改"):
                    current_editor_text = str(st.session_state.get(draft_editor_key, draft_text))
                    ok = persist_chapter_draft(current_project, selected_chapter_id, current_editor_text)
                    if ok:
                        # 同步当前内存里的 chapter，避免本轮页面继续显示旧状态。
                        chapter["draft_text"] = current_editor_text
                        chapter["draft_saved_at"] = now_text()
                        if chapter.get("status") == "outline":
                            chapter["status"] = "draft"
                        st.session_state[last_saved_key] = chapter["draft_saved_at"]
                        st.success("主稿已保存到 story_state.json，并额外备份到 drafts 文件夹。")
                        st.rerun()
                    else:
                        st.error("保存失败：没有找到当前项目或当前章节。")

            with col_learn_from_editor:
                if st.button("从原始 AI 主稿 → 当前编辑器学习文风"):
                    original_ai_draft = str(chapter.get("raw_ai_draft_text", "")).strip()
                    current_editor_text = str(st.session_state.get(draft_editor_key, draft_text)).strip()
                    if not original_ai_draft:
                        st.error("没有找到原始 AI 主稿。请先生成一次主稿，或使用上方手动粘贴样本学习。")
                    elif not current_editor_text:
                        st.error("当前编辑器为空。")
                    elif original_ai_draft.strip() == current_editor_text.strip():
                        st.warning("当前编辑器和原始 AI 主稿几乎一致。建议先人工修改后再学习。")
                    elif require_api_key():
                        style_memory = get_style_memory(state)
                        prompt = build_style_learning_prompt(
                            state=state,
                            ai_text=original_ai_draft[:6000],
                            human_text=current_editor_text[:6000],
                            old_style_profile=style_memory.get("profile", {}),
                        )
                        with st.spinner("正在从本章改稿学习文风..."):
                            try:
                                raw = call_llm(
                                    base_url=base_url,
                                    api_key=api_key,
                                    model=planner_model,
                                    prompt=prompt,
                                    system_prompt="你是中文网文文风分析编辑。你必须只输出严格 JSON 对象。",
                                    temperature=0.18,
                                    max_tokens=3500,
                                )
                                learned = parse_json_object(raw)
                                merge_style_profile_into_state(
                                    state,
                                    learned,
                                    chapter_id=selected_chapter_id,
                                    ai_excerpt=original_ai_draft,
                                    human_excerpt=current_editor_text,
                                )
                                persist_chapter_draft(current_project, selected_chapter_id, current_editor_text)
                                chapter["draft_text"] = current_editor_text
                                save_state(current_project, state)
                                st.success("已从本章改稿中学习文风，当前主稿也已保存。")
                                st.rerun()
                            except Exception as e:
                                st.error("文风学习失败。")
                                st.exception(e)

            diagnosis = chapter.get("diagnosis", [])
            diagnosis_raw = chapter.get("diagnosis_raw", "")
            if diagnosis:
                st.markdown("### 诊断清单")
                st.json(diagnosis)
            elif diagnosis_raw:
                show_raw_output_expander("查看 AI 原始诊断输出", diagnosis_raw, expanded=True)

            st.divider()

            st.markdown("### AI 味诊断 / 降 AI 腔局部处理")
            col_ai_taste, col_ai_hint = st.columns(2)
            with col_ai_taste:
                if st.button("检查 AI 味问题"):
                    current_text = str(st.session_state.get(draft_editor_key, chapter.get("draft_text", "")))
                    if not current_text.strip():
                        st.error("当前主稿为空。")
                    elif require_api_key():
                        prompt = build_ai_taste_diagnosis_prompt(state, chapter, current_text)
                        with st.spinner("正在检查 AI 味..."):
                            try:
                                raw = call_llm(
                                    base_url=base_url,
                                    api_key=api_key,
                                    model=planner_model,
                                    prompt=prompt,
                                    system_prompt="你是中文网文文本质检编辑。你必须只输出严格 JSON 数组。",
                                    temperature=0.15,
                                    max_tokens=4000,
                                )
                                chapter["ai_taste_diagnosis_raw"] = raw
                                chapter["ai_taste_diagnosis"] = parse_json_array(raw)
                                save_state(current_project, state)
                                st.success("AI 味诊断完成。")
                                st.rerun()
                            except Exception as e:
                                chapter["ai_taste_diagnosis"] = []
                                save_state(current_project, state)
                                st.warning("AI 味诊断返回内容不是严格 JSON，已保存原始输出。")
                                st.exception(e)
            with col_ai_hint:
                st.caption("用法：先检查 AI 味，然后只挑最刺眼的 1-3 段做局部处理。不要整章反复改写。")

            ai_taste_items = chapter.get("ai_taste_diagnosis", [])
            ai_taste_raw = chapter.get("ai_taste_diagnosis_raw", "")
            if ai_taste_items:
                st.json(ai_taste_items)
            elif ai_taste_raw:
                show_raw_output_expander("查看 AI 味诊断原始输出", ai_taste_raw, expanded=False)

            de_ai_text = st.text_area(
                "粘贴需要降 AI 腔的片段",
                value="",
                height=160,
                key=f"de_ai_selected_{current_project}_{selected_chapter_id}",
            )
            de_ai_before = st.text_area(
                "前文上下文，可选",
                value="",
                height=80,
                key=f"de_ai_before_{current_project}_{selected_chapter_id}",
            )
            de_ai_after = st.text_area(
                "后文上下文，可选",
                value="",
                height=80,
                key=f"de_ai_after_{current_project}_{selected_chapter_id}",
            )
            if st.button("局部降 AI 腔"):
                if not de_ai_text.strip():
                    st.error("请先粘贴需要处理的片段。")
                elif require_api_key():
                    prompt = build_de_ai_rewrite_prompt(
                        state=state,
                        chapter=chapter,
                        selected_text=de_ai_text,
                        before_context=de_ai_before,
                        after_context=de_ai_after,
                        user_style_note=anti_ai_style_note,
                    )
                    with st.spinner("正在局部降 AI 腔..."):
                        try:
                            rewritten = call_llm(
                                base_url=base_url,
                                api_key=api_key,
                                model=writer_model,
                                prompt=prompt,
                                system_prompt="你是中文网文局部降 AI 腔改写助手。只输出改写后的片段。",
                                temperature=0.78,
                                max_tokens=2200,
                            )
                            st.session_state[f"de_ai_rewritten_{current_project}_{selected_chapter_id}"] = rewritten
                            st.success("已生成局部处理版本。")
                        except Exception as e:
                            st.error("局部处理失败。")
                            st.exception(e)

            de_ai_rewritten = st.session_state.get(f"de_ai_rewritten_{current_project}_{selected_chapter_id}", "")
            if de_ai_rewritten:
                st.text_area(
                    "降 AI 腔结果，请手动复制回主稿对应位置",
                    value=de_ai_rewritten,
                    height=220,
                    key=f"de_ai_rewritten_result_{current_project}_{selected_chapter_id}",
                )

            st.divider()

            st.markdown("### 通用局部改写")

            selected_text = st.text_area(
                "粘贴需要局部改写的片段",
                value="",
                height=180,
                key=f"rewrite_selected_{current_project}_{selected_chapter_id}",
            )

            rewrite_instruction = st.text_input(
                "改写要求",
                value="降低 AI 腔，减少解释，加强动作和细节，但不要改变剧情事实。",
                key=f"rewrite_instruction_{current_project}_{selected_chapter_id}",
            )

            before_context = st.text_area(
                "前文上下文，可选",
                value="",
                height=100,
                key=f"rewrite_before_{current_project}_{selected_chapter_id}",
            )

            after_context = st.text_area(
                "后文上下文，可选",
                value="",
                height=100,
                key=f"rewrite_after_{current_project}_{selected_chapter_id}",
            )

            if st.button("AI 局部改写"):
                if not selected_text.strip():
                    st.error("请先粘贴需要改写的片段。")
                elif require_api_key():
                    prompt = build_local_rewrite_prompt(
                        state=state,
                        chapter=chapter,
                        selected_text=selected_text,
                        rewrite_instruction=rewrite_instruction,
                        before_context=before_context,
                        after_context=after_context,
                    )

                    with st.spinner("正在局部改写..."):
                        try:
                            rewritten = call_llm(
                                base_url=base_url,
                                api_key=api_key,
                                model=writer_model,
                                prompt=prompt,
                                system_prompt="你是中文网文局部改写助手，只输出改写片段。",
                                temperature=0.68,
                                max_tokens=2500,
                            )
                            st.session_state[f"rewritten_{current_project}_{selected_chapter_id}"] = rewritten
                            st.success("局部改写完成。")
                        except Exception as e:
                            st.error("局部改写失败。")
                            st.exception(e)

            rewritten = st.session_state.get(f"rewritten_{current_project}_{selected_chapter_id}", "")
            if rewritten:
                st.text_area(
                    "局部改写结果，请手动复制到主稿对应位置",
                    value=rewritten,
                    height=220,
                    key=f"rewritten_result_{current_project}_{selected_chapter_id}",
                )

            st.divider()

            st.markdown("### 定稿入库")

            if st.button("保存为正式章节"):
                final_text = str(st.session_state.get(draft_editor_key, draft_text)).strip()

                if not final_text:
                    st.error("正文不能为空。")
                else:
                    path = save_chapter_text(
                        current_project,
                        int(chapter.get("chapter_number", 1)),
                        chapter.get("title", "未命名章节"),
                        final_text,
                    )
                    chapter["final_text_path"] = path
                    chapter["status"] = "final"
                    chapter["draft_text"] = final_text
                    write_draft_backup(current_project, selected_chapter_id, final_text)

                    state["project"]["current_chapter_id"] = selected_chapter_id
                    state["project"]["total_chars"] = count_final_chars(current_project, state)

                    save_state(current_project, state)

                    st.success(f"已保存为正式章节：{path}")
                    st.rerun()

            if chapter.get("status") == "final":
                final_text = load_chapter_text(current_project, int(chapter.get("chapter_number", 1)))
                with st.expander("查看已入库正式章节", expanded=False):
                    st.text_area(
                        "正式章节",
                        value=final_text,
                        height=420,
                        key=f"final_view_{current_project}_{selected_chapter_id}",
                    )


# -----------------------------
# ⑤ 状态库
# -----------------------------
with tab_state:
    st.subheader("⑤ 状态库")

    if state is None:
        st.warning("请先选择一本书。")
    else:
        st.info("状态库仍保留手动编辑，但新增了资料导入提取入口。")

        st.markdown("### 智能提取状态，可选")
        raw_state_text = st.text_area(
            "粘贴人物、道具、伏笔、章节总结等资料",
            value="",
            height=180,
            key=f"state_import_text_{current_project}",
        )

        if st.button("AI 提取到状态库", key=f"btn_state_import_{current_project}"):
            if not raw_state_text.strip():
                st.error("请先粘贴资料。")
            elif require_api_key():
                prompt = build_state_extract_prompt(raw_state_text, state)
                with st.spinner("正在提取状态库信息..."):
                    raw = call_llm(
                        base_url=base_url,
                        api_key=api_key,
                        model=planner_model,
                        prompt=prompt,
                        system_prompt="你是中文网文状态库整理助手。你必须只输出严格 JSON 对象，不要输出解释、标题、Markdown。",
                        temperature=0.2,
                        max_tokens=4000,
                    )
                st.session_state[f"state_import_raw_{current_project}"] = raw
                try:
                    data = parse_json_object(raw)
                    result = normalize_state_extract_import(data)
                    st.session_state[f"state_import_result_{current_project}"] = result
                    st.success("状态信息已提取。请检查后确认合并。")
                except Exception as e:
                    st.session_state[f"state_import_result_{current_project}"] = None
                    st.warning("AI 返回内容不是严格 JSON，已保存原始输出。")
                    st.exception(e)

        state_import_result = st.session_state.get(f"state_import_result_{current_project}")
        state_import_raw = st.session_state.get(f"state_import_raw_{current_project}", "")

        show_raw_output_expander("查看 AI 原始状态提取输出", state_import_raw, expanded=not bool(state_import_result))

        if state_import_result:
            st.json(state_import_result)
            if st.button("确认合并到状态库", key=f"btn_merge_state_import_{current_project}"):
                state = merge_state_extract_into_state(state, state_import_result)
                save_state(current_project, state)
                st.success("已合并到状态库。")
                st.rerun()

        st.divider()

        with st.expander("高级：手动编辑状态库 JSON", expanded=False):
            characters_text = st.text_area(
                "characters JSON",
                value=safe_json_dumps(state.get("characters", {})),
                height=240,
                key=f"characters_editor_{current_project}",
            )

            items_text = st.text_area(
                "items JSON",
                value=safe_json_dumps(state.get("items", {})),
                height=240,
                key=f"items_editor_{current_project}",
            )

            flags_text = st.text_area(
                "flags JSON",
                value=safe_json_dumps(state.get("flags", {})),
                height=180,
                key=f"flags_editor_{current_project}",
            )

            if st.button("保存状态库"):
                state["characters"] = json_text_to_dict(characters_text, state.get("characters", {}))
                state["items"] = json_text_to_dict(items_text, state.get("items", {}))
                state["flags"] = json_text_to_dict(flags_text, state.get("flags", {}))
                save_state(current_project, state)
                st.success("状态库已保存。")
                st.rerun()


# -----------------------------
# ⑥ 原始 JSON
# -----------------------------
with tab_raw:
    st.subheader("⑥ 原始 JSON")

    if state is None:
        st.warning("请先选择一本书。")
    else:
        st.json(state)
