import json


def state_context(state: dict) -> str:
    context = {
        "project": state.get("project", {}),
        "book_bible": state.get("book_bible", {}),
        "volume_outline": state.get("volume_outline", ""),
        "characters": state.get("characters", {}),
        "items": state.get("items", {}),
        "flags": state.get("flags", {}),
        "style_memory": state.get("style_memory", {}),
        "recent_chapters": state.get("chapters", [])[-5:],
    }
    return json.dumps(context, ensure_ascii=False, indent=2)


def build_chapter_draft_prompt(state: dict, chapter: dict, previous_summary: str = "") -> str:
    return f"""
你现在是中文长篇网文正文作者。

你的任务：
严格根据【章节细纲】生成一章正文主稿。

核心规则：
1. 人类细纲优先，不能擅自改变剧情方向。
2. 只写本章正文，不要写解释、总结、标题、Markdown。
3. 不要超前写后续章节。
4. 不要违反“必须避免”的内容。
5. 不要让主角突然获得不合理的能力、信息或胜利。
6. 不要一次性解释完世界观、金手指、幕后真相。
7. 必须完成本章目标，并停在章末钩子附近。
8. 台词要像人物说话，不要像设定说明书。
9. 少用“这一刻”“命运的齿轮”“空气仿佛凝固”“他不知道的是”等套话。
10. 如果【项目上下文】里存在 style_memory，必须优先学习其中的作者句式、对白、节奏和禁忌。
11. 字数尽量接近 target_words，但优先保证内容质量。

【项目上下文】
{state_context(state)}

【上一章/近期摘要】
{previous_summary}

【章节细纲】
{json.dumps(chapter, ensure_ascii=False, indent=2)}

请直接输出正文主稿。
"""


def build_chapter_diagnosis_prompt(state: dict, chapter: dict, draft_text: str) -> str:
    return f"""
你现在是中文网文审稿编辑。

任务：
检查【章节主稿】是否符合【章节细纲】和【项目设定】，输出问题清单。

要求：
1. 只输出 JSON 数组。
2. 不要改写正文。
3. 不要泛泛而谈。
4. 每个问题必须指出具体段落或具体内容。
5. 优先检查：偏离细纲、违反禁忌、节奏拖沓、解释过多、AI 腔、人物行为不合理、章末钩子不足。
6. 如果没有严重问题，输出空数组 []。

输出格式：
[
  {{
    "severity": "高/中/低",
    "problem_type": "偏离细纲/违反禁忌/节奏问题/人物问题/设定问题/语言问题/章末钩子/其他",
    "location": "第几段或大致位置",
    "problem": "具体问题",
    "suggestion": "修改建议"
  }}
]

【项目上下文】
{state_context(state)}

【章节细纲】
{json.dumps(chapter, ensure_ascii=False, indent=2)}

【章节主稿】
{draft_text}
"""


def build_local_rewrite_prompt(
    state: dict,
    chapter: dict,
    selected_text: str,
    rewrite_instruction: str,
    before_context: str = "",
    after_context: str = "",
) -> str:
    return f"""
你现在是中文网文局部改写助手。

任务：
只改写【需要改写的片段】，不要改写前后文。

硬性规则：
1. 只输出改写后的片段。
2. 不要解释。
3. 不要输出 Markdown。
4. 必须遵守章节细纲和项目设定。
5. 不要新增重大剧情事实。
6. 不要提前揭露隐藏信息。
7. 保留片段在前后文中的衔接功能。
8. 根据用户改写要求执行，不要自由发挥过度。

【项目上下文】
{state_context(state)}

【章节细纲】
{json.dumps(chapter, ensure_ascii=False, indent=2)}

【前文上下文】
{before_context}

【需要改写的片段】
{selected_text}

【后文上下文】
{after_context}

【用户改写要求】
{rewrite_instruction}

请只输出改写后的片段。
"""


