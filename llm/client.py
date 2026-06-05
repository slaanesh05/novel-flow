from openai import OpenAI


def call_llm(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    system_prompt: str,
    temperature: float = 0.65,
    max_tokens: int = 4000,
) -> str:
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=120.0,
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )

    return response.choices[0].message.content or ""
