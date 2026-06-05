import json

from llm.prompts import state_context
from llm.schemas import (
    BATCH_CHAPTER_IMPORT_SCHEMA,
    BOOK_IMPORT_SCHEMA,
    CHAPTER_IMPORT_SCHEMA,
    STATE_EXTRACT_SCHEMA,
)


def build_book_import_prompt(raw_text: str) -> str:
    return f"""
你是中文长篇网文项目设定整理助手。

任务：
从用户提供的【原始设定资料】中，整理出 NovelFlow 需要的建书信息。

严格要求：
1. 只输出一个 JSON 对象，不要输出解释、标题、Markdown。
2. 不要编造用户没有提供的重要设定。
3. 如果资料没有明确提到某个字段，填空字符串。
4. 保留中文表达，尽量简洁、可直接进入项目数据库。
5. volume_outline 可以从卷纲、剧情走向、阶段规划中提炼。

输出 JSON 格式必须接近：
{json.dumps(BOOK_IMPORT_SCHEMA, ensure_ascii=False, indent=2)}

【原始设定资料】
{raw_text}
"""


def build_chapter_outline_import_prompt(raw_text: str, state: dict) -> str:
    return f"""
你是中文长篇网文章节细纲整理助手。

任务：
根据【项目上下文】和用户提供的【章节资料】，整理成 NovelFlow 的单章细纲 JSON。

严格要求：
1. 只输出一个 JSON 对象，不要输出解释、标题、Markdown。
2. 不要写正文。
3. 不要擅自改变作者提供的剧情方向。
4. 用户没有明确写出的字段，可以根据资料做低风险提炼；不要过度扩写。
5. must_include 写成剧情点列表。
6. must_avoid 写成禁忌或风险列表。
7. appearing_characters 只列本章可能出场的人物。
8. items_involved 只列本章明显涉及的道具、线索、资源、身份、地点凭证等。
9. ending_hook 写成一句话。
10. 如果没有明确章节序号，可以填 0；程序会自动补成下一章。

输出 JSON 格式必须接近：
{json.dumps(CHAPTER_IMPORT_SCHEMA, ensure_ascii=False, indent=2)}

【项目上下文】
{state_context(state)}

【章节资料】
{raw_text}
"""


def build_batch_chapter_import_prompt(raw_text: str, state: dict) -> str:
    return f"""
你是中文长篇网文批量章节细纲整理助手。

任务：
把用户提供的多章资料整理成 JSON 数组。每个数组元素是一章细纲。

严格要求：
1. 只输出 JSON 数组，不要输出解释、标题、Markdown。
2. 不要写正文。
3. 不要合并明显属于不同章节的内容。
4. 不确定章节序号时，chapter_number 填 0。
5. 每章格式必须接近指定 schema。

单章 schema：
{json.dumps(CHAPTER_IMPORT_SCHEMA, ensure_ascii=False, indent=2)}

数组示例：
{json.dumps(BATCH_CHAPTER_IMPORT_SCHEMA, ensure_ascii=False, indent=2)}

【项目上下文】
{state_context(state)}

【多章资料】
{raw_text}
"""


def build_state_extract_prompt(raw_text: str, state: dict) -> str:
    return f"""
你是中文长篇网文状态库整理助手。

任务：
从用户提供的资料中提取人物、道具、flags 和摘要，供 NovelFlow 状态库使用。

严格要求：
1. 只输出 JSON 对象，不要输出解释、标题、Markdown。
2. 不要编造重大设定。
3. characters 用对象保存，每个 key 是人物名。
4. items 用对象保存，每个 key 是道具 / 线索 / 资源名。
5. flags 用对象保存，每个 key 是事实、伏笔或状态标记。

输出 JSON 格式必须接近：
{json.dumps(STATE_EXTRACT_SCHEMA, ensure_ascii=False, indent=2)}

【当前项目上下文】
{state_context(state)}

【待提取资料】
{raw_text}
"""
