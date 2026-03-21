"""
翻译模型质量对比测试脚本

用途：
  对已有视频的 split 结果（original.srt），用不同 LLM 模型跑 optimize + translate 流程，
  持久化保存结果并生成人类可读的对比表。

设计：
  - 不走 pipeline，直接调用 LLMTranslator
  - 不影响已有视频数据（输出到独立目录）
  - 支持多模型横向对比、增量添加新模型

用法：
  python scripts/translation_benchmark.py --video-id _QOMPli80JA --model MiniMax-M2.5 --sample 50
  python scripts/translation_benchmark.py --video-id _QOMPli80JA --model kimi-k2.5 --sample 50 --prompt-lang zh  # 中文提示词
  python scripts/translation_benchmark.py --video-id _QOMPli80JA --compare  # 生成对比表
"""

import argparse
import json
import os
import sys
import time
import logging
from pathlib import Path
from datetime import datetime

# 项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from vat.asr.asr_data import ASRData, ASRDataSeg
from vat.translator.llm_translator import LLMTranslator
from vat.translator.types import TargetLanguage
from vat.llm.prompts import get_prompt

logger = logging.getLogger("translation_benchmark")


def _default_google_proxy() -> str:
    """Google / Vertex 系列模型默认代理。

    优先级：
    1. VAT_BENCHMARK_GOOGLE_PROXY
    2. HTTPS_PROXY / https_proxy
    3. 历史默认值 http://localhost:7990
    """
    return (
        os.environ.get("VAT_BENCHMARK_GOOGLE_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or "http://localhost:7990"
    )

# ============================================================
# 模型配置注册表
# 每个模型一个 dict，包含 name/model/api_key/base_url/proxy
# ============================================================
MODEL_CONFIGS = {
    "gpt-5-nano": {
        "model": "gpt-5-nano",
        "api_key": os.environ.get("VAT_RESELLER_APIKEY", ""),
        "base_url": "https://api.videocaptioner.cn",
        "proxy": "",  # 中转站不需要代理
    },
    "MiniMax-M2.5": {
        "model": "MiniMax-M2.5",
        "api_key": os.environ.get("VAT_DASHSCOPE_APIKEY", ""),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "proxy": "",  # 国内服务不需要代理
    },
    "kimi-k2.5": {
        "model": "kimi-k2.5",
        "api_key": os.environ.get("VAT_DASHSCOPE_APIKEY", ""),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "proxy": "",
    },
    "qwen3.5-plus": {
        "model": "qwen3.5-plus",
        "api_key": os.environ.get("VAT_DASHSCOPE_APIKEY", ""),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "proxy": "",
    },
    "glm-5": {
        "model": "glm-5",
        "api_key": os.environ.get("VAT_DASHSCOPE_APIKEY", ""),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "proxy": "",
    },
    "qwen3-max": {
        "model": "qwen3-max-2026-01-23",
        "api_key": os.environ.get("VAT_DASHSCOPE_APIKEY", ""),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "proxy": "",
    },
    "glm-4.7": {
        "model": "glm-4.7",
        "api_key": os.environ.get("VAT_DASHSCOPE_APIKEY", ""),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "proxy": "",
    },
    # ===== 中转站 GPT 模型（http://81.70.32.82） =====
    # 可用模型：gpt-5, gpt-5.1, gpt-5.2, gpt-5.4, gpt-5-codex, gpt-5.1-codex, etc.
    # API Key 通过环境变量 VAT_PROXY_APIKEY 传入
    "proxy-gpt-5.4": {
        "model": "gpt-5.4",
        "api_key": os.environ.get("VAT_PROXY_APIKEY", ""),
        "base_url": "http://81.70.32.82/v1",
        "proxy": "",
    },
    "proxy-gpt-5.2": {
        "model": "gpt-5.2",
        "api_key": os.environ.get("VAT_PROXY_APIKEY", ""),
        "base_url": "http://81.70.32.82/v1",
        "proxy": "",
    },
    "proxy-gpt-5.1": {
        "model": "gpt-5.1",
        "api_key": os.environ.get("VAT_PROXY_APIKEY", ""),
        "base_url": "http://81.70.32.82/v1",
        "proxy": "",
    },
    "proxy-gpt-5": {
        "model": "gpt-5",
        "api_key": os.environ.get("VAT_PROXY_APIKEY", ""),
        "base_url": "http://81.70.32.82/v1",
        "proxy": "",
    },
    "proxy-gpt-5.1-codex-max": {
        "model": "gpt-5.1-codex-max",
        "api_key": os.environ.get("VAT_PROXY_APIKEY", ""),
        "base_url": "http://81.70.32.82/v1",
        "proxy": "",
    },
    # ===== 中转站 GPT/Claude 模型（ai.phpth.cn） =====
    # 可用模型：gpt-5, gpt-5.1, gpt-5.2, gpt-5.4, claude-opus-4-6, claude-sonnet-4-6
    # API Key 通过环境变量 VAT_PHPTH_APIKEY 传入
    "phpth-gpt-5.4": {
        "model": "gpt-5.4",
        "api_key": os.environ.get("VAT_PHPTH_APIKEY", ""),
        "base_url": "https://ai.phpth.cn/v1",
        "proxy": "",
    },
    "phpth-claude-sonnet-4-6": {
        "model": "claude-sonnet-4-6",
        "api_key": os.environ.get("VAT_PHPTH_APIKEY", ""),
        "base_url": "https://ai.phpth.cn/v1",
        "proxy": "",
    },
    "phpth-claude-opus-4-6": {
        "model": "claude-opus-4-6",
        "api_key": os.environ.get("VAT_PHPTH_APIKEY", ""),
        "base_url": "https://ai.phpth.cn/v1",
        "proxy": "",
    },
    # ===== 火山引擎方舟模型 =====
    # 可用模型：kimi-k2.5, deepseek-v3.2, glm-4.7, doubao-seed-2.0-code, doubao-seed-code, kimi-k2-thinking
    "volc-kimi-k2.5": {
        "model": "kimi-k2.5",
        "api_key": os.environ.get("VAT_VOLC_APIKEY", ""),
        "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "proxy": "",
    },
    "volc-deepseek-v3.2": {
        "model": "deepseek-v3.2",
        "api_key": os.environ.get("VAT_VOLC_APIKEY", ""),
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "proxy": "",
    },
    "volc-glm-4.7": {
        "model": "glm-4.7",
        "api_key": os.environ.get("VAT_VOLC_APIKEY", ""),
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "proxy": "",
    },
    "volc-doubao-seed-2.0-code": {
        "model": "doubao-seed-2.0-code",
        "api_key": os.environ.get("VAT_VOLC_APIKEY", ""),
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "proxy": "",
    },
    "volc-doubao-seed-code": {
        "model": "doubao-seed-code",
        "api_key": os.environ.get("VAT_VOLC_APIKEY", ""),
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "proxy": "",
    },
    "volc-kimi-k2-thinking": {
        "model": "kimi-k2-thinking",
        "api_key": os.environ.get("VAT_VOLC_APIKEY", ""),
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "proxy": "",
    },
    # ===== Google Gemini 模型（通过 Google API + 代理） =====
    # 用于 Gemini 系列横向对比评测
    "gemini-3-flash": {
        "model": "gemini-3-flash-preview",
        "api_key": os.environ.get("VAT_GOOGLE_APIKEY", ""),
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "proxy": _default_google_proxy(),
    },
    "gemini-3.1-flash-lite": {
        "model": "gemini-3.1-flash-lite-preview",
        "api_key": os.environ.get("VAT_GOOGLE_APIKEY", ""),
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "proxy": _default_google_proxy(),
    },
    "gemini-2.5-flash": {
        "model": "gemini-2.5-flash",
        "api_key": os.environ.get("VAT_GOOGLE_APIKEY", ""),
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "proxy": _default_google_proxy(),
    },
    "gemini-2.5-flash-lite": {
        "model": "gemini-2.5-flash-lite",
        "api_key": os.environ.get("VAT_GOOGLE_APIKEY", ""),
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "proxy": _default_google_proxy(),
    },
    "gemini-3-flash-vertex": {
        "model": "gemini-3-flash-preview",
        "api_key": os.environ.get("VAT_VERTEX_APIKEY", os.environ.get("VAT_GOOGLE_APIKEY", "")),
        "base_url": "",
        "proxy": _default_google_proxy(),
        "provider": "vertex_native",
        "auth_mode": "api_key",
        "location": "global",
        "project_id": "",
        "credentials_path": "",
    },
}

# 默认测试视频路径
DEFAULT_VIDEO_DATA_DIR = Path("/local/gzy/4090/vat/data/videos")

# 结果输出目录
RESULTS_DIR = Path(__file__).resolve().parent / "translation_benchmark_results"

# 中文提示词目录
ZH_PROMPTS_DIR = Path(__file__).resolve().parent / "translation_benchmark_prompts"


_RUNTIME_ENV_KEYS = [
    "VAT_LLM_PROVIDER",
    "VAT_VERTEX_AUTH_MODE",
    "VAT_VERTEX_LOCATION",
    "VAT_VERTEX_PROJECT_ID",
    "VAT_VERTEX_CREDENTIALS",
]


def _apply_model_runtime_env(config: dict) -> dict:
    """为本次 benchmark 运行设置 provider 相关环境变量，并返回旧值快照。"""
    previous = {key: os.environ.get(key) for key in _RUNTIME_ENV_KEYS}

    provider = config.get("provider", "openai_compatible")
    os.environ["VAT_LLM_PROVIDER"] = provider

    if provider == "vertex_native":
        os.environ["VAT_VERTEX_AUTH_MODE"] = config.get("auth_mode", "api_key")
        os.environ["VAT_VERTEX_LOCATION"] = config.get("location", "global")

        project_id = config.get("project_id", "")
        credentials_path = config.get("credentials_path", "")

        if project_id:
            os.environ["VAT_VERTEX_PROJECT_ID"] = project_id
        else:
            os.environ.pop("VAT_VERTEX_PROJECT_ID", None)

        if credentials_path:
            os.environ["VAT_VERTEX_CREDENTIALS"] = credentials_path
        else:
            os.environ.pop("VAT_VERTEX_CREDENTIALS", None)
    else:
        for key in _RUNTIME_ENV_KEYS[1:]:
            os.environ.pop(key, None)

    return previous


def _restore_runtime_env(previous: dict) -> None:
    """恢复 benchmark 前的 provider 相关环境变量。"""
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _install_zh_prompt_patch():
    """Monkey-patch get_prompt 使其返回中文版系统提示词"""
    import vat.llm.prompts as prompts_mod
    
    # 中文系统提示词映射
    zh_prompt_map = {
        "optimize/subtitle": ZH_PROMPTS_DIR / "optimize_system_zh.md",
        "translate/reflect": ZH_PROMPTS_DIR / "translate_reflect_zh.md",
    }
    
    original_load = prompts_mod._load_prompt_file.__wrapped__  # bypass lru_cache
    
    def patched_load(prompt_path: str) -> str:
        if prompt_path in zh_prompt_map:
            zh_file = zh_prompt_map[prompt_path]
            if zh_file.exists():
                return zh_file.read_text(encoding="utf-8")
        return original_load(prompt_path)
    
    # 替换 get_prompt 使用的加载函数
    prompts_mod._load_prompt_file.cache_clear()
    prompts_mod._load_prompt_file = patched_load
    
    # 同时需要替换 get_prompt 中对 _load_prompt_file 的引用
    original_get_prompt = prompts_mod.get_prompt
    def patched_get_prompt(prompt_path: str, **kwargs) -> str:
        from string import Template
        raw_prompt = patched_load(prompt_path)
        if not kwargs:
            return raw_prompt
        template = Template(raw_prompt)
        return template.safe_substitute(**kwargs)
    
    prompts_mod.get_prompt = patched_get_prompt
    logger.info("已安装中文提示词补丁")


def load_and_sample_srt(video_id: str, sample_count: int = 50) -> ASRData:
    """加载视频的 original.srt 并采样前 N 条"""
    srt_path = DEFAULT_VIDEO_DATA_DIR / video_id / "original.srt"
    if not srt_path.exists():
        raise FileNotFoundError(f"找不到 original.srt: {srt_path}")

    asr_data = ASRData.from_subtitle_file(str(srt_path))
    total = len(asr_data.segments)
    logger.info(f"加载 original.srt: {total} 条字幕")

    if sample_count > 0 and sample_count < total:
        sampled_segments = asr_data.segments[:sample_count]
        asr_data = ASRData(sampled_segments)
        logger.info(f"采样前 {sample_count} 条（共 {total} 条）")

    return asr_data


def load_reference_translation(video_id: str, sample_count: int = 50) -> dict:
    """
    从已有的 translated.srt 加载参考翻译（Gemini 的结果）
    
    Returns:
        {序号(1-based): {"original": 日语原文, "translated": 中文翻译}, ...}
    """
    srt_path = DEFAULT_VIDEO_DATA_DIR / video_id / "translated.srt"
    if not srt_path.exists():
        logger.warning(f"找不到 translated.srt，无参考翻译: {srt_path}")
        return {}

    asr_data = ASRData.from_subtitle_file(str(srt_path))
    result = {}
    for i, seg in enumerate(asr_data.segments, 1):
        if sample_count > 0 and i > sample_count:
            break
        result[i] = {
            "original": seg.text,
            "translated": seg.translated_text,
        }
    return result


def run_benchmark(
    video_id: str,
    model_name: str,
    sample_count: int = 50,
    skip_optimize: bool = False,
    prompt_lang: str = "en",
    temperature: float = 1.0,
    batch_size: int = 30,
    tag: str = "",
    custom_translate_prompt_path: str = "",
    optimized_input_path: str = "",
    skip_reflect: bool = False,
    thread_num: int = 3,
    optimize_only: bool = False,
):
    """
    对指定视频用指定模型跑 optimize + translate 流程
    
    Args:
        video_id: 视频 ID
        model_name: MODEL_CONFIGS 中的模型名称
        sample_count: 采样条数（0=全部）
        skip_optimize: 是否跳过 optimize 阶段
        prompt_lang: 提示词语言
        temperature: LLM 温度参数
        batch_size: 每批处理的字幕条数
        tag: 额外的输出目录标签（用于区分实验）
        custom_translate_prompt_path: 自定义翻译prompt文件路径（覆盖默认）
        optimized_input_path: 预优化字幕文件路径（跳过optimize阶段，直接用此文件做translate）
        skip_reflect: 是否使用standard模式而非reflect模式
        optimize_only: 是否只运行optimize阶段，跳过translate
    """
    if model_name not in MODEL_CONFIGS:
        raise ValueError(
            f"未知模型: {model_name}\n可用模型: {list(MODEL_CONFIGS.keys())}"
        )

    config = MODEL_CONFIGS[model_name]
    runtime_env_snapshot = _apply_model_runtime_env(config)
    
    try:
        # 火山引擎模型：注入 thinking=disabled 参数以避免reasoning导致的巨大延迟
        # kimi-k2.5 默认走thinking路径（34s/call），关闭后降至0.7s/call，与DashScope持平
        # 方案：patch get_or_create_client，对火山引擎的 client wrap 其 create 方法
        if config.get("base_url", "").startswith("https://ark.cn-beijing.volces.com"):
            import vat.llm.client as llm_client_mod
            _original_get_or_create = llm_client_mod.get_or_create_client
            
            def _patched_get_or_create(*args, **kwargs):
                client = _original_get_or_create(*args, **kwargs)
                # 只 patch 一次：检查是否已 patch
                if getattr(client, '_volc_thinking_disabled', False):
                    return client
                base_url_str = str(client.base_url)
                if 'ark.cn-beijing.volces.com' in base_url_str:
                    _orig_create = client.chat.completions.create
                    def _create_no_thinking(*a, **kw):
                        kw.setdefault('extra_body', {})
                        kw['extra_body']['thinking'] = {'type': 'disabled'}
                        return _orig_create(*a, **kw)
                    client.chat.completions.create = _create_no_thinking
                    client._volc_thinking_disabled = True
                return client
            
            llm_client_mod.get_or_create_client = _patched_get_or_create
            logger.info("已安装火山引擎 thinking=disabled 补丁（via get_or_create_client）")
        
        # 中转站模型：强制 stream=true 适配
        # 该中转站 API 要求 stream 必须为 true，否则返回错误
        # 方案：wrap client.create，强制 stream=True，收集 chunks 拼装为非流式响应
        if config.get("base_url", "").startswith("http://81.70.32.82"):
            import vat.llm.client as llm_client_mod
            from openai.types.chat import ChatCompletion, ChatCompletionMessage
            from openai.types.chat.chat_completion import Choice
            _original_get_or_create_proxy = llm_client_mod.get_or_create_client
            
            def _patched_get_or_create_proxy(*args, **kwargs):
                client = _original_get_or_create_proxy(*args, **kwargs)
                if getattr(client, '_proxy_stream_patched', False):
                    return client
                base_url_str = str(client.base_url)
                if '81.70.32.82' in base_url_str:
                    _orig_create = client.chat.completions.create
                    def _create_with_stream(*a, **kw):
                        kw['stream'] = True
                        stream = _orig_create(*a, **kw)
                        # 收集所有 chunk 的 content
                        content_parts = []
                        model_name_resp = kw.get('model', 'unknown')
                        resp_id = None
                        for chunk in stream:
                            if not resp_id and hasattr(chunk, 'id'):
                                resp_id = chunk.id
                            if not chunk.choices:
                                continue
                            delta = chunk.choices[0].delta
                            if hasattr(delta, 'content') and delta.content:
                                content_parts.append(delta.content)
                        # 拼装为 ChatCompletion 格式
                        full_content = "".join(content_parts)
                        return ChatCompletion(
                            id=resp_id or "proxy-stream",
                            object="chat.completion",
                            created=0,
                            model=model_name_resp,
                            choices=[Choice(
                                index=0,
                                message=ChatCompletionMessage(
                                    role="assistant",
                                    content=full_content,
                                ),
                                finish_reason="stop",
                            )],
                        )
                    client.chat.completions.create = _create_with_stream
                    client._proxy_stream_patched = True
                return client
            
            llm_client_mod.get_or_create_client = _patched_get_or_create_proxy
            logger.info("已安装中转站 stream=true 适配补丁")
        
        # 安装中文提示词补丁
        if prompt_lang == "zh":
            _install_zh_prompt_patch()
        
        # 准备输出目录
        dir_suffix = model_name
        if prompt_lang == "zh":
            dir_suffix += "_zh"
        if sample_count == 0:
            dir_suffix += "_full"
        if tag:
            dir_suffix += f"_{tag}"
        output_dir = RESULTS_DIR / video_id / dir_suffix
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 加载输入数据
        asr_data = load_and_sample_srt(video_id, sample_count)
        
        # 保存采样输入（方便回溯）
        input_srt = RESULTS_DIR / video_id / "input.srt"
        if not input_srt.exists():
            input_srt.parent.mkdir(parents=True, exist_ok=True)
            asr_data.save(str(input_srt))
            logger.info(f"保存采样输入: {input_srt}")
        
        # 读取自定义 prompt
        if prompt_lang == "zh":
            # 中文版 custom prompt
            custom_translate_prompt_file = ZH_PROMPTS_DIR / "custom_translate_fubuki_zh.md"
            custom_optimize_prompt_file = ZH_PROMPTS_DIR / "custom_optimize_fubuki_zh.md"
        else:
            # 英文版（与 config/default.yaml 中的 fubuki 一致）
            custom_translate_prompt_file = PROJECT_ROOT / "vat/llm/prompts/custom/translate/fubuki.md"
            custom_optimize_prompt_file = PROJECT_ROOT / "vat/llm/prompts/custom/optimize/fubuki.md"
        # 如果指定了自定义翻译prompt路径，使用它
        if custom_translate_prompt_path:
            ctp = Path(custom_translate_prompt_path)
            if not ctp.exists():
                raise FileNotFoundError(f"自定义翻译prompt文件不存在: {ctp}")
            custom_translate_prompt = ctp.read_text(encoding="utf-8")
            logger.info(f"  使用自定义翻译prompt: {ctp}")
        else:
            custom_translate_prompt = custom_translate_prompt_file.read_text(encoding="utf-8") if custom_translate_prompt_file.exists() else ""
        custom_optimize_prompt = custom_optimize_prompt_file.read_text(encoding="utf-8") if custom_optimize_prompt_file.exists() else ""
        
        # 温度覆盖（translate阶段的_agent_loop默认temp=1.0，通过monkey-patch注入）
        if temperature != 1.0:
            import vat.translator.llm_translator as translator_mod
            _original_call_llm = translator_mod.call_llm
            def _temp_override_call_llm(*args, **kwargs):
                kwargs['temperature'] = temperature
                return _original_call_llm(*args, **kwargs)
            translator_mod.call_llm = _temp_override_call_llm
            logger.info(f"  已注入温度覆盖: {temperature}")
        
        logger.info(f"=== 开始测试模型: {model_name} (prompt_lang={prompt_lang}) ===")
        logger.info(f"  模型: {config['model']}")
        logger.info(f"  Base URL: {config['base_url']}")
        logger.info(f"  提示词语言: {prompt_lang}")
        logger.info(f"  温度: {temperature}")
        logger.info(f"  批大小: {batch_size}")
        logger.info(f"  字幕条数: {len(asr_data)}")
        logger.info(f"  输出目录: {output_dir}")
        
        start_time = time.time()
        
        # 混合方案：如果提供了预优化输入，直接加载它跳过optimize
        if optimized_input_path:
            opt_input = Path(optimized_input_path)
            if not opt_input.exists():
                raise FileNotFoundError(f"预优化字幕文件不存在: {opt_input}")
            optimized_data = ASRData.from_subtitle_file(str(opt_input))
            # 采样同样数量
            if sample_count > 0 and len(optimized_data.segments) > sample_count:
                optimized_data = ASRData(optimized_data.segments[:sample_count])
            opt_elapsed = 0
            skip_optimize = True
            logger.info(f"使用预优化输入: {opt_input} ({len(optimized_data)} 条)")
        
        # 创建 translator（用于 optimize）
        if not skip_optimize:
            logger.info("--- 阶段 1: Optimize ---")
            opt_start = time.time()
            
            optimizer = LLMTranslator(
                thread_num=thread_num,
                batch_num=batch_size,
                target_language=TargetLanguage.SIMPLIFIED_CHINESE,
                output_dir=str(output_dir),
                model=config["model"],
                custom_translate_prompt=custom_translate_prompt,
                is_reflect=False,
                enable_optimize=True,
                custom_optimize_prompt=custom_optimize_prompt,
                enable_context=True,
                api_key=config["api_key"],
                base_url=config["base_url"],
                optimize_model=config["model"],
                optimize_api_key=config["api_key"],
                optimize_base_url=config["base_url"],
                proxy=config.get("proxy", ""),
                optimize_proxy=config.get("proxy", ""),
            )
            
            try:
                optimized_data = optimizer._optimize_subtitle(asr_data)
                optimized_srt = output_dir / "optimized.srt"
                optimized_data.save(str(optimized_srt))
                opt_elapsed = time.time() - opt_start
                logger.info(f"Optimize 完成: {len(optimized_data)} 条, 耗时 {opt_elapsed:.1f}s")
            except Exception as e:
                logger.error(f"Optimize 失败: {e}")
                import traceback
                traceback.print_exc()
                optimized_data = asr_data  # fallback 到原文
                opt_elapsed = time.time() - opt_start
            finally:
                optimizer.stop()
        else:
            optimized_data = asr_data
            opt_elapsed = 0
        
        # optimize_only 模式：只跑 optimize，跳过 translate
        if optimize_only:
            total_elapsed = time.time() - start_time
            meta = {
                "model_name": model_name,
                "model": config["model"],
                "base_url": config["base_url"],
                "video_id": video_id,
                "sample_count": len(asr_data),
                "skip_optimize": skip_optimize,
                "optimize_only": True,
                "optimize_time_sec": round(opt_elapsed, 1),
                "total_time_sec": round(total_elapsed, 1),
                "timestamp": datetime.now().isoformat(),
                "prompt_lang": prompt_lang,
                "tag": tag,
            }
            meta_path = output_dir / "meta.json"
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"=== Optimize-only 完成: {model_name}, 耗时 {total_elapsed:.1f}s ===")
            logger.info(f"  optimized.srt: {output_dir / 'optimized.srt'}")
            return True

        # 创建 translator（用于 translate）
        logger.info("--- 阶段 2: Translate (reflect mode) ---")
        trans_start = time.time()
        
        translator = LLMTranslator(
            thread_num=thread_num,
            batch_num=batch_size,
            target_language=TargetLanguage.SIMPLIFIED_CHINESE,
            output_dir=str(output_dir),
            model=config["model"],
            custom_translate_prompt=custom_translate_prompt,
            is_reflect=not skip_reflect,  # reflect或standard模式
            enable_optimize=False,   # optimize 已单独跑过
            custom_optimize_prompt="",
            enable_context=True,
            api_key=config["api_key"],
            base_url=config["base_url"],
            proxy=config.get("proxy", ""),
        )
        
        try:
            translated_data = translator.translate_subtitle(optimized_data)
            trans_elapsed = time.time() - trans_start
            logger.info(f"Translate 完成: {len(translated_data)} 条, 耗时 {trans_elapsed:.1f}s")
        except Exception as e:
            logger.error(f"Translate 失败: {e}")
            import traceback
            traceback.print_exc()
            translated_data = None
            trans_elapsed = time.time() - trans_start
        finally:
            translator.stop()
        
        total_elapsed = time.time() - start_time
        
        # 保存元信息
        meta = {
            "model_name": model_name,
            "model": config["model"],
            "base_url": config["base_url"],
            "video_id": video_id,
            "sample_count": len(asr_data),
            "skip_optimize": skip_optimize,
            "optimize_time_sec": round(opt_elapsed, 1),
            "translate_time_sec": round(trans_elapsed, 1),
            "total_time_sec": round(total_elapsed, 1),
            "timestamp": datetime.now().isoformat(),
            "reflect_mode": not skip_reflect,
            "batch_size": batch_size,
            "thread_num": thread_num,
            "prompt_lang": prompt_lang,
            "temperature": temperature,
            "tag": tag,
        }
        meta_path = output_dir / "meta.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        
        logger.info(f"=== 测试完成: {model_name} ===")
        logger.info(f"  总耗时: {total_elapsed:.1f}s (optimize: {opt_elapsed:.1f}s, translate: {trans_elapsed:.1f}s)")
        logger.info(f"  结果目录: {output_dir}")
        
        return translated_data is not None
    finally:
        _restore_runtime_env(runtime_env_snapshot)


