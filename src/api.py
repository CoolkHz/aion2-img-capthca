import base64
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from src.auth import get_gemini_api_key, verify_api_secret
from src.gemini import gemini_classify, gemini_get_status, gemini_submit

router = APIRouter(dependencies=[Depends(verify_api_secret)])


class CaptchaRequest(BaseModel):
    image: str  # base64 编码的图片


class CaptchaResponse(BaseModel):
    code: str
    raw: str  # 原始返回


class CaptchaPollResponse(BaseModel):
    status: Literal["pending", "done", "error"]
    task_id: str
    code: Optional[str] = None
    raw: Optional[str] = None
    error: Optional[str] = None


@router.post("/ocr", response_model=CaptchaResponse)
async def ocr_base64(req: CaptchaRequest, gemini_api_key: str = Depends(get_gemini_api_key)):
    """Base64 图片识别（等待 Gemini 返回）"""
    try:
        img_data = base64.b64decode(req.image)
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 base64 图片")

    code, raw = await gemini_classify(img_data, gemini_api_key)
    return CaptchaResponse(code=code, raw=raw)


@router.post("/ocr/upload", response_model=CaptchaResponse)
async def ocr_upload(file: UploadFile = File(...), gemini_api_key: str = Depends(get_gemini_api_key)):
    """上传图片识别（等待 Gemini 返回）"""
    img_data = await file.read()
    code, raw = await gemini_classify(img_data, gemini_api_key)
    return CaptchaResponse(code=code, raw=raw)


@router.post("/ocr/poll", response_model=CaptchaPollResponse)
async def ocr_poll(
    req: CaptchaRequest,
    retry: bool = False,
    gemini_api_key: str = Depends(get_gemini_api_key),
):
    """轮询式识别：第一次触发后台任务；后续调用同一图片会在完成后直接返回结果。"""
    try:
        img_data = base64.b64decode(req.image)
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 base64 图片")

    task_id = await gemini_submit(img_data, gemini_api_key, force_retry=retry)
    status = await gemini_get_status(task_id, gemini_api_key)
    if status["status"] == "pending":
        return CaptchaPollResponse(status="pending", task_id=task_id)
    if status["status"] == "done":
        return CaptchaPollResponse(status="done", task_id=task_id, code=status["code"], raw=status["raw"])
    return CaptchaPollResponse(status="error", task_id=task_id, error=status.get("error") or "unknown error")


@router.post("/ocr/upload/poll", response_model=CaptchaPollResponse)
async def ocr_upload_poll(
    file: UploadFile = File(...),
    retry: bool = False,
    gemini_api_key: str = Depends(get_gemini_api_key),
):
    """上传图片的轮询式识别。"""
    img_data = await file.read()
    task_id = await gemini_submit(img_data, gemini_api_key, force_retry=retry)
    status = await gemini_get_status(task_id, gemini_api_key)
    if status["status"] == "pending":
        return CaptchaPollResponse(status="pending", task_id=task_id)
    if status["status"] == "done":
        return CaptchaPollResponse(status="done", task_id=task_id, code=status["code"], raw=status["raw"])
    return CaptchaPollResponse(status="error", task_id=task_id, error=status.get("error") or "unknown error")


@router.get("/ocr/task/{task_id}", response_model=CaptchaPollResponse)
async def ocr_task_status(task_id: str, gemini_api_key: str = Depends(get_gemini_api_key)):
    """按 task_id（图片 hash）查询任务状态。"""
    status = await gemini_get_status(task_id, gemini_api_key)
    if status["status"] == "pending":
        return CaptchaPollResponse(status="pending", task_id=task_id)
    if status["status"] == "done":
        return CaptchaPollResponse(status="done", task_id=task_id, code=status["code"], raw=status["raw"])
    if status["status"] == "error":
        return CaptchaPollResponse(status="error", task_id=task_id, error=status.get("error") or "unknown error")
    raise HTTPException(status_code=404, detail="task_id not found")
