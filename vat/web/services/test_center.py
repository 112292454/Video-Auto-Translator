"""Web 测试中心服务。"""

from __future__ import annotations

import copy
import json
import math
import os
import shutil
import shlex
import struct
import subprocess
import tempfile
import threading
import time
import traceback
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import yaml

from vat.asr.split import build_split_llm_request
from vat.asr.model_source import (
    can_access_huggingface,
    has_local_whisper_cache,
    resolve_faster_whisper_repo_id,
)
from vat.asr.whisper_wrapper import WhisperASR
from vat.config import Config, build_starter_config, load_config
from vat.llm.client import call_llm, reset_llm_runtime_state
from vat.llm.prompts import reload_cache as reload_prompt_cache
from vat.llm.scene_identifier import SceneIdentifier
from vat.llm.video_info_translator import VideoInfoTranslator
from vat.media import extract_audio_ffmpeg, probe_media_info
from vat.translator.llm_translator import LLMTranslator
from vat.translator.types import TargetLanguage
from vat.uploaders.upload_config import DEFAULT_CONFIG_PATH as DEFAULT_UPLOAD_CONFIG_PATH, UploadConfig
from vat.utils.gpu import get_available_gpus, get_gpu_info, is_cuda_available
from vat.web.deps import get_db, get_web_config, get_web_config_path, set_web_config_path
from vat.utils.logger import setup_logger

logger = setup_logger("web.test_center")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PROJECT_DEFAULT_CONFIG = _PROJECT_ROOT / "config" / "default.yaml"

_DEFAULT_PREVIEW_INPUTS: Dict[str, Dict[str, Any]] = {
    "split": {
        "text": "今日はみんなで雑談するよ。最近あった面白いことをゆっくり話していこう。",
        "scene_prompt": "当前场景为杂谈，尽量保留自然口语节奏。",
    },
    "translate": {
        "segments": [
            "今日はみんな来てくれてありがとう",
            "次は新曲を歌います",
        ],
        "previous_context": {
            "1": "谢谢大家今天来看直播",
            "2": "接下来想和大家慢慢聊天",
        },
    },
    "optimize": {
        "subtitle_chunk": {
            "1": "こんフブキー！",
            "2": "今日はまったり雑談です",
        },
    },
    "scene_identify": {
        "title": "【Minecraft】建造天空城堡！",
        "description": "今天继续在我的世界里盖房子。",
    },
    "video_info_translate": {
        "title": "【初配信】みんなとおしゃべり",
        "description": "今日は初めての配信です。よろしくお願いします！",
        "tags": ["雑談", "初配信"],
        "uploader": "白上フブキ",
        "default_tid": 21,
    },
}

_UPLOAD_REFERENCE_DESCRIPTIONS = {
    "bilibili.copyright": "投稿版权类型，1=自制，2=转载。",
    "bilibili.default_tid": "默认分区 ID，上传时作为推荐分区的回退值。",
    "bilibili.default_tags": "默认标签列表，会与翻译/生成标签一起参与上传。",
    "bilibili.auto_cover": "是否自动使用视频封面。",
    "bilibili.cover_source": "自动封面的来源，通常为 thumbnail。",
    "bilibili.season_id": "默认加入的 B 站合集 ID，null 表示不自动加入。",
    "bilibili.templates.title": "投稿标题模板，支持 ${变量名} 形式的模板变量。",
    "bilibili.templates.description": "投稿简介模板，支持多行文本和模板变量。",
    "bilibili.templates.custom_vars": "自定义模板变量字典，可在 title/description 中引用。",
}


def _resolve_active_config_path() -> Path:
    explicit_path = get_web_config_path()
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()

    cwd_config = Path("config/config.yaml")
    if cwd_config.exists():
        return cwd_config.resolve()

    cwd_default = Path("config/default.yaml")
    if cwd_default.exists():
        return cwd_default.resolve()

    if _PROJECT_DEFAULT_CONFIG.exists():
        return _PROJECT_DEFAULT_CONFIG.resolve()

    raise RuntimeError("未找到可编辑的配置文件")


def _resolve_main_config_io_paths() -> Dict[str, Path]:
    source_path = _resolve_active_config_path()
    save_path = source_path
    if source_path.name == "default.yaml":
        save_path = source_path.with_name("config.yaml")
    return {"source_path": source_path, "save_path": save_path}


def _resolve_upload_config_path() -> Path:
    return DEFAULT_UPLOAD_CONFIG_PATH.expanduser().resolve()


def _load_config_dict_from_text(text: str) -> Dict[str, Any]:
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, dict):
        raise ValueError("配置文件根节点必须是 YAML 字典对象")
    return parsed


def _load_upload_config_dict_from_text(text: str) -> Dict[str, Any]:
    parsed = yaml.safe_load(text)
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError("Upload 配置文件根节点必须是 YAML 字典对象")
    return parsed


def _backup_dir_for(config_path: Path) -> Path:
    return config_path.parent / ".web_backups" / config_path.name


