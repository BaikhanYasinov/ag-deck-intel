"""Конфигурация. Все значения переопределяются через .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# override=True принципиально: без него переменная, уже присутствующая в
# окружении оболочки, молча перебивает .env, и вы отлаживаете ключ, которого
# в файле нет. Путь указан явно, чтобы запуск из любого каталога работал.
load_dotenv(BASE_DIR / ".env", override=True)
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
STORAGE_DIR = DATA_DIR / "storage"
DB_PATH = DATA_DIR / "app.db"

for _d in (DATA_DIR, STORAGE_DIR, STORAGE_DIR / "originals",
           STORAGE_DIR / "pdf", STORAGE_DIR / "pages", STORAGE_DIR / "reports"):
    _d.mkdir(parents=True, exist_ok=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip().strip('"').strip("'")
MODEL_FAST = os.getenv("MODEL_FAST", "claude-haiku-4-5-20251001")
MODEL_MAIN = os.getenv("MODEL_MAIN", "claude-sonnet-5")

FUND_NAME = os.getenv("FUND_NAME", "AG Ventures")
CHECK_MIN_USD = int(os.getenv("CHECK_MIN_USD", 10_000))
CHECK_TARGET_USD = int(os.getenv("CHECK_TARGET_USD", 25_000))
CHECK_MAX_USD = int(os.getenv("CHECK_MAX_USD", 50_000))
THESIS_STAGE = os.getenv("THESIS_STAGE", "pre-seed / seed")
THESIS_SECTOR = os.getenv("THESIS_SECTOR", "IT")
THESIS_GEO = os.getenv("THESIS_GEO", "Кыргызстан")

MAX_PAGES = int(os.getenv("MAX_PAGES", 100))
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", 50))
CONCURRENCY = int(os.getenv("CONCURRENCY", 4))
RENDER_DPI = int(os.getenv("RENDER_DPI", 150))
MAX_VISION_PAGES = int(os.getenv("MAX_VISION_PAGES", 30))

ENRICHMENT_ENABLED = os.getenv("ENRICHMENT_ENABLED", "false").lower() == "true"

# --------------------------------------------------------------------------- #
# Провайдеры моделей
# --------------------------------------------------------------------------- #
# anthropic          — облачный API, платный
# ollama / vllm      — OpenAI-совместимый локальный эндпойнт, бесплатный
# transformers       — модель в процессе приложения, бесплатный
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")

LOCAL_BASE_URL = os.getenv("LOCAL_BASE_URL", "http://localhost:11434/v1")
LOCAL_API_KEY = os.getenv("LOCAL_API_KEY", "ollama")
LOCAL_MODEL = os.getenv("LOCAL_MODEL", "qwen3-vl:8b")
LOCAL_MODEL_FAST = os.getenv("LOCAL_MODEL_FAST", LOCAL_MODEL)
# На одной видеокарте параллельные запросы только мешают друг другу.
LOCAL_CONCURRENCY = int(os.getenv("LOCAL_CONCURRENCY", 1))

TRANSFORMERS_MODEL = os.getenv("TRANSFORMERS_MODEL", "Qwen/Qwen3-VL-8B-Instruct")
TRANSFORMERS_DEVICE = os.getenv("TRANSFORMERS_DEVICE", "auto")

# batch — все изображения в одном запросе; per_page — по одной странице;
# auto — batch для облака, per_page для локальных рантаймов.
VISION_MODE = os.getenv("VISION_MODE", "auto")
MAX_IMAGES_PER_CALL = int(os.getenv("MAX_IMAGES_PER_CALL", 8))

# Роли вызовов. Любую можно перевести на свой провайдер переменной окружения
# MODEL_ROLE_<ROLE>, например MODEL_ROLE_CLASSIFIER=ollama:qwen3-vl:4b
ROLES = ["classifier", "structurizer", "onepager", "team",
         "problem_solution", "market", "product", "business_model",
         "traction", "ask_deal_fit"]

_FAST_ROLES = {"classifier"}


def _default_for(role: str) -> str:
    if LLM_PROVIDER == "mock":
        return "mock:demo"
    if LLM_PROVIDER == "anthropic":
        return f"anthropic:{MODEL_FAST if role in _FAST_ROLES else MODEL_MAIN}"
    if LLM_PROVIDER == "transformers":
        return f"transformers:{TRANSFORMERS_MODEL}"
    model = LOCAL_MODEL_FAST if role in _FAST_ROLES else LOCAL_MODEL
    return f"{LLM_PROVIDER}:{model}"


def role_model(role: str) -> tuple[str, str]:
    """Возвращает (провайдер, модель) для роли. Формат значения: provider:model.

    Двоеточие делится только по первому вхождению: имена моделей в Ollama
    сами содержат двоеточие, например qwen3-vl:8b.
    """
    raw = os.getenv(f"MODEL_ROLE_{role.upper()}") or _default_for(role)
    provider, _, model = raw.partition(":")
    return provider.strip(), model.strip()

ALLOWED_EXTENSIONS = {".pdf", ".pptx"}

# Ориентировочная стоимость, USD за миллион токенов. Нужна только для учёта.
PRICING = {
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus-5": (15.0, 75.0),
}


def thesis_summary() -> str:
    return (
        f"Фонд: {FUND_NAME}. Стадия: {THESIS_STAGE}. Сектор: {THESIS_SECTOR}. "
        f"География: {THESIS_GEO}. Размер чека: ${CHECK_MIN_USD:,}–${CHECK_MAX_USD:,}, "
        f"типичный ${CHECK_TARGET_USD:,}."
    ).replace(",", " ")


def current_bundle() -> str:
    """Чем сейчас считает приложение. Используется, чтобы не выдавать за
    свежий результат прогон, сделанный на другом провайдере."""
    return "{}:{}".format(*role_model("structurizer"))


def key_diagnostics() -> dict:
    """Что именно ушло бы в API. Сам ключ не раскрывается."""
    if LLM_PROVIDER in ("mock", "ollama", "vllm", "lmstudio", "transformers"):
        return {"env_file": str(BASE_DIR / ".env"),
                "env_file_exists": (BASE_DIR / ".env").exists(),
                "key_present": True, "key_prefix": "", "key_length": 0,
                "problems": [],
                "note": f"провайдер {LLM_PROVIDER}: ключ Anthropic не требуется"}
    key = ANTHROPIC_API_KEY
    env_file = BASE_DIR / ".env"
    problems = []
    if not key:
        problems.append("ключ пустой: .env не найден или переменная не задана")
    elif key.startswith("sk-ant-...") or key.endswith("..."):
        problems.append("в .env остался placeholder из .env.example")
    elif not key.startswith("sk-ant-"):
        problems.append("ключ не начинается с sk-ant-: похоже, это не ключ Anthropic API")
    elif len(key) < 50:
        problems.append("ключ подозрительно короткий: возможно, скопирован не целиком")
    if key != key.strip():
        problems.append("в ключе есть пробелы или перевод строки по краям")
    return {
        "env_file": str(env_file),
        "env_file_exists": env_file.exists(),
        "key_present": bool(key),
        "key_prefix": key[:14] + "…" if key else "",
        "key_length": len(key),
        "problems": problems,
    }