def build_next_outline_ideas_prompt(state: dict, user_note: str) -> str:
    return f"""
你现在是中文长篇网文大纲助手。

任务：
基于当前项目状态和作者补充要求，提供 3 到 5 个“下一章细纲方案”。

定位：
这是辅助功能，不是让 AI 决定正史。作者会从中选择或改写。

输出要求：
1. 只输出 JSON 数组。
2. 每个方案都要有章节标题、章节目标、主要剧情点、风险、章末钩子。
3. 不要写正文。
4. 不要跳太远。
5. 不要破坏已有设定。

格式：
[
  {{
    "title": "章节标题",
    "chapter_goal": "本章功能",
    "plot_beats": ["剧情点1", "剧情点2", "剧情点3"],
    "characters": [],
    "items": [],
    "risk": "这个方案的风险",
    "ending_hook": "章末钩子"
  }}
]

【项目上下文】
{state_context(state)}

【作者补充要求】
{user_note}
"""



def build_scene_plan_prompt(state: dict, chapter: dict, previous_summary: str = "") -> str:
    return f"""
你现在是中文网文分场景策划编辑。

任务：
不要写正文。请把【章节细纲】拆成 3 到 6 个可写作的场景，用来降低长篇一次性生成导致的 AI 腔。

输出要求：
1. 只输出严格 JSON 数组，不要 Markdown，不要解释。
2. 每个场景必须有明确冲突、人物动作、场景出口。
3. 不要把“心理总结”“气氛渲染”当成场景。
4. 每个场景建议 400-900 字。
5. 不要新增重大剧情事实，不要提前揭秘。

输出格式：
[
  {{
    "scene_no": 1,
    "scene_title": "场景标题",
    "location": "地点",
    "pov_character": "视角人物",
    "scene_goal": "这个场景要完成什么",
    "conflict": "场景内的具体冲突",
    "key_beats": ["动作/事件节点1", "动作/事件节点2", "动作/事件节点3"],
    "must_avoid": ["本场景必须避免的写法"],
    "exit_hook": "场景结束时把读者推向下一场的钩子",
    "target_words": 600
  }}
]

【项目上下文】
{state_context(state)}

【上一章/近期摘要】
{previous_summary}

【章节细纲】
{json.dumps(chapter, ensure_ascii=False, indent=2)}
"""


def build_scene_draft_prompt(
    state: dict,
    chapter: dict,
    scene: dict,
    previous_summary: str = "",
    written_scenes: str = "",
    user_style_note: str = "",
) -> str:
    return f"""
你现在是中文网文场景草稿作者。

任务：
只根据【当前场景卡】写一个场景草稿，不要写整章。

硬性规则：
1. 只输出正文，不要标题、解释、Markdown。
2. 场景必须通过动作、对白、环境细节推进，不要靠大段总结。
3. 少用抽象判断词：震惊、复杂、冰冷、凝重、仿佛、命运、这一刻、深吸一口气、眼神一凝。
4. 不要频繁写“他知道/他明白/他意识到”来代替具体行为。
5. 人物说话要短，带目的，不要用对白解释设定。
6. 每段尽量有具体动作、物件、位置或声音。
7. 不要新增重大剧情事实，不要提前揭秘。
8. 保留网文可读性，但避免说明书式推进。
9. 如果【项目上下文】里存在 style_memory，必须优先贴近作者已学习到的句式、对白和节奏。
10. 字数接近当前场景 target_words。

【作者额外风格要求】
{user_style_note}

【项目上下文】
{state_context(state)}

【上一章/近期摘要】
{previous_summary}

【章节细纲】
{json.dumps(chapter, ensure_ascii=False, indent=2)}

【已经写出的前序场景】
{written_scenes}

【当前场景卡】
{json.dumps(scene, ensure_ascii=False, indent=2)}

请只输出当前场景正文。
"""


