BOOK_IMPORT_SCHEMA = {
    "book_bible": {
        "genre": "题材 / 类型",
        "core_selling_point": "核心卖点",
        "protagonist_direction": "主角方向",
        "world_rules": "世界观规则 / 限制",
        "cheat_rules": "金手指规则 / 限制",
        "style_direction": "文风方向",
        "must_avoid": "全书必须避免的内容",
    },
    "volume_outline": "当前卷 / 第一卷粗纲",
}


CHAPTER_IMPORT_SCHEMA = {
    "chapter_number": 1,
    "title": "章节标题",
    "target_words": 3000,
    "outline": "本章自然语言细纲",
    "must_include": ["本章必须包含的剧情点"],
    "must_avoid": ["本章必须避免的内容"],
    "appearing_characters": ["本章出场人物"],
    "items_involved": ["本章涉及道具 / 线索 / 资源"],
    "ending_hook": "章末钩子",
}


BATCH_CHAPTER_IMPORT_SCHEMA = [CHAPTER_IMPORT_SCHEMA]


STATE_EXTRACT_SCHEMA = {
    "characters": {},
    "items": {},
    "flags": {},
    "summary": "资料摘要",
}
