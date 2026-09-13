"""Диагностика ключа и связи с моделью. Запуск: python check_key.py"""
import asyncio
import os

from app.config import key_diagnostics, role_model
from app.llm.blocks import text_block
from app.llm.providers import get_provider


async def main() -> None:
    diag = key_diagnostics()
    print("Файл .env:          ", diag["env_file"],
          "(найден)" if diag["env_file_exists"] else "(НЕ НАЙДЕН)")
    print("Ключ из конфигурации:", diag["key_prefix"] or "отсутствует",
          f"длина {diag['key_length']}")

    shell_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if shell_key and shell_key.strip() != diag["key_prefix"].rstrip("…"):
        print("Внимание: ANTHROPIC_API_KEY задан и в окружении оболочки. "
              "Он имеет приоритет при override=False.")

    for p in diag["problems"]:
        print("  проблема:", p)
    if diag["problems"]:
        return

    provider_name, model = role_model("classifier")
    print(f"Пробный вызов: {provider_name}:{model}")
    try:
        res = await get_provider(provider_name).complete(
            model, "Ответь одним словом.", [text_block("ping")], None, 8)
        print("Связь есть. Ответ:", res.text.strip()[:40])
    except Exception as e:
        print("Вызов не прошёл:", str(e)[:300])


if __name__ == "__main__":
    asyncio.run(main())
