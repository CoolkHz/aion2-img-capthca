import re
import hashlib
import asyncio
import time
from google import genai
from google.genai import types
from fastapi import HTTPException

from src.settings import get_gemini_config

# 识别提示词
PROMPT = "直接返回我图中的五个由数字和英文组成的字符，背景可能有噪点或干扰线，请专注识别前景中的5个字母和数字字符，注意区分大小写。直接返回识别到的字符串结果（例如：aB3dE）"


class GeminiOcrService:
    def __init__(
        self,
        *,
        task_ttl_seconds: int = 10 * 60,
        max_entries: int = 200,
        call_timeout_seconds: float = 30.0,
    ) -> None:
        self._clients: dict[str, genai.Client] = {}

        self._cache: dict[str, tuple[str, str]] = {}
        self._pending: dict[str, asyncio.Task[None]] = {}
        self._errors: dict[str, str] = {}

        self._lock = asyncio.Lock()
        self._task_ttl_seconds = task_ttl_seconds
        self._max_entries = max_entries
        self._call_timeout_seconds = call_timeout_seconds
        self._completed_at: dict[str, float] = {}

    def _get_client(self, api_key: str) -> genai.Client:
        client = self._clients.get(api_key)
        if client is None:
            client = genai.Client(api_key=api_key)
            self._clients[api_key] = client
        return client

    def _get_model(self) -> str:
        return get_gemini_config().model

    def _task_key(self, api_key: str, img_hash: str) -> str:
        api_key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        return f"{api_key_hash}:{img_hash}"

    def _cleanup_locked(self, now: float) -> None:
        expired = [k for k, ts in self._completed_at.items() if now - ts > self._task_ttl_seconds]
        for k in expired:
            self._completed_at.pop(k, None)
            self._cache.pop(k, None)
            self._errors.pop(k, None)

        if self._max_entries > 0 and len(self._completed_at) > self._max_entries:
            to_remove = sorted(self._completed_at.items(), key=lambda kv: kv[1])[
                : len(self._completed_at) - self._max_entries
            ]
            for k, _ in to_remove:
                self._completed_at.pop(k, None)
                self._cache.pop(k, None)
                self._errors.pop(k, None)

    async def _call_gemini(self, img_data: bytes, api_key: str) -> tuple[str, str]:
        response = await asyncio.wait_for(
            self._get_client(api_key).aio.models.generate_content(
                model=self._get_model(),
                contents=[
                    types.Part.from_bytes(data=img_data, mime_type="image/png"),
                    PROMPT,
                ],
            ),
            timeout=self._call_timeout_seconds,
        )
        raw = response.text.strip()
        code = extract_captcha(raw)
        return code, raw

    async def _run_task(self, task_key: str, img_data: bytes, api_key: str) -> None:
        try:
            result = await self._call_gemini(img_data, api_key)
            async with self._lock:
                self._cache[task_key] = result
                self._errors.pop(task_key, None)
                self._completed_at[task_key] = time.time()
                self._cleanup_locked(self._completed_at[task_key])
        except asyncio.TimeoutError:
            async with self._lock:
                self._errors[task_key] = f"timeout after {self._call_timeout_seconds}s"
                self._completed_at[task_key] = time.time()
                self._cleanup_locked(self._completed_at[task_key])
        except Exception as e:
            async with self._lock:
                self._errors[task_key] = str(e)
                self._completed_at[task_key] = time.time()
                self._cleanup_locked(self._completed_at[task_key])
        finally:
            async with self._lock:
                self._pending.pop(task_key, None)

    async def submit(self, img_data: bytes, api_key: str, *, force_retry: bool = False) -> str:
        img_hash = image_hash(img_data)
        task_key = self._task_key(api_key, img_hash)
        async with self._lock:
            now = time.time()
            self._cleanup_locked(now)

            if task_key in self._cache:
                return img_hash
            if task_key in self._pending:
                return img_hash
            if task_key in self._errors and not force_retry:
                return img_hash

            self._errors.pop(task_key, None)
            task = asyncio.create_task(self._run_task(task_key, img_data, api_key))
            self._pending[task_key] = task
            return img_hash

    async def get_status(self, task_id: str, api_key: str) -> dict:
        task_key = self._task_key(api_key, task_id)
        async with self._lock:
            now = time.time()
            self._cleanup_locked(now)

            if task_key in self._cache:
                code, raw = self._cache[task_key]
                return {"status": "done", "code": code, "raw": raw}
            if task_key in self._pending:
                return {"status": "pending"}
            if task_key in self._errors:
                return {"status": "error", "error": self._errors[task_key]}
            return {"status": "not_found"}

    async def classify(self, img_data: bytes, api_key: str) -> tuple[str, str]:
        task_id = image_hash(img_data)
        task_key = self._task_key(api_key, task_id)

        async with self._lock:
            now = time.time()
            self._cleanup_locked(now)

            if task_key in self._cache:
                return self._cache[task_key]
            if task_key in self._errors:
                raise HTTPException(status_code=500, detail=self._errors[task_key])

            task = self._pending.get(task_key)
            if task is None:
                task = asyncio.create_task(self._run_task(task_key, img_data, api_key))
                self._pending[task_key] = task

        await task

        async with self._lock:
            if task_key in self._cache:
                return self._cache[task_key]
            if task_key in self._errors:
                raise HTTPException(status_code=500, detail=self._errors[task_key])

        raise HTTPException(status_code=500, detail="Gemini task finished without result")


def extract_captcha(text: str) -> str:
    """正则提取五个连续的英文和数字组合"""
    match = re.search(r'[A-Za-z0-9]{5}', text)
    return match.group() if match else text.strip()


def image_hash(img_data: bytes) -> str:
    return hashlib.md5(img_data).hexdigest()


async def gemini_submit(img_data: bytes, api_key: str, *, force_retry: bool = False) -> str:
    """提交后台识别任务，立即返回 task_id（图片 hash）。

    - 若结果已在缓存中：直接返回 task_id
    - 若任务进行中：直接返回 task_id
    - 若任务失败：默认返回 task_id（不自动重试），force_retry=True 才会重试
    """
    return await service.submit(img_data, api_key, force_retry=force_retry)


async def gemini_get_status(task_id: str, api_key: str) -> dict:
    """按 task_id 查询状态。返回结构：
    - done:  {"status": "done", "code": str, "raw": str}
    - pending: {"status": "pending"}
    - error: {"status": "error", "error": str}
    - not_found: {"status": "not_found"}
    """
    return await service.get_status(task_id, api_key)


async def gemini_classify(img_data: bytes, api_key: str) -> tuple[str, str]:
    """Gemini 异步识别（等待结果），带内存缓存。"""
    return await service.classify(img_data, api_key)


service = GeminiOcrService()