def _list_backups(config_path: Path) -> List[Dict[str, Any]]:
    backup_dir = _backup_dir_for(config_path)
    if not backup_dir.exists():
        return []
    backups = []
    for path in sorted(backup_dir.glob("*.bak"), reverse=True):
        stat = path.stat()
        backups.append(
            {
                "name": path.name,
                "path": str(path),
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
            }
        )
    return backups


def _create_backup(config_path: Path) -> Optional[Dict[str, Any]]:
    if not config_path.exists():
        return None
    backup_dir = _backup_dir_for(config_path)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{config_path.name}.{timestamp}.bak"
    shutil.copy2(config_path, backup_path)
    return {
        "name": backup_path.name,
        "path": str(backup_path),
        "size": backup_path.stat().st_size,
    }


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def _reload_web_runtime(config_path: Path) -> None:
    set_web_config_path(str(config_path))
    reset_llm_runtime_state()
    reload_prompt_cache()
    load_config(str(config_path))


def _mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:3]}***{secret[-2:]}"


def _infer_type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    if value is None:
        return "null"
    return "str"


def _flatten_dict(data: Dict[str, Any], prefix: str = "") -> Iterable[Dict[str, Any]]:
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        item = {"path": path, "type": _infer_type_name(value), "example": value}
        yield item
        if isinstance(value, dict):
            yield from _flatten_dict(value, path)


def _parse_yaml_comments(reference_path: Path) -> Dict[str, str]:
    comments: Dict[str, str] = {}
    pending: List[str] = []
    stack: List[tuple[int, str]] = []
    for raw_line in reference_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            pending = []
            continue
        if stripped.startswith("#"):
            pending.append(stripped.lstrip("#").strip())
            continue
        if stripped.startswith("- "):
            pending = []
            continue
        if ":" not in stripped:
            pending = []
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        key = stripped.split(":", 1)[0].strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        full_path = ".".join(name for _, name in stack)
        inline_comment = ""
        if " #" in raw_line:
            inline_comment = raw_line.split(" #", 1)[1].strip()
        desc_parts = [part for part in pending if part]
        if inline_comment:
            desc_parts.append(inline_comment)
        if desc_parts:
            comments[full_path] = " ".join(desc_parts)
        pending = []
    return comments


