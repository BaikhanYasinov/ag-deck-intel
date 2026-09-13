"""Три реализации провайдера: Anthropic API, OpenAI-совместимый эндпойнт
(Ollama, vLLM, LM Studio) и transformers прямо в процессе.
"""
from __future__ import annotations

import asyncio

from ..config import (ANTHROPIC_API_KEY, LOCAL_API_KEY, LOCAL_BASE_URL,
                      LOCAL_CONCURRENCY, PRICING, TRANSFORMERS_DEVICE,
                      TRANSFORMERS_MODEL)
from .base import Completion
from .mock import MockProvider


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #
class AnthropicProvider:
    name = "anthropic"
    supports_multi_image = True

    def __init__(self) -> None:
        self._client = None

    def _c(self):
        if self._client is None:
            from anthropic import AsyncAnthropic
            if not ANTHROPIC_API_KEY:
                raise RuntimeError("ANTHROPIC_API_KEY не задан")
            self._client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        return self._client

    async def complete(self, model, system, blocks, schema, max_tokens) -> Completion:
        resp = await self._c().messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": blocks}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        pin, pout = PRICING.get(model, (3.0, 15.0))
        cost = (resp.usage.input_tokens * pin + resp.usage.output_tokens * pout) / 1e6
        return Completion(text, cost, resp.usage.input_tokens, resp.usage.output_tokens)


# --------------------------------------------------------------------------- #
# OpenAI-совместимый эндпойнт: Ollama, vLLM, LM Studio
# --------------------------------------------------------------------------- #
class OpenAICompatProvider:
    """Локальный инференс через HTTP. Стоимость всегда нулевая.

    Схема передаётся в response_format=json_schema. Если рантайм её не
    поддерживает, автоматически откатываемся на json_object: тогда валидность
    JSON гарантируется, а соответствие схеме проверяет Pydantic на нашей стороне.
    """
    name = "openai_compat"
    supports_multi_image = False   # зависит от модели, считаем худший случай

    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(LOCAL_CONCURRENCY)
        self._json_schema_ok = True

    @staticmethod
    def _to_openai(blocks: list[dict]) -> list[dict]:
        out = []
        for b in blocks:
            if b["type"] == "text":
                out.append({"type": "text", "text": b["text"]})
            elif b["type"] == "image":
                src = b["source"]
                out.append({"type": "image_url", "image_url": {
                    "url": f"data:{src['media_type']};base64,{src['data']}"}})
        return out

    @staticmethod
    async def _models(client) -> list[str] | None:
        """Список моделей рантайма. None — эндпойнта нет вовсе."""
        try:
            r = await client.get(f"{LOCAL_BASE_URL}/models",
                                 headers={"Authorization": f"Bearer {LOCAL_API_KEY}"})
            if r.status_code != 200:
                return None
            return [m["id"] for m in r.json().get("data", [])]
        except Exception:
            return None

    async def available_models(self) -> list[str] | None:
        import httpx
        async with httpx.AsyncClient(timeout=20) as c:
            return await self._models(c)

    @staticmethod
    def _server_message(resp) -> str:
        """Ollama и vLLM кладут причину в тело ответа. Без неё 400 бесполезен."""
        try:
            body = resp.json()
        except Exception:
            return (resp.text or "")[:400]
        err = body.get("error", body)
        if isinstance(err, dict):
            return str(err.get("message") or err)[:400]
        return str(err)[:400]

    def _format_variants(self, schema: dict | None) -> list[dict | None]:
        """Каскад отката. Не все рантаймы и не все модели понимают строгую
        схему; если не понимают, лучше получить свободный JSON и провалидировать
        его самим, чем упасть с 400."""
        if not schema:
            return [None]
        variants: list[dict | None] = []
        if self._json_schema_ok:
            variants.append({"type": "json_schema", "json_schema": {
                "name": "result", "strict": True, "schema": schema}})
        variants.append({"type": "json_object"})
        variants.append(None)
        return variants

    async def complete(self, model, system, blocks, schema, max_tokens) -> Completion:
        import httpx

        base = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": self._to_openai(blocks)}],
            "max_tokens": max_tokens,
            "temperature": 0,          # детерминизм важнее разнообразия
        }

        last_message = ""
        async with self._sem:
            async with httpx.AsyncClient(timeout=600) as c:
                for i, fmt in enumerate(self._format_variants(schema)):
                    payload = dict(base)
                    if fmt:
                        payload["response_format"] = fmt
                    r = await c.post(f"{LOCAL_BASE_URL}/chat/completions", json=payload,
                                     headers={"Authorization": f"Bearer {LOCAL_API_KEY}"})

                    if r.status_code == 200:
                        data = r.json()
                        usage = data.get("usage") or {}
                        return Completion(
                            data["choices"][0]["message"]["content"], 0.0,
                            usage.get("prompt_tokens", 0),
                            usage.get("completion_tokens", 0))

                    last_message = self._server_message(r)

                    if r.status_code == 404:
                        available = await self._models(c)
                        if available is None:
                            raise RuntimeError(
                                f"{LOCAL_BASE_URL} не отвечает на /models. Проверьте, "
                                f"запущен ли рантайм и поддерживает ли он "
                                f"OpenAI-совместимое API")
                        raise RuntimeError(
                            f"Модель «{model}» не найдена. Скачайте её: "
                            f"ollama pull {model}. Доступны: "
                            f"{', '.join(available) if available else 'ни одной'}")

                    if r.status_code == 400 and i == 0 and schema:
                        # строгая схема не принята — дальше пробуем мягче
                        self._json_schema_ok = False
                        continue
                    if r.status_code == 400:
                        continue

                    raise RuntimeError(
                        f"{model}: HTTP {r.status_code} от {LOCAL_BASE_URL}. "
                        f"{last_message}")

        images = sum(1 for b in blocks if b["type"] == "image")
        raise RuntimeError(
            f"{model}: рантайм отклонил запрос (400). Ответ сервера: {last_message or 'пусто'}. "
            f"В запросе было изображений: {images}. Частые причины — модель не "
            f"принимает изображения, либо запрос не помещается в контекст "
            f"(увеличьте num_ctx), либо рантайм не поддерживает переданный "
            f"формат ответа.")