def generate_comparison(video_id: str, sample_count: int = 50):
    """
    生成所有已测模型的对比 Markdown 表
    
    读取每个模型目录下的 translated.srt，与 Gemini 参考翻译并排展示。
    """
    video_dir = RESULTS_DIR / video_id
    if not video_dir.exists():
        logger.error(f"结果目录不存在: {video_dir}")
        return
    
    # 加载参考翻译
    reference = load_reference_translation(video_id, sample_count)
    
    # 加载原文输入
    input_srt = video_dir / "input.srt"
    if input_srt.exists():
        input_data = ASRData.from_subtitle_file(str(input_srt))
    else:
        input_data = load_and_sample_srt(video_id, sample_count)
    
    # 扫描所有模型结果
    model_results = {}
    for model_dir in sorted(video_dir.iterdir()):
        if not model_dir.is_dir():
            continue
        translated_srt = model_dir / "translated.srt"
        if not translated_srt.exists():
            continue
        
        model_name = model_dir.name
        asr_data = ASRData.from_subtitle_file(str(translated_srt))
        model_results[model_name] = {}
        for i, seg in enumerate(asr_data.segments, 1):
            model_results[model_name][i] = seg.translated_text
        
        # 读取 meta
        meta_path = model_dir / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            model_results[model_name]["__meta__"] = meta
    
    if not model_results:
        logger.error("没有找到任何模型测试结果")
        return
    
    model_names = [k for k in model_results.keys()]
    logger.info(f"找到 {len(model_names)} 个模型结果: {model_names}")
    
    # 生成 Markdown
    lines = []
    lines.append(f"# 翻译模型对比 - {video_id}")
    lines.append("")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"采样条数: {len(input_data)} / 总条数")
    lines.append("")
    
    # 模型信息表
    lines.append("## 模型信息")
    lines.append("")
    lines.append("| 模型 | 耗时(s) | Optimize(s) | Translate(s) |")
    lines.append("|------|---------|-------------|--------------|")
    if reference:
        lines.append("| **Gemini (参考)** | - | - | - |")
    for name in model_names:
        meta = model_results[name].get("__meta__", {})
        total = meta.get("total_time_sec", "-")
        opt = meta.get("optimize_time_sec", "-")
        trans = meta.get("translate_time_sec", "-")
        lines.append(f"| **{name}** | {total} | {opt} | {trans} |")
    lines.append("")
    
    # 逐条对比表
    lines.append("## 逐条对比")
    lines.append("")
    
    # 表头
    header = "| # | 原文(日语)"
    separator = "|---|----------"
    if reference:
        header += " | Gemini(参考)"
        separator += "|-------------"
    for name in model_names:
        header += f" | {name}"
        separator += "|" + "-" * max(len(name), 10)
    header += " |"
    separator += "|"
    lines.append(header)
    lines.append(separator)
    
    # 数据行
    for i, seg in enumerate(input_data.segments, 1):
        original = seg.text.replace("\n", " ").replace("|", "\\|")
        row = f"| {i} | {original}"
        
        if reference:
            ref_text = reference.get(i, {}).get("translated", "")
            ref_text = ref_text.replace("\n", " ").replace("|", "\\|")
            row += f" | {ref_text}"
        
        for name in model_names:
            model_text = model_results[name].get(i, "")
            model_text = model_text.replace("\n", " ").replace("|", "\\|")
            row += f" | {model_text}"
        
        row += " |"
        lines.append(row)
    
    # 写入文件
    comparison_path = video_dir / "comparison.md"
    comparison_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"对比表已生成: {comparison_path}")
    
    # 同时输出到 stdout（方便直接查看）
    print("\n" + "=" * 80)
    print("\n".join(lines))
    print("=" * 80)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    
    parser = argparse.ArgumentParser(description="翻译模型质量对比测试")
    parser.add_argument("--video-id", default="_QOMPli80JA", help="测试视频 ID")
    parser.add_argument("--model", type=str, help="要测试的模型名称")
    parser.add_argument("--sample", type=int, default=50, help="采样条数（0=全部）")
    parser.add_argument("--skip-optimize", action="store_true", help="跳过 optimize 阶段")
    parser.add_argument("--no-reflect", action="store_true", help="使用 standard 模式而非 reflect 模式")
    parser.add_argument("--prompt-lang", choices=["en", "zh"], default="en", help="提示词语言 (en=英文/zh=中文)")
    parser.add_argument("--temperature", type=float, default=1.0, help="LLM 温度参数")
    parser.add_argument("--batch-size", type=int, default=30, help="每批处理的字幕条数")
    parser.add_argument("--thread-num", type=int, default=3, help="并发线程数")
    parser.add_argument("--tag", type=str, default="", help="额外的输出目录标签")
    parser.add_argument("--custom-translate-prompt", type=str, default="", help="自定义翻译prompt文件路径（覆盖默认）")
    parser.add_argument("--optimized-input", type=str, default="", help="预优化字幕文件路径（跳过optimize，直接用此文件做translate）")
    parser.add_argument("--optimize-only", action="store_true", help="只运行 optimize 阶段，跳过 translate（用于预生成 optimized.srt）")
    parser.add_argument("--compare", action="store_true", help="生成对比表（不跑翻译）")
    parser.add_argument("--list-models", action="store_true", help="列出所有可用模型配置")
    
    args = parser.parse_args()
    
    if args.list_models:
        print("可用模型配置:")
        for name, cfg in MODEL_CONFIGS.items():
            print(f"  {name}: model={cfg['model']}, base_url={cfg['base_url']}")
        return
    
    if args.compare:
        generate_comparison(args.video_id, args.sample)
        return
    
    if not args.model:
        parser.error("请指定 --model 或使用 --compare / --list-models")
    
    success = run_benchmark(
        video_id=args.video_id,
        model_name=args.model,
        sample_count=args.sample,
        skip_optimize=args.skip_optimize,
        prompt_lang=args.prompt_lang,
        temperature=args.temperature,
        batch_size=args.batch_size,
        tag=args.tag,
        custom_translate_prompt_path=args.custom_translate_prompt,
        optimized_input_path=args.optimized_input,
        skip_reflect=args.no_reflect,
        thread_num=args.thread_num,
        optimize_only=args.optimize_only,
    )
    
    if success:
        # 测试成功后自动生成对比表
        generate_comparison(args.video_id, args.sample)
    else:
        logger.error("测试失败，跳过对比表生成")
        sys.exit(1)


if __name__ == "__main__":
    main()
