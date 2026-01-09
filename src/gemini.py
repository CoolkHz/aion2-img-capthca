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
        self._client: genai.Client | None = None
        self._client_api_key: str | None = None

        self._cache: dict[str, tuple[str, str]] = {}
        self._pending: dict[str, asyncio.Task] = {}
        self._errors: dict[str, str] = {}

        self._lock = asyncio.Lock()
        self._task_ttl_seconds = task_ttl_seconds
        self._max_entries = max_entries
        self._call_timeout_seconds = call_timeout_seconds
        self._completed_at: dict[str, float] = {}

    def _get_client(self) -> genai.Client:
        cfg = get_gemini_config()
        if self._client is None or self._client_api_key != cfg.api_key:
            self._client = genai.Client(api_key=cfg.api_key)
            self._client_api_key = cfg.api_key
        return self._client

    def _get_model(self) -> str:
        return get_gemini_config().model

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

    async def _call_gemini(self, img_data: bytes) -> tuple[str, str]:
        response = await asyncio.wait_for(
            self._get_client().aio.models.generate_content(
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

    async def _run_task(self, img_hash: str, img_data: bytes) -> None:
        try:
            result = await self._call_gemini(img_data)
            async with self._lock:
                self._cache[img_hash] = result
                self._errors.pop(img_hash, None)
                self._completed_at[img_hash] = time.time()
                self._cleanup_locked(self._completed_at[img_hash])
        except asyncio.TimeoutError:
            async with self._lock:
                self._errors[img_hash] = f"timeout after {self._call_timeout_seconds}s"
                self._completed_at[img_hash] = time.time()
                self._cleanup_locked(self._completed_at[img_hash])
        except Exception as e:
            async with self._lock:
                self._errors[img_hash] = str(e)
                self._completed_at[img_hash] = time.time()
                self._cleanup_locked(self._completed_at[img_hash])
        finally:
            async with self._lock:
                self._pending.pop(img_hash, None)

    async def submit(self, img_data: bytes, *, force_retry: bool = False) -> str:
        img_hash = image_hash(img_data)
        async with self._lock:
            now = time.time()
            self._cleanup_locked(now)

            if img_hash in self._cache:
                return img_hash
            if img_hash in self._pending:
                return img_hash
            if img_hash in self._errors and not force_retry:
                return img_hash

            self._errors.pop(img_hash, None)
            task = asyncio.create_task(self._run_task(img_hash, img_data))
            self._pending[img_hash] = task
            return img_hash

    async def get_status(self, task_id: str) -> dict:
        async with self._lock:
            now = time.time()
            self._cleanup_locked(now)

            if task_id in self._cache:
                code, raw = self._cache[task_id]
                return {"status": "done", "code": code, "raw": raw}
            if task_id in self._pending:
                return {"status": "pending"}
            if task_id in self._errors:
                return {"status": "error", "error": self._errors[task_id]}
            return {"status": "not_found"}

    async def classify(self, img_data: bytes) -> tuple[str, str]:
        task_id = image_hash(img_data)

        async with self._lock:
            now = time.time()
            self._cleanup_locked(now)

            if task_id in self._cache:
                return self._cache[task_id]
            if task_id in self._errors:
                raise HTTPException(status_code=500, detail=self._errors[task_id])

            task = self._pending.get(task_id)
            if task is None:
                task = asyncio.create_task(self._run_task(task_id, img_data))
                self._pending[task_id] = task

        await task

        async with self._lock:
            if task_id in self._cache:
                return self._cache[task_id]
            if task_id in self._errors:
                raise HTTPException(status_code=500, detail=self._errors[task_id])

        raise HTTPException(status_code=500, detail="Gemini task finished without result")


def extract_captcha(text: str) -> str:
    """正则提取五个连续的英文和数字组合"""
    match = re.search(r'[A-Za-z0-9]{5}', text)
    return match.group() if match else text.strip()


def image_hash(img_data: bytes) -> str:
    return hashlib.md5(img_data).hexdigest()


async def gemini_submit(img_data: bytes, *, force_retry: bool = False) -> str:
    """提交后台识别任务，立即返回 task_id（图片 hash）。

    - 若结果已在缓存中：直接返回 task_id
    - 若任务进行中：直接返回 task_id
    - 若任务失败：默认返回 task_id（不自动重试），force_retry=True 才会重试
    """
    return await service.submit(img_data, force_retry=force_retry)


async def gemini_get_status(task_id: str) -> dict:
    """按 task_id 查询状态。返回结构：
    - done:  {"status": "done", "code": str, "raw": str}
    - pending: {"status": "pending"}
    - error: {"status": "error", "error": str}
    - not_found: {"status": "not_found"}
    """
    return await service.get_status(task_id)


async def gemini_classify(img_data: bytes) -> tuple[str, str]:
    """Gemini 异步识别（等待结果），带内存缓存。"""
    return await service.classify(img_data)


service = GeminiOcrService()
