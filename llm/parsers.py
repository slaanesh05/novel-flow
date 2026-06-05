import json
import re


def extract_json_text(text: str) -> str:
    text = (text or "").strip()

    if text.startswith("```"):
        text = re.sub(r"^```json", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"^```", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    return text


def parse_json_object(text: str) -> dict:
    text = extract_json_text(text)

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    data = json.loads(text)

    if not isinstance(data, dict):
        raise ValueError("模型输出不是 JSON 对象。")

    return data


def parse_json_array(text: str) -> list:
    text = extract_json_text(text)

    start = text.find("[")
    end = text.rfind("]")

    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    data = json.loads(text)

    if not isinstance(data, list):
        raise ValueError("模型输出不是 JSON 数组。")

    return data



def try_parse_json_object(text: str) -> tuple[dict | None, str | None]:
    try:
        return parse_json_object(text), None
    except Exception as e:
        return None, str(e)


def try_parse_json_array(text: str) -> tuple[list | None, str | None]:
    try:
        return parse_json_array(text), None
    except Exception as e:
        return None, str(e)
