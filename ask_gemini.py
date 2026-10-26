import sys
import os
from openai import OpenAI

api_key = os.getenv("OPENROUTER_API_KEY")
if not api_key:
    print("Ошибка: OPENROUTER_API_KEY не задан в переменных окружения.")
    sys.exit(1)

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
)

query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
if not query:
    print("Использование: python ask_gemini.py 'твой вопрос или задача'")
    sys.exit(1)

system_instruction = (
    "Ты — главный системный архитектор и старший ревьюер проекта. "
    "Твой партнер — агент Claude Code, который пишет код и выполняет команды в терминале. "
    "Отвечай емко, строго по делу: предлагай архитектуру, логику БД, находи граничные случаи "
    "и декомпозируй сложные задачи на четкие технические шаги без лишней воды."
)

models_to_try = [
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.3-70b-instruct:free"
]

for model_name in models_to_try:
    try:
        response = client.chat.completions.create(
            model=model_name,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": query}
            ]
        )
        print(f"[model: {response.model or model_name}]")
        print(response.choices[0].message.content)
        if response.choices[0].finish_reason == "length":
            print("\n[ответ обрезан по max_tokens]")
        break
    except Exception as e:
        if model_name == models_to_try[-1]:
            print(f"Ошибка вызова: {e}")
        else:
            print(f"[{model_name} недоступна: {e}, пробую следующую]", file=sys.stderr)