# --------------------------------------------------------------------------- #
# transformers в процессе
# --------------------------------------------------------------------------- #
class TransformersProvider:
    """Запасной путь для архитектур, которых ещё нет в llama.cpp.

    Модель грузится один раз и живёт в памяти. Инференс синхронный и тяжёлый,
    поэтому он вынесен в отдельный поток и защищён семафором на одну задачу:
    иначе он заблокирует event loop и уронит SSE-прогресс.
    """
    name = "transformers"
    supports_multi_image = True

    def __init__(self) -> None:
        self._model = None
        self._processor = None
        self._sem = asyncio.Semaphore(1)

    _INSTALL_HINT = (
        "Провайдер transformers требует torch, torchvision и accelerate.\n"
        "  pip install torch torchvision --index-url "
        "https://download.pytorch.org/whl/cpu\n"
        "  pip install \"transformers>=4.57\" accelerate\n"
        "Для видеокарты NVIDIA выберите индекс под свою версию CUDA на "
        "pytorch.org.\n"
        "Если локальная модель уже есть в Ollama, дешевле использовать её: "
        "LLM_PROVIDER=ollama."
    )

    def _load(self, model_id: str):
        if self._model is None:
            try:
                import torch
                import torchvision  # noqa: F401  процессоры VL тянут его неявно
                from transformers import AutoModelForImageTextToText, AutoProcessor
            except ImportError as e:
                raise RuntimeError(f"{e}\n\n{self._INSTALL_HINT}") from e

            try:
                self._processor = AutoProcessor.from_pretrained(model_id)
                self._model = AutoModelForImageTextToText.from_pretrained(
                    model_id, torch_dtype=torch.bfloat16,
                    device_map=TRANSFORMERS_DEVICE)
            except ImportError as e:
                # процессоры Qwen-VL подгружают видеочасть, а она зависит
                # от torchvision: сообщение библиотеки об этом невнятное
                raise RuntimeError(f"{e}\n\n{self._INSTALL_HINT}") from e
        return self._model, self._processor

    @staticmethod
    def _to_hf(blocks: list[dict]) -> tuple[list[dict], list]:
        import base64
        import io

        from PIL import Image
        content, images = [], []
        for b in blocks:
            if b["type"] == "text":
                content.append({"type": "text", "text": b["text"]})
            else:
                img = Image.open(io.BytesIO(base64.b64decode(b["source"]["data"])))
                images.append(img.convert("RGB"))
                content.append({"type": "image"})
        return content, images

    def _run(self, model_id: str, system: str, blocks: list[dict],
             max_tokens: int) -> str:
        model, processor = self._load(model_id)
        content, images = self._to_hf(blocks)
        messages = [{"role": "system", "content": [{"type": "text", "text": system}]},
                    {"role": "user", "content": content}]
        prompt = processor.apply_chat_template(messages, add_generation_prompt=True,
                                               tokenize=False)
        inputs = processor(text=[prompt], images=images or None,
                           return_tensors="pt").to(model.device)
        out = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
        trimmed = out[0][inputs["input_ids"].shape[1]:]
        return processor.decode(trimmed, skip_special_tokens=True)

    async def complete(self, model, system, blocks, schema, max_tokens) -> Completion:
        model_id = model or TRANSFORMERS_MODEL
        async with self._sem:
            text = await asyncio.to_thread(self._run, model_id, system, blocks, max_tokens)
        return Completion(text, 0.0)


_REGISTRY: dict[str, object] = {}


def get_provider(name: str):
    name = {"ollama": "openai_compat", "vllm": "openai_compat",
            "lmstudio": "openai_compat"}.get(name, name)
    if name not in _REGISTRY:
        _REGISTRY[name] = {
            "anthropic": AnthropicProvider,
            "openai_compat": OpenAICompatProvider,
            "transformers": TransformersProvider,
            "mock": MockProvider,
        }[name]()
    return _REGISTRY[name]


def provider_names() -> list[str]:
    return ["anthropic", "openai_compat", "transformers", "mock"]