def build_ai_taste_diagnosis_prompt(state: dict, chapter: dict, draft_text: str) -> str:
    return f"""
你现在是中文网文文本质检编辑，专门检查 AI 腔。

任务：
检查【正文】中哪些地方像 AI 写的，并给出可操作修改方向。不要整段改写。

只输出严格 JSON 数组：
[
  {{
    "severity": "高/中/低",
    "issue_type": "空泛总结/解释性对白/情绪标签/套话句式/节奏太顺/动作空洞/设定说明/人物不像人/其他",
    "location": "第几段或原文短句",
    "problem": "为什么像 AI",
    "fix_direction": "具体怎么改，尽量落到动作、物件、对白或删减"
  }}
]

判断标准：
1. 抽象词堆叠，但缺少具体动作和物件。
2. 人物对白像在解释剧情或设定。
3. 段落结尾总是总结意义、气氛或命运。
4. 句式过整齐，情绪推进太顺。
5. 滥用“这一刻、仿佛、空气凝固、深吸一口气、眼神复杂、他知道”。
6. 本该写冲突，却写成旁白概括。

【项目上下文】
{state_context(state)}

【章节细纲】
{json.dumps(chapter, ensure_ascii=False, indent=2)}

【正文】
{draft_text}
"""


def build_de_ai_rewrite_prompt(
    state: dict,
    chapter: dict,
    selected_text: str,
    before_context: str = "",
    after_context: str = "",
    user_style_note: str = "",
) -> str:
    return f"""
你现在是中文网文局部降 AI 腔改写助手。

任务：
只改写【需要处理的片段】，目标不是华丽，而是更像作者粗粝写出来的连载文本。

硬性规则：
1. 只输出改写后的片段。
2. 不要解释，不要 Markdown。
3. 不改变剧情事实、人物关系、胜负结果。
4. 优先删掉空泛总结、情绪标签、套话。
5. 用具体动作、物件、位置、声音替代抽象心理说明。
6. 对白要短，不要让人物替作者解释设定。
7. 句长要有变化，可以保留一点不完美，不要写得过于工整。
8. 不要新增重大设定，不要提前揭秘。

【作者额外风格要求】
{user_style_note}

【项目上下文】
{state_context(state)}

【章节细纲】
{json.dumps(chapter, ensure_ascii=False, indent=2)}

【前文上下文】
{before_context}

【需要处理的片段】
{selected_text}

【后文上下文】
{after_context}

请只输出改写后的片段。
"""



def build_style_learning_prompt(
    state: dict,
    ai_text: str,
    human_text: str,
    old_style_profile: dict | None = None,
) -> str:
    return f"""
你现在是中文网文文风分析编辑。

任务：
对比【AI 原稿】和【作者人工修正版】，提炼作者稳定的行文偏好。

重要原则：
1. 不是模仿某个外部作者，只学习当前项目作者自己的改稿习惯。
2. 不要评价谁写得好，只提炼“以后 AI 生成时应该怎么写”。
3. 规则要具体，可执行，能直接放进后续正文生成 prompt。
4. 不要输出 Markdown，不要解释，只输出严格 JSON 对象。
5. 如果样本很短，也要谨慎提炼，不要夸大。

请输出格式：
{{
  "summary": "用 1-3 句话概括作者文风",
  "sentence_rhythm": ["句式和节奏规则"],
  "word_choice": ["遣词用字偏好"],
  "dialogue": ["对白写法规则"],
  "description": ["动作、环境、物件描写规则"],
  "pacing": ["叙事推进和节奏规则"],
  "taboos": ["作者明显不喜欢的写法"],
  "rewrite_rules": ["以后把 AI 初稿改成作者风格时应遵守的规则"]
}}

【已有文风档案】
{json.dumps(old_style_profile or {}, ensure_ascii=False, indent=2)}

【项目上下文】
{state_context(state)}

【AI 原稿】
{ai_text}

【作者人工修正版】
{human_text}
"""
