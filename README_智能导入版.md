# NovelFlow v0.6.2 智能导入版说明

这个版本解决“输入框太多”的问题。

核心变化：

1. 建书页新增“智能导入建书”。
2. 大纲工作台新增“智能导入单章细纲”。
3. 状态库新增“AI 提取到状态库”。
4. 原来的大量字段没有删除，而是移动到“高级”折叠区。
5. AI 返回 JSON 失败时，不再直接崩溃，会保存原始输出，方便复制查看。

## 运行方式

在项目文件夹中执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

如果你已经激活虚拟环境，也可以用：

```powershell
pip install -r requirements.txt
streamlit run app.py
```

## 新手使用流程

1. 打开左侧 AI 设置，填写 Base URL、API Key、模型名。
2. 进入“② 建书”。
3. 选择“智能导入建书（推荐）”。
4. 粘贴你的小说设定，或上传 txt/md 文件。
5. 点击“AI 整理设定”。
6. 看下方 JSON 预览。
7. 没问题就点“确认创建项目”。
8. 进入“③ 大纲工作台”。
9. 在“智能导入单章细纲”里粘贴章节想法。
10. 点击“AI 整理为章节细纲”。
11. 预览无误后，点击“确认保存这章细纲”。
12. 进入“④ 章节写作”。
13. 选择章节，点击“根据细纲生成章节主稿”。
14. 人工修改后，点击“保存为正式章节”。

## 修改过 / 新增的文件

新增：

- `core/import_ops.py`
- `llm/import_prompts.py`
- `llm/schemas.py`
- `README_智能导入版.md`

修改：

- `app.py`
- `core/models.py`
- `llm/parsers.py`

