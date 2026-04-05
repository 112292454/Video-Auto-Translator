"""Web 测试中心 API。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from vat.web.services import test_center as test_center_service

router = APIRouter(prefix="/api/test-center", tags=["test-center"])


class RunTargetRequest(BaseModel):
    target_id: str


class PromptPreviewRequest(BaseModel):
    stage: str
    overrides: Optional[Dict[str, Any]] = None


class SaveConfigRequest(BaseModel):
    content: str = Field(min_length=1)


class RestoreConfigRequest(BaseModel):
    backup_name: str


class SaveConfigFormRequest(BaseModel):
    updates: Dict[str, Any]


class VideoProbeRequest(BaseModel):
    video_id: str = ""
    path: str = ""


async def _run_safe_test(func, *args):
    try:
        return await run_in_threadpool(func, *args)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/llm/targets")
async def list_llm_targets():
    return await run_in_threadpool(test_center_service.list_llm_targets)


@router.post("/llm/run")
async def run_llm_target(body: RunTargetRequest):
    return await _run_safe_test(
        test_center_service.run_llm_connectivity_test,
        body.target_id,
    )


@router.post("/llm/run-all")
async def run_all_llm_targets():
    return await _run_safe_test(test_center_service.run_all_llm_connectivity_tests)


@router.post("/prompt-preview")
async def preview_prompt(body: PromptPreviewRequest):
    return await run_in_threadpool(
        test_center_service.build_prompt_preview,
        body.stage,
        body.overrides,
    )


@router.get("/config/main")
async def get_main_config():
    return await run_in_threadpool(test_center_service.get_main_config_editor_state)


@router.get("/config/upload")
async def get_upload_config():
    return await run_in_threadpool(test_center_service.get_upload_config_editor_state)


@router.post("/config/main")
async def save_main_config(body: SaveConfigRequest):
    return await run_in_threadpool(test_center_service.save_main_config_text, body.content)


@router.post("/config/main/form")
async def save_main_config_form(body: SaveConfigFormRequest):
    return await run_in_threadpool(test_center_service.save_main_config_form_data, body.updates)


@router.post("/config/upload")
async def save_upload_config(body: SaveConfigRequest):
    return await run_in_threadpool(test_center_service.save_upload_config_text, body.content)


@router.post("/config/upload/form")
async def save_upload_config_form(body: SaveConfigFormRequest):
    return await run_in_threadpool(test_center_service.save_upload_config_form_data, body.updates)


@router.post("/config/main/restore")
async def restore_main_config(body: RestoreConfigRequest):
    return await run_in_threadpool(
        test_center_service.restore_main_config_backup,
        body.backup_name,
    )


@router.post("/config/upload/restore")
async def restore_upload_config(body: RestoreConfigRequest):
    return await run_in_threadpool(
        test_center_service.restore_upload_config_backup,
        body.backup_name,
    )


@router.post("/environment/ffmpeg")
async def run_ffmpeg_check():
    return await _run_safe_test(test_center_service.run_ffmpeg_check)


@router.post("/environment/whisper")
async def run_whisper_check():
    return await _run_safe_test(test_center_service.run_whisper_check)


@router.post("/environment/video-probe")
async def run_video_probe(body: VideoProbeRequest):
    return await _run_safe_test(
        test_center_service.run_video_probe,
        body.video_id,
        body.path,
    )