def _build_reference_items(current_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    starter = build_starter_config()
    comments = _parse_yaml_comments(_PROJECT_DEFAULT_CONFIG) if _PROJECT_DEFAULT_CONFIG.exists() else {}
    current_map = {item["path"]: item["example"] for item in _flatten_dict(current_config)}

    reference_items: List[Dict[str, Any]] = []
    for item in _flatten_dict(starter):
        path = item["path"]
        reference_items.append(
            {
                "path": path,
                "type": item["type"],
                "example": item["example"],
                "current_value": current_map.get(path, item["example"]),
                "source": "显式配置" if path in current_map else "默认/继承",
                "description": comments.get(path, ""),
            }
        )
    return reference_items


def _apply_reference_descriptions(
    reference_items: List[Dict[str, Any]],
    description_map: Dict[str, str],
) -> List[Dict[str, Any]]:
    for item in reference_items:
        description = description_map.get(item["path"])
        if description:
            item["description"] = description
    return reference_items


def get_main_config_editor_state() -> Dict[str, Any]:
    io_paths = _resolve_main_config_io_paths()
    source_path = io_paths["source_path"]
    save_path = io_paths["save_path"]
    yaml_text = source_path.read_text(encoding="utf-8")
    current_config = _load_config_dict_from_text(yaml_text)
    return {
        "path": str(source_path),
        "source_path": str(source_path),
        "save_path": str(save_path),
        "yaml_text": yaml_text,
        "structured_data": current_config,
        "reference_items": _build_reference_items(current_config),
        "backups": _list_backups(save_path),
        "notes": [
            f"当前读取: {source_path}",
            f"保存目标: {save_path}",
            "结构化编辑会校验 YAML 和 Config 契约；如果当前来源是 default.yaml，会保存到 config.yaml 作为优先级更高的覆盖文件。",
        ],
    }


def get_upload_config_editor_state() -> Dict[str, Any]:
    config_path = _resolve_upload_config_path()
    if config_path.exists():
        yaml_text = config_path.read_text(encoding="utf-8")
        current_config = _load_upload_config_dict_from_text(yaml_text)
    else:
        current_config = UploadConfig().to_dict()
        yaml_text = yaml.dump(current_config, allow_unicode=True, default_flow_style=False, sort_keys=False)

    reference_items = _apply_reference_descriptions(
        list(_flatten_dict(UploadConfig().to_dict())),
        _UPLOAD_REFERENCE_DESCRIPTIONS,
    )
    current_map = {item["path"]: item["example"] for item in _flatten_dict(current_config)}
    for item in reference_items:
        item["current_value"] = current_map.get(item["path"], item["example"])
        item["source"] = "显式配置" if item["path"] in current_map else "默认/继承"

    return {
        "path": str(config_path),
        "yaml_text": yaml_text,
        "structured_data": current_config,
        "reference_items": reference_items,
        "backups": _list_backups(config_path),
        "notes": [
            "这里编辑的是 config/upload.yaml，对应 B站投稿模板和默认上传参数。",
            "保存前会自动备份；保存后不会重排字段，只直接写入你编辑后的 YAML 文本。",
            "B站设置页读取的也是这份文件，因此这里保存后那边会同步生效。",
        ],
    }


def save_main_config_text(text: str) -> Dict[str, Any]:
    io_paths = _resolve_main_config_io_paths()
    source_path = io_paths["source_path"]
    config_path = io_paths["save_path"]
    parsed = _load_config_dict_from_text(text)
    Config.from_dict(copy.deepcopy(parsed))
    backup = _create_backup(config_path) if config_path.exists() else None
    _atomic_write_text(config_path, text)
    _reload_web_runtime(config_path)
    return {
        "path": str(config_path),
        "source_path": str(source_path),
        "save_path": str(config_path),
        "backup": backup,
        "reloaded": True,
        "backups": _list_backups(config_path),
    }


def save_upload_config_text(text: str) -> Dict[str, Any]:
    config_path = _resolve_upload_config_path()
    parsed = _load_upload_config_dict_from_text(text)
    UploadConfig.from_dict(copy.deepcopy(parsed))
    backup = _create_backup(config_path) if config_path.exists() else None
    _atomic_write_text(config_path, text)
    return {
        "path": str(config_path),
        "backup": backup,
        "reloaded": True,
        "backups": _list_backups(config_path),
    }


def _set_nested_value(data: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor: Any = data
    for key in parts[:-1]:
        if key not in cursor or not isinstance(cursor[key], dict):
            cursor[key] = {}
        cursor = cursor[key]
    cursor[parts[-1]] = value


def save_main_config_form_data(updates: Dict[str, Any]) -> Dict[str, Any]:
    io_paths = _resolve_main_config_io_paths()
    source_path = io_paths["source_path"]
    config_path = io_paths["save_path"]
    if config_path.exists():
        current = _load_config_dict_from_text(config_path.read_text(encoding="utf-8"))
    else:
        current = _load_config_dict_from_text(source_path.read_text(encoding="utf-8"))
    updated = copy.deepcopy(current)
    for path, value in updates.items():
        _set_nested_value(updated, path, value)
    yaml_text = yaml.dump(updated, allow_unicode=True, default_flow_style=False, sort_keys=False)
    result = save_main_config_text(yaml_text)
    result["applied_updates"] = updates
    return result


def save_upload_config_form_data(updates: Dict[str, Any]) -> Dict[str, Any]:
    config_path = _resolve_upload_config_path()
    if config_path.exists():
        current = _load_upload_config_dict_from_text(config_path.read_text(encoding="utf-8"))
    else:
        current = UploadConfig().to_dict()
    updated = copy.deepcopy(current)
    for path, value in updates.items():
        _set_nested_value(updated, path, value)
    UploadConfig.from_dict(copy.deepcopy(updated))
    yaml_text = yaml.dump(updated, allow_unicode=True, default_flow_style=False, sort_keys=False)
    result = save_upload_config_text(yaml_text)
    result["applied_updates"] = updates
    return result


def restore_main_config_backup(backup_name: str) -> Dict[str, Any]:
    config_path = _resolve_main_config_io_paths()["save_path"]
    backup_path = _backup_dir_for(config_path) / backup_name
    if not backup_path.exists():
        raise FileNotFoundError(f"备份不存在: {backup_name}")
    backup_text = backup_path.read_text(encoding="utf-8")
    result = save_main_config_text(backup_text)
    result["restored_from"] = backup_name
    result["restored"] = True
    return result


def restore_upload_config_backup(backup_name: str) -> Dict[str, Any]:
    config_path = _resolve_upload_config_path()
    backup_path = _backup_dir_for(config_path) / backup_name
    if not backup_path.exists():
        raise FileNotFoundError(f"备份不存在: {backup_name}")
    backup_text = backup_path.read_text(encoding="utf-8")
    result = save_upload_config_text(backup_text)
    result["restored_from"] = backup_name
    result["restored"] = True
    return result


def _resolve_target_provider(base_url: str, config: Config) -> str:
    if base_url:
        return "openai_compatible"
    return config.llm.provider


def _build_stage_target(
    *,
    target_id: str,
    label: str,
    model: str,
    api_key: str,
    base_url: str,
    proxy: Optional[str],
    provider: str,
) -> Dict[str, Any]:
    return {
        "id": target_id,
        "label": label,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "proxy": proxy or "",
        "api_key_configured": bool(api_key),
        "api_key_masked": _mask_secret(api_key),
    }


def list_llm_targets() -> Dict[str, Any]:
    config = get_web_config()
    targets: List[Dict[str, Any]] = []

    global_provider = _resolve_target_provider(config.llm.base_url, config)
    targets.append(
        _build_stage_target(
            target_id="global",
            label="全局 LLM",
            model=config.llm.model,
            api_key=config.llm.api_key,
            base_url=config.llm.base_url,
            proxy=config.proxy.get_proxy() or "",
            provider=global_provider,
        )
    )

    split_creds = config.get_stage_llm_credentials("split")
    targets.append(
        _build_stage_target(
            target_id="split",
            label="Split 断句",
            model=split_creds["model"],
            api_key=split_creds["api_key"],
            base_url=split_creds["base_url"],
            proxy=config.get_stage_proxy("split"),
            provider=_resolve_target_provider(split_creds["base_url"], config),
        )
    )

    translate_creds = config.get_stage_llm_credentials("translate")
    targets.append(
        _build_stage_target(
            target_id="translate",
            label="Translate 翻译",
            model=translate_creds["model"],
            api_key=translate_creds["api_key"],
            base_url=translate_creds["base_url"],
            proxy=config.get_stage_proxy("translate"),
            provider=_resolve_target_provider(translate_creds["base_url"], config),
        )
    )

    optimize_creds = config.get_optimize_effective_config()
    targets.append(
        _build_stage_target(
            target_id="optimize",
            label="Optimize 优化",
            model=optimize_creds["model"],
            api_key=optimize_creds["api_key"],
            base_url=optimize_creds["base_url"],
            proxy=config.get_stage_proxy("optimize"),
            provider=_resolve_target_provider(optimize_creds["base_url"], config),
        )
    )

    scene_model = config.downloader.scene_identify.model or config.llm.model
    scene_api_key = config.downloader.scene_identify.api_key or config.llm.api_key
    scene_base_url = config.downloader.scene_identify.base_url or config.llm.base_url
    targets.append(
        _build_stage_target(
            target_id="scene_identify",
            label="Scene Identify 场景识别",
            model=scene_model,
            api_key=scene_api_key,
            base_url=scene_base_url,
            proxy=config.get_stage_proxy("scene_identify"),
            provider=_resolve_target_provider(scene_base_url, config),
        )
    )

    vit_model = config.downloader.video_info_translate.model or config.llm.model
    vit_api_key = config.downloader.video_info_translate.api_key or config.llm.api_key
    vit_base_url = config.downloader.video_info_translate.base_url or config.llm.base_url
    targets.append(
        _build_stage_target(
            target_id="video_info_translate",
            label="Video Info Translate 视频信息翻译",
            model=vit_model,
            api_key=vit_api_key,
            base_url=vit_base_url,
            proxy=config.get_stage_proxy("video_info_translate"),
            provider=_resolve_target_provider(vit_base_url, config),
        )
    )

    return {"targets": targets}


def _find_target(target_id: str) -> Dict[str, Any]:
    for target in list_llm_targets()["targets"]:
        if target["id"] == target_id:
            return target
    raise ValueError(f"未知的 LLM 测试目标: {target_id}")


def _build_llm_reproduction(target: Dict[str, Any], request_payload: Dict[str, Any]) -> Dict[str, Any]:
    endpoint = ""
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    body = {
        "model": request_payload["model"],
        "messages": request_payload["messages"],
        "temperature": request_payload.get("temperature", 0.0),
        "max_tokens": request_payload.get("max_tokens"),
    }
    body = {key: value for key, value in body.items() if value is not None}

    if target["provider"] == "openai_compatible":
        base_url = request_payload["base_url"].rstrip("/")
        endpoint = f"{base_url}/chat/completions"
        if request_payload.get("api_key"):
            headers["Authorization"] = f"Bearer {request_payload['api_key']}"
    else:
        endpoint = request_payload["base_url"] or "<需要按当前 provider 手动补 endpoint>"

    curl_parts = [
        "curl -X POST",
        shlex.quote(endpoint),
    ]
    for key, value in headers.items():
        curl_parts.append(f"-H {shlex.quote(f'{key}: {value}')}")
    curl_parts.append(f"--data-raw {shlex.quote(json.dumps(body, ensure_ascii=False))}")
    return {
        "endpoint": endpoint,
        "headers": headers,
        "body": body,
        "curl_command": " ".join(curl_parts),
    }


def run_llm_connectivity_test(target_id: str) -> Dict[str, Any]:
    target = _find_target(target_id)
    request_payload = {
        "messages": [
            {"role": "system", "content": "You are a connectivity test assistant."},
            {"role": "user", "content": "Reply with EXACTLY OK"},
        ],
        "model": target["model"],
        "temperature": 0.0,
        "api_key": "" if not target["api_key_configured"] else _get_target_secret(target_id, "api_key"),
        "base_url": target["base_url"],
        "proxy": target["proxy"],
        "max_tokens": 8,
    }
    start = time.perf_counter()
    try:
        response = call_llm(**request_payload)
        duration_ms = int((time.perf_counter() - start) * 1000)
        raw_output = response.choices[0].message.content if response and response.choices else ""
        return {
            "target_id": target_id,
            "target": target,
            "ok": bool(raw_output.strip()),
            "exact_ok": raw_output.strip() == "OK",
            "status": "success" if raw_output.strip() else "empty",
            "summary": "返回成功" if raw_output.strip() else "返回为空",
            "duration_ms": duration_ms,
            "request": _sanitize_request(request_payload),
            "reproduction": _build_llm_reproduction(target, request_payload),
            "response": {
                "text": raw_output.strip(),
                "raw_output": raw_output,
                "choice_count": len(getattr(response, "choices", []) or []),
            },
            "error": None,
            "traceback": "",
        }
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        return {
            "target_id": target_id,
            "target": target,
            "ok": False,
            "exact_ok": False,
            "status": "error",
            "summary": str(exc),
            "duration_ms": duration_ms,
            "request": _sanitize_request(request_payload),
            "reproduction": _build_llm_reproduction(target, request_payload),
            "response": {
                "text": "",
                "raw_output": "",
                "choice_count": 0,
            },
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
            "traceback": traceback.format_exc(),
        }


def run_all_llm_connectivity_tests() -> Dict[str, Any]:
    results = [run_llm_connectivity_test(target["id"]) for target in list_llm_targets()["targets"]]
    return {
        "results": results,
        "summary": {
            "total": len(results),
            "success": sum(1 for item in results if item.get("ok")),
            "failed": sum(1 for item in results if not item.get("ok")),
        },
    }


def _get_target_secret(target_id: str, secret_name: str) -> str:
    config = get_web_config()
    if target_id == "global":
        return getattr(config.llm, secret_name)
    if target_id == "split":
        return config.get_stage_llm_credentials("split")[secret_name]
    if target_id == "translate":
        return config.get_stage_llm_credentials("translate")[secret_name]
    if target_id == "optimize":
        return config.get_optimize_effective_config()[secret_name]
    if target_id == "scene_identify":
        override = getattr(config.downloader.scene_identify, secret_name)
        return override or getattr(config.llm, secret_name)
    if target_id == "video_info_translate":
        override = getattr(config.downloader.video_info_translate, secret_name)
        return override or getattr(config.llm, secret_name)
    raise ValueError(f"未知的 LLM 测试目标: {target_id}")


def _make_translator_for_preview(config: Config) -> LLMTranslator:
    optimize_cfg = config.get_optimize_effective_config()
    translate_creds = config.get_stage_llm_credentials("translate")
    return LLMTranslator(
        thread_num=config.translator.llm.thread_num,
        batch_num=config.translator.llm.batch_size,
        target_language=TargetLanguage.SIMPLIFIED_CHINESE,
        output_dir=tempfile.gettempdir(),
        model=translate_creds["model"],
        custom_translate_prompt=config.translator.llm.custom_prompt,
        is_reflect=config.translator.llm.enable_reflect,
        enable_optimize=config.translator.llm.optimize.enable,
        custom_optimize_prompt=config.translator.llm.optimize.custom_prompt,
        enable_context=config.translator.llm.enable_context,
        api_key=translate_creds["api_key"],
        base_url=translate_creds["base_url"],
        optimize_model=optimize_cfg["model"],
        optimize_api_key=optimize_cfg["api_key"],
        optimize_base_url=optimize_cfg["base_url"],
        proxy=config.get_stage_proxy("translate") or "",
        optimize_proxy=config.get_stage_proxy("optimize") or "",
        enable_fallback=config.translator.llm.enable_fallback,
    )


def _sanitize_request(request: Dict[str, Any]) -> Dict[str, Any]:
    safe_request = copy.deepcopy(request)
    safe_request["api_key_masked"] = _mask_secret(safe_request.get("api_key", ""))
    safe_request["api_key"] = "***" if safe_request.get("api_key") else ""
    return safe_request


def build_prompt_preview(stage: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = get_web_config()
    overrides = overrides or {}

    if stage == "split":
        default = _DEFAULT_PREVIEW_INPUTS["split"]
        effective_input = {
            "text": overrides.get("text", default["text"]),
            "scene_prompt": overrides.get("scene_prompt", default["scene_prompt"]),
        }
        split_creds = config.get_stage_llm_credentials("split")
        request = build_split_llm_request(
            text=effective_input["text"],
            model=split_creds["model"],
            max_word_count_cjk=config.asr.split.max_words_cjk,
            max_word_count_english=config.asr.split.max_words_english,
            min_word_count_cjk=config.asr.split.min_words_cjk,
            min_word_count_english=config.asr.split.min_words_english,
            recommend_word_count_cjk=config.asr.split.recommend_words_cjk,
            recommend_word_count_english=config.asr.split.recommend_words_english,
            scene_prompt=effective_input["scene_prompt"],
            mode=config.asr.split.mode,
            api_key=split_creds["api_key"],
            base_url=split_creds["base_url"],
            proxy=config.get_stage_proxy("split") or "",
        )
    elif stage == "translate":
        default = _DEFAULT_PREVIEW_INPUTS["translate"]
        effective_input = {
            "segments": overrides.get("segments", default["segments"]),
            "previous_context": overrides.get("previous_context", default["previous_context"]),
        }
        translator = _make_translator_for_preview(config)
        translator._previous_batch_result = effective_input["previous_context"]
        request = translator.build_translate_llm_request(
            [
                SimpleNamespace(index=1, original_text=segment)
                for segment in effective_input["segments"]
            ]
        )
    elif stage == "optimize":
        default = _DEFAULT_PREVIEW_INPUTS["optimize"]
        effective_input = {
            "subtitle_chunk": overrides.get("subtitle_chunk", default["subtitle_chunk"]),
        }
        translator = _make_translator_for_preview(config)
        request = translator.build_optimize_llm_request(
            effective_input["subtitle_chunk"]
        )
    elif stage == "scene_identify":
        default = _DEFAULT_PREVIEW_INPUTS["scene_identify"]
        effective_input = {
            "title": overrides.get("title", default["title"]),
            "description": overrides.get("description", default["description"]),
        }
        identifier = SceneIdentifier(
            model=config.downloader.scene_identify.model or config.llm.model,
            api_key=config.downloader.scene_identify.api_key or config.llm.api_key,
            base_url=config.downloader.scene_identify.base_url or config.llm.base_url,
            proxy=config.get_stage_proxy("scene_identify") or "",
        )
        request = identifier.build_detect_scene_llm_request(
            title=effective_input["title"],
            description=effective_input["description"],
        )
    elif stage == "video_info_translate":
        default = _DEFAULT_PREVIEW_INPUTS["video_info_translate"]
        effective_input = {
            "title": overrides.get("title", default["title"]),
            "description": overrides.get("description", default["description"]),
            "tags": overrides.get("tags", default["tags"]),
            "uploader": overrides.get("uploader", default["uploader"]),
            "default_tid": overrides.get("default_tid", default["default_tid"]),
        }
        translator = VideoInfoTranslator(
            model=config.downloader.video_info_translate.model or config.llm.model,
            api_key=config.downloader.video_info_translate.api_key or config.llm.api_key,
            base_url=config.downloader.video_info_translate.base_url or config.llm.base_url,
            proxy=config.get_stage_proxy("video_info_translate") or "",
        )
        request = translator.build_translate_llm_request(
            title=effective_input["title"],
            description=effective_input["description"],
            tags=effective_input["tags"],
            uploader=effective_input["uploader"],
            default_tid=effective_input["default_tid"],
        )
    else:
        raise ValueError(f"未知的 Prompt 预览阶段: {stage}")

    return {
        "stage": stage,
        "effective_input": effective_input,
        "request": _sanitize_request(request),
        "runtime": {
            "model": request.get("model", ""),
            "temperature": request.get("temperature"),
            "base_url": request.get("base_url", ""),
            "proxy": request.get("proxy", ""),
        },
    }


def _run_version_command(command: List[str]) -> Dict[str, Any]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        first_line = (result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else ""
        return {"available": True, "command": " ".join(command), "version": first_line}
    except FileNotFoundError:
        return {"available": False, "command": " ".join(command), "error": "命令不存在"}
    except subprocess.CalledProcessError as exc:
        return {"available": False, "command": " ".join(command), "error": exc.stderr or str(exc)}


def _ensure_sample_audio() -> Path:
    sample_dir = Path(tempfile.gettempdir()) / "vat_test_center_assets"
    sample_dir.mkdir(parents=True, exist_ok=True)
    audio_path = sample_dir / "sample_tone.wav"
    if audio_path.exists():
        return audio_path

    framerate = 16000
    duration_sec = 1.2
    frequency = 440.0
    amplitude = 12000
    with wave.open(str(audio_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(framerate)
        for i in range(int(framerate * duration_sec)):
            sample = int(amplitude * math.sin(2 * math.pi * frequency * i / framerate))
            wav_file.writeframes(struct.pack("<h", sample))
    return audio_path


def _ensure_sample_video() -> Optional[Path]:
    if not shutil.which("ffmpeg"):
        return None
    sample_dir = Path(tempfile.gettempdir()) / "vat_test_center_assets"
    sample_dir.mkdir(parents=True, exist_ok=True)
    video_path = sample_dir / "sample_clip.mp4"
    if video_path.exists():
        return video_path

    audio_path = _ensure_sample_audio()
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=640x360:d=1.2",
        "-i",
        str(audio_path),
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(video_path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return video_path


def run_ffmpeg_check() -> Dict[str, Any]:
    ffmpeg = _run_version_command(["ffmpeg", "-version"])
    ffprobe = _run_version_command(["ffprobe", "-version"])
    sample_video = None
    sample_info = None
    audio_extract = {"ok": False}

    if ffmpeg["available"]:
        sample_video = _ensure_sample_video()
    if sample_video and ffprobe["available"]:
        sample_info = probe_media_info(sample_video)
        sample_audio = sample_video.with_suffix(".extracted.wav")
        extract_audio_ffmpeg(sample_video, sample_audio)
        audio_extract = {"ok": True, "path": str(sample_audio), "size": sample_audio.stat().st_size}

    return {
        "ok": ffmpeg["available"] and ffprobe["available"],
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "hwaccels": _run_ffmpeg_list_command(["ffmpeg", "-hide_banner", "-hwaccels"]),
        "nvenc_encoders": _run_ffmpeg_list_command(["ffmpeg", "-hide_banner", "-encoders"]),
        "sample_video": str(sample_video) if sample_video else "",
        "sample_info": sample_info,
        "audio_extract": audio_extract,
    }


def _run_ffmpeg_list_command(command: List[str]) -> Dict[str, Any]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        if command[-1] == "-encoders":
            lines = [line for line in lines if "nvenc" in line.lower()]
        return {"ok": True, "command": " ".join(command), "lines": lines[:80]}
    except Exception as exc:
        return {"ok": False, "command": " ".join(command), "error": str(exc)}


def _is_oom_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "out of memory" in message or "oom" in message or "cuda error 2" in message


def _monitor_gpu_memory(gpu_id: Optional[int], stop_event: threading.Event, samples: List[int]) -> None:
    if gpu_id is None:
        return
    while not stop_event.is_set():
        try:
            info = get_gpu_info(gpu_id)
            if info is not None:
                samples.append(info.memory_used_mb)
        except Exception:
            pass
        stop_event.wait(0.2)


def _run_with_gpu_memory_monitor(gpu_id: Optional[int], func):
    baseline = None
    peak_used_mb = None
    stop_event = threading.Event()
    samples: List[int] = []
    if gpu_id is not None:
        try:
            info = get_gpu_info(gpu_id)
            if info is not None:
                baseline = info.memory_used_mb
        except Exception:
            baseline = None
        monitor = threading.Thread(
            target=_monitor_gpu_memory,
            args=(gpu_id, stop_event, samples),
            daemon=True,
        )
        monitor.start()
    else:
        monitor = None

    try:
        result = func()
        return result, {
            "baseline_used_mb": baseline,
            "peak_used_mb": max(samples) if samples else baseline,
            "delta_peak_mb": (max(samples) - baseline) if samples and baseline is not None else None,
        }
    finally:
        stop_event.set()
        if monitor is not None:
            monitor.join(timeout=1.0)


def _resolve_video_probe_path(video_id: str = "", path: str = "") -> Path:
    if path:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"文件不存在: {resolved}")
        return resolved
    if not video_id:
        raise ValueError("必须提供 video_id 或 path")

    config = get_web_config()
    db = get_db()
    video = db.get_video(video_id)
    if not video:
        raise ValueError(f"视频不存在: {video_id}")
    video_dir = Path(config.storage.output_dir) / video.id
    candidates = [video_dir / "final.mp4"]
    candidates.extend(video_dir.glob("original.*"))
    candidates.extend(path for path in video_dir.iterdir() if path.is_file() and not path.name.startswith("translated."))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"未找到视频文件: {video_id}")


def run_video_probe(video_id: str = "", path: str = "") -> Dict[str, Any]:
    video_path = _resolve_video_probe_path(video_id=video_id, path=path)
    media_info = probe_media_info(video_path)
    return {
        "ok": media_info is not None,
        "path": str(video_path),
        "media_info": media_info,
    }


def run_whisper_check() -> Dict[str, Any]:
    config = get_web_config()
    sample_audio = _ensure_sample_audio()
    sample_video = _ensure_sample_video()

    gpu_error = ""
    gpus: List[Dict[str, Any]] = []
    try:
        gpus = [gpu.__dict__ for gpu in get_available_gpus()]
    except Exception as exc:
        gpu_error = str(exc)

    model_source = {
        "repo_id": resolve_faster_whisper_repo_id(config.asr.model),
        "download_root": config.get_whisper_models_dir(),
        "local_cache_available": has_local_whisper_cache(config.asr.model, config.get_whisper_models_dir()),
        "huggingface_accessible": can_access_huggingface(timeout_seconds=5.0),
        "hf_token_present": bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")),
    }

    runtime_config = {
        "model": config.asr.model,
        "language": config.asr.language,
        "device": config.asr.device,
        "compute_type": config.asr.compute_type,
        "vad_filter": config.asr.vad_filter,
        "beam_size": config.asr.beam_size,
        "enable_chunked": config.asr.enable_chunked,
        "chunk_length_sec": config.asr.chunk_length_sec,
        "chunk_overlap_sec": config.asr.chunk_overlap_sec,
        "chunk_concurrency": config.asr.chunk_concurrency,
    }

    whisper = WhisperASR(
        model_name=config.asr.model,
        device=config.asr.device,
        compute_type=config.asr.compute_type,
        language=config.asr.language,
        vad_filter=config.asr.vad_filter,
        beam_size=config.asr.beam_size,
        download_root=config.get_whisper_models_dir(),
        word_timestamps=config.asr.word_timestamps,
        condition_on_previous_text=config.asr.condition_on_previous_text,
        temperature=config.asr.temperature,
        compression_ratio_threshold=config.asr.compression_ratio_threshold,
        log_prob_threshold=config.asr.log_prob_threshold,
        no_speech_threshold=config.asr.no_speech_threshold,
        initial_prompt=config.asr.initial_prompt,
        repetition_penalty=config.asr.repetition_penalty,
        hallucination_silence_threshold=config.asr.hallucination_silence_threshold,
        vad_threshold=config.asr.vad_threshold,
        vad_min_speech_duration_ms=config.asr.vad_min_speech_duration_ms,
        vad_max_speech_duration_s=config.asr.vad_max_speech_duration_s,
        vad_min_silence_duration_ms=config.asr.vad_min_silence_duration_ms,
        vad_speech_pad_ms=config.asr.vad_speech_pad_ms,
        enable_chunked=config.asr.enable_chunked,
        chunk_length_sec=config.asr.chunk_length_sec,
        chunk_overlap_sec=config.asr.chunk_overlap_sec,
        chunk_concurrency=config.asr.chunk_concurrency,
        use_pipeline=config.asr.use_pipeline,
        enable_diarization=config.asr.enable_diarization,
        enable_punctuation=config.asr.enable_punctuation,
        pipeline_batch_size=config.asr.pipeline_batch_size,
        pipeline_chunk_length=config.asr.pipeline_chunk_length,
        num_speakers=config.asr.num_speakers,
        min_speakers=config.asr.min_speakers,
        max_speakers=config.asr.max_speakers,
    )

    load_start = time.perf_counter()
    try:
        _, load_memory = _run_with_gpu_memory_monitor(whisper.gpu_id, whisper._ensure_model_loaded)
        load_result = {
            "ok": True,
            "duration_ms": int((time.perf_counter() - load_start) * 1000),
            "model_loaded": whisper.model is not None,
            "model_class": whisper.model.__class__.__name__ if whisper.model is not None else "",
            "memory": load_memory,
        }
    except Exception as exc:
        return {
            "ok": False,
            "runtime_config": runtime_config,
            "model_source": model_source,
            "gpu": {
                "cuda_available": is_cuda_available(),
                "selected_device": whisper.device,
                "selected_gpu_id": whisper.gpu_id,
                "inventory": gpus,
                "detection_error": gpu_error,
            },
            "sample_audio": str(sample_audio),
            "sample_video": str(sample_video) if sample_video else "",
            "load": {
                "ok": False,
                "error": str(exc),
                "oom": _is_oom_error(exc),
                "duration_ms": int((time.perf_counter() - load_start) * 1000),
            },
            "transcription": {"ok": False, "error": "模型加载失败，未执行最小转录", "oom": False},
        }

    extraction_result = {"ok": False}
    if sample_video and Path(sample_video).exists():
        extracted_audio = Path(tempfile.gettempdir()) / "vat_test_center_assets" / "sample_clip.whisper.wav"
        try:
            extract_audio_ffmpeg(Path(sample_video), extracted_audio)
            extraction_result = {"ok": True, "path": str(extracted_audio), "size": extracted_audio.stat().st_size}
        except Exception as exc:
            extraction_result = {"ok": False, "error": str(exc)}

    start = time.perf_counter()
    try:
        result, transcription_memory = _run_with_gpu_memory_monitor(
            whisper.gpu_id,
            lambda: whisper.asr_audio(sample_audio, language=config.asr.language),
        )
        duration_ms = int((time.perf_counter() - start) * 1000)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        return {
            "ok": False,
            "runtime_config": runtime_config,
            "model_source": model_source,
            "gpu": {
                "cuda_available": is_cuda_available(),
                "selected_device": whisper.device,
                "selected_gpu_id": whisper.gpu_id,
                "inventory": gpus,
                "detection_error": gpu_error,
            },
            "sample_audio": str(sample_audio),
            "sample_video": str(sample_video) if sample_video else "",
            "sample_video_audio_extract": extraction_result,
            "load": load_result,
            "transcription": {
                "ok": False,
                "duration_ms": duration_ms,
                "error": str(exc),
                "oom": _is_oom_error(exc),
                "segments": 0,
                "preview": [],
            },
        }
    segment_preview = [
        {
            "text": segment.text,
            "start_time": segment.start_time,
            "end_time": segment.end_time,
        }
        for segment in result.segments[:5]
    ]
    return {
        "ok": True,
        "runtime_config": runtime_config,
        "model_source": model_source,
        "gpu": {
            "cuda_available": is_cuda_available(),
            "selected_device": whisper.device,
            "selected_gpu_id": whisper.gpu_id,
            "inventory": gpus,
            "detection_error": gpu_error,
        },
        "sample_audio": str(sample_audio),
        "sample_video": str(sample_video) if sample_video else "",
        "sample_video_audio_extract": extraction_result,
        "load": load_result,
        "transcription": {
            "ok": True,
            "duration_ms": duration_ms,
            "segments": len(result.segments),
            "preview": segment_preview,
            "oom": False,
            "memory": transcription_memory,
        },
    }
