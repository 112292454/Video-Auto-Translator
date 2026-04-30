"""
CLI tools 子命令组

提供统一的 tools 入口，支持 JobManager 作为子进程调用。
每个子命令包装现有功能逻辑，输出标准化进度标记：
  - [N%]      进度百分比
  - [RESULT_JSON] 结构化结果
  - [SUCCESS]  任务成功完成
  - [FAILED]   任务失败

用法:
  vat tools fix-violation --aid 12345
  vat tools sync-playlist --playlist PL_xxx
  vat tools refresh-playlist --playlist PL_xxx
  vat tools retranslate-playlist --playlist PL_xxx
  vat tools upload-sync --playlist PL_xxx
  vat tools update-info --playlist PL_xxx
  vat tools replace-videos --job-id JOB_ID --dry-run
  vat tools sync-db --season 12345 --playlist PL_xxx
  vat tools season-sync --playlist PL_xxx
  vat tools test-center --kind whisper
"""
import json
import traceback
import sys
import click
from pathlib import Path

from .commands import cli, get_config, get_logger
from ..services.bilibili_support import build_bilibili_uploader, find_local_video_for_aid


def _emit(msg: str):
    """输出消息到 stdout（确保子进程模式下日志可被 JobManager 读取）"""
    click.echo(msg, nl=True)
    sys.stdout.flush()


def _success(msg: str = ""):
    """输出成功标记"""
    _emit(f"[SUCCESS] {msg}" if msg else "[SUCCESS]")


def _failed(msg: str = ""):
    """输出失败标记"""
    _emit(f"[FAILED] {msg}" if msg else "[FAILED]")


def _progress(pct: int):
    """输出进度标记（0-100）"""
    _emit(f"[{pct}%]")


def _emit_result_json(payload):
    """输出结构化结果，供 WebUI 轮询接口解析。"""
    _emit(f"[RESULT_JSON] {json.dumps(payload, ensure_ascii=False)}")


def _get_config_and_db(ctx):
    """获取 config 和 db 实例"""
    from ..config import load_config
    from ..database import Database
    config_path = ctx.obj.get('config_path') if ctx.obj else None
    config = load_config(config_path)
    db = Database(config.storage.database_path, output_base_dir=config.storage.output_dir)
    return config, db


def _get_bilibili_uploader(config):
    """创建 BilibiliUploader 实例"""
    return build_bilibili_uploader(
        config,
        with_upload_params=True,
        project_root=Path(__file__).parent.parent.parent,
    )


def _get_playlist_service(config, db):
    """创建 PlaylistService 实例"""
    from ..services.playlist_service import PlaylistService
    return PlaylistService(db, config)


def _load_web_job_video_ids(db, job_id: str):
    """从 web_jobs 记录读取视频 ID 列表。"""
    import sqlite3

    with sqlite3.connect(str(db.db_path)) as conn:
        row = conn.execute(
            "SELECT video_ids FROM web_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    if not row:
        raise click.ClickException(f"找不到 web job: {job_id}")
    return json.loads(row[0] or "[]")


def _collect_replace_video_candidates(db, *, job_id=None, video_ids=None, limit=0, skip_verified=False):
    """收集可执行 B站稿件视频文件替换的候选项。"""
    from ..models import TaskStep, TaskStatus

    selected_ids = []
    if job_id:
        selected_ids.extend(_load_web_job_video_ids(db, job_id))
    selected_ids.extend(video_ids or [])
    selected_ids = list(dict.fromkeys(selected_ids))

    candidates = []
    skipped = []
    for video_id in selected_ids:
        video = db.get_video(video_id)
        if not video:
            skipped.append({"video_id": video_id, "reason": "video_not_found"})
            continue

        embed_task = db.get_task(video_id, TaskStep.EMBED)
        if not embed_task or embed_task.status != TaskStatus.COMPLETED:
            skipped.append({
                "video_id": video_id,
                "reason": f"embed_not_completed:{embed_task.status.value if embed_task else 'missing'}",
            })
            continue

        metadata = video.metadata or {}
        aid = metadata.get("bilibili_aid")
        if not aid:
            skipped.append({"video_id": video_id, "reason": "missing_bilibili_aid"})
            continue

        output_dir = Path(video.output_dir or "")
        final_video = output_dir / "final.mp4"
        if not final_video.exists() or final_video.stat().st_size <= 0:
            skipped.append({"video_id": video_id, "reason": "missing_or_empty_final_mp4"})
            continue

        final_stat = final_video.stat()
        if skip_verified:
            replace_op = dict((metadata.get("bilibili_ops") or {}).get("replace_video") or {})
            if (
                replace_op.get("state") == "verified"
                and replace_op.get("new_video_path") == str(final_video)
                and replace_op.get("new_video_size") == final_stat.st_size
                and replace_op.get("new_video_mtime_ns") == final_stat.st_mtime_ns
            ):
                skipped.append({"video_id": video_id, "reason": "already_verified_current_final_mp4"})
                continue

        candidates.append({
            "video": video,
            "video_id": video_id,
            "aid": int(aid),
            "final_video": final_video,
            "title": video.title or video_id,
        })

    if limit and limit > 0:
        candidates = candidates[:limit]
    return candidates, skipped


def _snapshot_replace_context(context):
    """提取替换前后需要保持不变的远端元信息。"""
    archive = dict(context.get("archive") or {})
    videos = list(context.get("old_videos") or [])
    first_video = dict(videos[0]) if videos else {}
    return {
        "title": archive.get("title", ""),
        "desc": archive.get("desc", ""),
        "tag": archive.get("tag", ""),
        "tid": archive.get("tid"),
        "copyright": archive.get("copyright"),
        "source": archive.get("source", ""),
        "part_count": len(videos),
        "part_title": first_video.get("title", ""),
        "part_desc": first_video.get("desc", ""),
    }


def _compare_replace_snapshots(before, after):
    """比较替换前后应保持不变的远端元信息。"""
    changed = []
    for key in ("title", "desc", "tag", "tid", "copyright", "source", "part_count", "part_title", "part_desc"):
        if before.get(key) != after.get(key):
            changed.append(key)
    return changed


# =============================================================================
# tools 命令组
# =============================================================================

@cli.group()
def tools():
    """工具任务（支持 job 化管理，可由 WebUI 或 CLI 发起）"""
    pass


@tools.command('test-center')
@click.option(
    '--kind',
    required=True,
    type=click.Choice(['llm', 'llm-all', 'ffmpeg', 'whisper', 'video-probe']),
    help='测试中心任务类型',
)
@click.option('--target-id', default='', help='LLM 测试目标 ID')
@click.option('--video-id', default='', help='视频探测用的视频 ID')
@click.option('--path', 'probe_path', default='', type=click.Path(), help='视频探测用的文件路径')
@click.pass_context
def tools_test_center(ctx, kind, target_id, video_id, probe_path):
    """执行测试中心任务（供 WebUI 通过 JobManager 子进程调用）。"""
    from ..web.deps import set_web_config_path
    from ..web.services import test_center as test_center_service

    config_path = ctx.obj.get('config_path') if ctx.obj else None
    set_web_config_path(config_path)

    try:
        _progress(5)

        if kind == 'llm':
            if not target_id:
                raise click.ClickException('--target-id 不能为空')
            result = test_center_service.run_llm_connectivity_test(target_id)
        elif kind == 'llm-all':
            result = test_center_service.run_all_llm_connectivity_tests()
        elif kind == 'ffmpeg':
            result = test_center_service.run_ffmpeg_check()
        elif kind == 'whisper':
            result = test_center_service.run_whisper_check()
        elif kind == 'video-probe':
            result = test_center_service.run_video_probe(video_id=video_id, path=probe_path)
        else:
            raise click.ClickException(f'未知测试类型: {kind}')

        _emit_result_json(result)
        _progress(100)
        _success(f'test-center:{kind}')
    except Exception as exc:
        result = {
            'ok': False,
            'error': str(exc),
            'traceback': traceback.format_exc(),
            'kind': kind,
        }
        _emit_result_json(result)
        _progress(100)
        _failed(f'test-center:{kind}: {exc}')


# =============================================================================
# fix-violation: 修复被退回的B站稿件
# =============================================================================

@tools.command('fix-violation')
@click.option('--aid', required=True, type=int, help='B站稿件 AV号')
@click.option('--video-path', type=click.Path(), default=None, help='本地视频文件路径')
@click.option('--margin', default=1.0, type=float, help='违规区间安全边距（秒）')
@click.option('--mask-text', default='此处内容因平台合规要求已被遮罩', help='遮罩文字')
@click.option('--dry-run', is_flag=True, help='仅遮罩不上传')
@click.option('--max-rounds', default=10, type=int, help='最大修复轮次（默认10，设为1则不自动循环）')
@click.option('--wait-seconds', default=0, type=int,
              help='每轮修复后等待审核的秒数（默认0=上传耗时*2，下限900秒）')
@click.pass_context
def tools_fix_violation(ctx, aid, video_path, margin, mask_text, dry_run, max_rounds, wait_seconds):
    """修复被退回的B站稿件（累积式遮罩+上传替换，默认自动循环直到通过）
    
    自动循环流程：mask→上传→等待审核→check是否通过→如不通过则再次修复。
    等待时间默认为上传耗时的2倍（下限15分钟）。
    修复成功后自动尝试添加到B站合集（如有配置）。
    """
    import time as _time
    config, db = _get_config_and_db(ctx)
    logger = get_logger()

    try:
        uploader = _get_bilibili_uploader(config)

        # 查找本地视频（只做一次，所有轮次复用）
        local_video = None
        if video_path:
            local_video = Path(video_path)
            if not local_video.exists():
                _failed(f"文件不存在: {video_path}")
                return
        else:
            local_video = _find_local_video_for_aid(aid, config, db, uploader)
            if local_video:
                _emit(f"找到本地文件: {local_video}")
            else:
                _emit("⚠️ 本地文件未找到，将从B站下载（质量会降低）")

        from .commands import _get_previous_violation_ranges, _save_violation_ranges
        from ..services.bilibili_workflows import fix_violation_with_recovery

        # 读取累积的 violation_ranges
        all_previous_ranges = _get_previous_violation_ranges(db, aid)
        if all_previous_ranges:
            _emit(f"历史已 mask: {len(all_previous_ranges)} 段")

        for round_num in range(max_rounds):
            _emit(f"\n{'='*40}")
            _emit(f"第 {round_num + 1}/{max_rounds} 轮修复")
            _emit(f"{'='*40}")

            result = fix_violation_with_recovery(
                db=db,
                uploader=uploader,
                aid=aid,
                video_path=local_video,
                mask_text=mask_text,
                margin_sec=margin,
                previous_ranges=all_previous_ranges,
                dry_run=dry_run,
                callback=_emit,
            )

            if not result['success']:
                _failed(result['message'])
                return

            # 更新累积 ranges 并持久化
            all_previous_ranges = result['all_ranges']
            _save_violation_ranges(db, aid, all_previous_ranges)

            # dry-run 模式不循环
            if dry_run:
                _progress(100)
                _success(f"dry-run 完成，遮罩文件: {result.get('masked_path', '')}")
                return

            # 最后一轮不再等待
            if round_num >= max_rounds - 1:
                _emit(f"已达最大轮次 ({max_rounds})，最后一轮修复已提交")
                break

            # 计算等待时间：用户指定 > 上传耗时*3 > 下限900秒(15分钟)
            upload_dur = result.get('upload_duration', 0)
            if wait_seconds > 0:
                actual_wait = wait_seconds
            else:
                actual_wait = max(int(upload_dur * 3), 900)
            _emit(f"上传耗时 {upload_dur:.0f}s，等待审核 {actual_wait}s ({actual_wait // 60} 分钟)...")

            # 等待，每10分钟输出一次日志
            waited = 0
            log_interval = 600  # 10分钟
            while waited < actual_wait:
                chunk = min(log_interval, actual_wait - waited)
                _time.sleep(chunk)
                waited += chunk
                remaining = actual_wait - waited
                if remaining > 0:
                    _emit(f"  等待审核中... 剩余 {remaining // 60} 分钟")

            # 检查是否仍被退回
            _emit("检查审核状态...")
            rejected = uploader.get_rejected_videos()
            still_rejected = any(v['aid'] == aid for v in rejected)

            if not still_rejected:
                _emit(f"av{aid} 已不在退回列表中（已通过审核或仍在审核中）")
                _post_fix_actions(db, uploader, config, aid)
                _progress(100)
                _success(f"av{aid} 修复流程完成")
                return

            _emit(f"av{aid} 仍被退回，准备下一轮修复...")
            # 下一轮 fix_violation 会重新 get_rejected_videos 获取最新违规信息

        # 所有轮次结束（最后一轮已提交但未等待检查）
        _post_fix_actions(db, uploader, config, aid)
        _progress(100)
        _success(f"av{aid} 已完成 {max_rounds} 轮修复提交")

    except Exception as e:
        logger.error(f"fix-violation 异常: {e}", exc_info=True)
        _failed(str(e))


def _post_fix_actions(db, uploader, config, aid: int):
    """修复成功后的收尾操作：从DB模板重新渲染元信息 + 添加到合集
    
    1. resync_video_info: 从 DB 和模板重新渲染 title/desc/tags/tid 并更新到 B站
    2. add_to_season: 如果配置了目标合集且尚未添加，则添加到合集
    """
    import json, sqlite3
    from ..services.bilibili_workflows import resync_video_info
    logger = get_logger()
    
    # Step 1: 重新渲染元信息并同步到 B站
    try:
        _emit(f"从 DB 模板重新渲染元信息...")
        sync_result = resync_video_info(db, uploader, config, aid, callback=_emit)
        if not sync_result['success']:
            _emit(f"  ⚠️ 元信息同步失败: {sync_result['message']}（不影响修复结果）")
    except Exception as e:
        logger.warning(f"resync_video_info 异常（不影响修复结果）: {e}")
    
    # Step 2: 添加到合集
    try:
        conn = sqlite3.connect(str(db.db_path))
        c = conn.cursor()
        c.execute("SELECT id, metadata FROM videos WHERE metadata LIKE ?", (f'%{aid}%',))
        rows = c.fetchall()
        conn.close()

        for (video_id, meta_str) in rows:
            if not meta_str:
                continue
            meta = json.loads(meta_str)
            if str(meta.get('bilibili_aid')) != str(aid):
                continue
            season_id = meta.get('bilibili_target_season_id')
            if not season_id:
                _emit(f"av{aid} 未配置目标合集，跳过 add-to-season")
                return
            if meta.get('bilibili_season_added'):
                _emit(f"av{aid} 已在合集 {season_id} 中，跳过")
                return
            _emit(f"尝试将 av{aid} 添加到合集 {season_id}...")
            ok = uploader.add_to_season(aid, int(season_id))
            if ok:
                meta['bilibili_season_added'] = True
                db.update_video(video_id, metadata=meta)
                _emit(f"  已添加到合集 {season_id}")
            else:
                _emit(f"  添加到合集失败（不影响修复结果）")
            return
        _emit(f"DB 中未找到 av{aid} 对应的视频记录，跳过 add-to-season")
    except Exception as e:
        logger.warning(f"add-to-season 异常（不影响修复结果）: {e}")


def _find_local_video_for_aid(aid, config, db, uploader):
    """通过 B站稿件信息查找本地视频文件。"""
    return find_local_video_for_aid(aid, config, db, uploader)


# =============================================================================
# sync-playlist: 同步 Playlist（增量更新）
# =============================================================================

@tools.command('sync-playlist')
@click.option('--playlist', required=True, help='Playlist ID')
@click.option('--url', default=None, help='Playlist URL（首次添加时需要）')
@click.option('--fetch-dates', is_flag=True, default=True, help='获取上传日期')
@click.pass_context
def tools_sync_playlist(ctx, playlist, url, fetch_dates):
    """同步 YouTube Playlist（增量更新视频列表）"""
    config, db = _get_config_and_db(ctx)

    try:
        pl = db.get_playlist(playlist)
        if not pl:
            _failed(f"Playlist 不存在: {playlist}")
            return

        playlist_url = url or pl.source_url
        if not playlist_url:
            _failed(f"Playlist {playlist} 无 source_url，需通过 --url 指定")
            return

        service = _get_playlist_service(config, db)
        _emit(f"同步 Playlist: {pl.title}")
        _progress(10)

        result = service.sync_playlist(
            playlist_url,
            auto_add_videos=True,
            fetch_upload_dates=fetch_dates,
            progress_callback=_emit,
            target_playlist_id=playlist
        )

        _progress(100)
        _success(f"同步完成: 新增 {result.new_count}, 已存在 {result.existing_count}")

    except Exception as e:
        _failed(str(e))


# =============================================================================
# refresh-playlist: 刷新 Playlist 视频信息
# =============================================================================

@tools.command('refresh-playlist')
@click.option('--playlist', required=True, help='Playlist ID')
@click.option('--force-refetch', is_flag=True, help='强制重新获取所有字段')
@click.option('--force-retranslate', is_flag=True, help='强制重新翻译')
@click.pass_context
def tools_refresh_playlist(ctx, playlist, force_refetch, force_retranslate):
    """刷新 Playlist 视频信息（补全缺失的封面、时长、日期等）"""
    config, db = _get_config_and_db(ctx)

    try:
        pl = db.get_playlist(playlist)
        if not pl:
            _failed(f"Playlist 不存在: {playlist}")
            return

        service = _get_playlist_service(config, db)
        _emit(f"刷新 Playlist: {pl.title}")
        _progress(10)

        result = service.refresh_videos(
            playlist,
            force_refetch=force_refetch,
            force_retranslate=force_retranslate,
            callback=_emit
        )

        _progress(100)
        _success(f"刷新完成: 成功 {result['refreshed']}, 失败 {result['failed']}, 跳过 {result['skipped']}")

    except Exception as e:
        _failed(str(e))


# =============================================================================
# retranslate-playlist: 重新翻译 Playlist 视频标题/简介
# =============================================================================

@tools.command('retranslate-playlist')
@click.option('--playlist', required=True, help='Playlist ID')
@click.pass_context
def tools_retranslate_playlist(ctx, playlist):
    """重新翻译 Playlist 中所有视频的标题/简介"""
    from ..services.playlist_service import PlaylistService

    config, db = _get_config_and_db(ctx)

    try:
        service = PlaylistService(db)
        pl = service.get_playlist(playlist)
        if not pl:
            _failed(f"Playlist 不存在: {playlist}")
            return

        _emit(f"重新翻译 Playlist: {pl.title}")
        _progress(10)

        service.retranslate_videos(playlist)

        _progress(100)
        _success("重新翻译完成")

    except Exception as e:
        _failed(str(e))


# =============================================================================
# upload-sync: 将已上传视频批量添加到B站合集
# =============================================================================

@tools.command('upload-sync')
@click.option('--playlist', required=True, help='Playlist ID')
@click.option('--retry-delay', default=30, type=int, help='失败后重试等待（分钟），0=不重试')
@click.pass_context
def tools_upload_sync(ctx, playlist, retry_delay):
    """将已上传但未入集的视频批量添加到B站合集并排序"""
    from ..services.playlist_service import PlaylistService

    config, db = _get_config_and_db(ctx)

    try:
        service = PlaylistService(db)
        pl = service.get_playlist(playlist)
        if not pl:
            _failed(f"Playlist 不存在: {playlist}")
            return

        from ..services.bilibili_workflows import season_sync
        uploader = _get_bilibili_uploader(config)

        _emit(f"开始 upload sync: {pl.title}")
        _progress(10)

        result = season_sync(db, uploader, playlist)
        _emit(f"结果: {result['success']} 成功, {result['failed']} 失败, 共 {result['total']} 个")

        # 显示诊断结果
        diag = result.get('diagnostics', {})
        if diag.get('upload_completed_no_aid'):
            _emit(f"⚠ {len(diag['upload_completed_no_aid'])} 个视频 upload 已完成但无 bilibili_aid（需手动核实）")
        if diag.get('aid_not_found_on_bilibili'):
            _emit(f"⚠ {len(diag['aid_not_found_on_bilibili'])} 个视频有 bilibili_aid 但 B站查不到（可能已删除）")

        if result['failed'] > 0 and retry_delay > 0:
            _emit(f"有 {result['failed']} 个失败，{retry_delay} 分钟后重试...")
            _progress(50)
            import time
            time.sleep(retry_delay * 60)

            uploader2 = _get_bilibili_uploader(config)
            _emit("开始重试...")
            result2 = season_sync(db, uploader2, playlist)
            _emit(f"重试结果: {result2['success']} 成功, {result2['failed']} 失败")

            if result2['failed'] > 0:
                _failed(f"仍有 {result2['failed']} 个视频失败")
                return

        _progress(100)
        if result['total'] == 0:
            _success("没有待同步的视频")
        else:
            _success(f"同步完成: {result['success']} 个视频已添加到合集")

    except Exception as e:
        _failed(str(e))


# =============================================================================
# update-info: 批量更新已上传视频的标题和简介
# =============================================================================

@tools.command('update-info')
@click.option('--playlist', required=True, help='Playlist ID')
@click.option('--dry-run', is_flag=True, help='仅预览不执行')
@click.pass_context
def tools_update_info(ctx, playlist, dry_run):
    """批量更新已上传视频的标题和简介（重新渲染模板）"""
    from ..services.playlist_service import PlaylistService
    from ..uploaders.template import render_upload_metadata

    config, db = _get_config_and_db(ctx)

    try:
        service = PlaylistService(db)
        pl = service.get_playlist(playlist)
        if not pl:
            _failed(f"Playlist 不存在: {playlist}")
            return

        bilibili_config = config.uploader.bilibili
        templates = {}
        if bilibili_config.templates:
            templates = {
                'title': bilibili_config.templates.title,
                'description': bilibili_config.templates.description,
                'custom_vars': bilibili_config.templates.custom_vars,
            }

        pl_upload_config = (pl.metadata or {}).get('upload_config', {})

        videos = service.get_playlist_videos(playlist)
        uploaded = []
        for v in videos:
            meta = v.metadata or {}
            aid = meta.get('bilibili_aid')
            if aid:
                uploaded.append((v, int(aid)))

        if not uploaded:
            _success("没有已上传到B站的视频")
            return

        _emit(f"已上传视频: {len(uploaded)} 个")
        _progress(10)

        # 构建更新列表
        update_list = []
        for v, aid in uploaded:
            pv_info = db.get_playlist_video_info(playlist, v.id)
            upload_order_index = pv_info.get('upload_order_index', 0) if pv_info else 0
            if not upload_order_index:
                upload_order_index = (v.metadata or {}).get('upload_order_index', 0) or v.playlist_index or 0

            playlist_info = {
                'name': pl.title,
                'id': pl.id,
                'index': upload_order_index,
                'uploader_name': pl_upload_config.get('uploader_name', ''),
            }
            rendered = render_upload_metadata(v, templates, playlist_info)

            meta = v.metadata or {}
            translated = meta.get('translated', {})

            update_list.append({
                'video': v,
                'aid': aid,
                'new_title': rendered['title'][:80],
                'new_desc': rendered['description'][:2000],
                'new_tags': translated.get('tags', None),
                'new_tid': translated.get('recommended_tid', None),
            })

        if dry_run:
            for i, item in enumerate(update_list, 1):
                _emit(f"[{i}] av{item['aid']}: {item['new_title'][:60]}")
            _success(f"dry-run 完成，共 {len(update_list)} 个视频待更新")
            return

        # 执行更新
        uploader = _get_bilibili_uploader(config)
        success = 0
        failed = 0
        total = len(update_list)
        for i, item in enumerate(update_list, 1):
            pct = 10 + int(90 * i / total)
            _progress(pct)
            title_short = item['new_title'][:40]
            _emit(f"[{i}/{total}] av{item['aid']}: {title_short}...")

            if uploader.edit_video_info(
                aid=item['aid'],
                title=item['new_title'],
                desc=item['new_desc'],
                tags=item['new_tags'],
                tid=item['new_tid'],
            ):
                success += 1
            else:
                failed += 1
                _emit(f"  ✗ 更新失败")

            import time
            time.sleep(1)

        _progress(100)
        if failed == 0:
            _success(f"完成: {success} 个视频已更新")
        else:
            _failed(f"{success} 成功, {failed} 失败")

    except Exception as e:
        _failed(str(e))


# =============================================================================
# replace-videos: 批量替换已上传稿件的视频文件
# =============================================================================

@tools.command('replace-videos')
@click.option('--job-id', default='', help='只处理该 Web job 中的视频 ID')
@click.option('--video-id', 'video_ids', multiple=True, help='额外指定视频 ID，可重复传入')
@click.option('--limit', default=0, type=int, help='最多处理多少个候选视频；0 表示不限制')
@click.option('--dry-run', is_flag=True, help='仅列出候选，不上传、不编辑稿件')
@click.option('--check-remote', is_flag=True, help='dry-run 时也读取远端稿件元信息，验证可加载 title/desc/tags')
@click.option('--skip-verified', is_flag=True, help='跳过已成功替换且 final.mp4 未变化的视频')
@click.option('--yes', is_flag=True, help='确认执行真实替换；非 dry-run 必须传入')
@click.option('--sleep-seconds', default=3.0, type=float, help='真实替换时每个稿件之间的等待秒数')
@click.option('--verify-metadata/--no-verify-metadata', default=True, help='真实替换后检查标题/简介/标签等元信息是否保持不变')
@click.pass_context
def tools_replace_videos(ctx, job_id, video_ids, limit, dry_run, check_remote, skip_verified, yes, sleep_seconds, verify_metadata):
    """批量替换 B站已有稿件的视频文件，不新建投稿、不重新渲染简介。"""
    import time
    from ..services.bilibili_workflows import replace_video_with_recovery

    if not job_id and not video_ids:
        _failed("必须提供 --job-id 或至少一个 --video-id")
        return
    if not dry_run and not yes:
        _failed("真实替换需要显式传入 --yes")
        return

    config, db = _get_config_and_db(ctx)
    candidates, skipped = _collect_replace_video_candidates(
        db,
        job_id=job_id or None,
        video_ids=list(video_ids),
        limit=limit,
        skip_verified=skip_verified,
    )

    _emit(f"候选视频: {len(candidates)} 个，跳过: {len(skipped)} 个")
    for item in candidates:
        size_mb = item["final_video"].stat().st_size / 1024 / 1024
        _emit(f"  av{item['aid']} {item['video_id']} {size_mb:.1f}MB {item['title'][:60]}")
    if skipped:
        reasons = {}
        for item in skipped:
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
        _emit(f"跳过原因: {reasons}")

    result_payload = {
        "job_id": job_id or None,
        "candidate_count": len(candidates),
        "skipped_count": len(skipped),
        "skipped": skipped[:50],
        "success": 0,
        "failed": 0,
        "metadata_warnings": [],
        "details": [],
        "dry_run": bool(dry_run),
        "skip_verified": bool(skip_verified),
    }

    uploader = None
    if dry_run:
        if check_remote and candidates:
            uploader = _get_bilibili_uploader(config)
            for idx, item in enumerate(candidates, 1):
                context = uploader._load_replace_video_context(item["aid"])
                if context:
                    snap = _snapshot_replace_context(context)
                    _emit(
                        f"[check {idx}/{len(candidates)}] av{item['aid']} "
                        f"title={snap['title'][:40]} desc={len(snap['desc'])} tags={snap['tag'][:80]}"
                    )
                    result_payload["details"].append({
                        "video_id": item["video_id"],
                        "aid": item["aid"],
                        "remote_loaded": True,
                        "desc_len": len(snap["desc"]),
                        "part_count": snap["part_count"],
                    })
                else:
                    _emit(f"[check {idx}/{len(candidates)}] av{item['aid']} 远端上下文加载失败")
                    result_payload["details"].append({
                        "video_id": item["video_id"],
                        "aid": item["aid"],
                        "remote_loaded": False,
                    })
        _emit_result_json(result_payload)
        _success("dry-run 完成")
        return

    uploader = _get_bilibili_uploader(config)
    total = len(candidates)
    if total == 0:
        _emit_result_json(result_payload)
        _success("没有可替换的视频")
        return

    for idx, item in enumerate(candidates, 1):
        pct = int(100 * (idx - 1) / total)
        _progress(pct)
        aid = item["aid"]
        video_id = item["video_id"]
        final_video = item["final_video"]
        _emit(f"[{idx}/{total}] 替换 av{aid} <- {video_id}/final.mp4")

        before = None
        if verify_metadata:
            context = uploader._load_replace_video_context(aid)
            if not context:
                _emit("  远端上下文加载失败，跳过该稿件")
                result_payload["failed"] += 1
                result_payload["details"].append({
                    "video_id": video_id,
                    "aid": aid,
                    "success": False,
                    "message": "远端上下文加载失败",
                })
                continue
            before = _snapshot_replace_context(context)

        result = replace_video_with_recovery(db, uploader, aid, final_video)
        detail = {
            "video_id": video_id,
            "aid": aid,
            "success": bool(result.get("success")),
            "message": result.get("message", ""),
        }
        if result.get("success"):
            result_payload["success"] += 1
            _emit(f"  替换提交成功: av{aid}")
            if verify_metadata and before:
                after_context = uploader._load_replace_video_context(aid)
                if after_context:
                    after = _snapshot_replace_context(after_context)
                    changed = _compare_replace_snapshots(before, after)
                    detail["metadata_changed_fields"] = changed
                    if changed:
                        warning = {"video_id": video_id, "aid": aid, "fields": changed}
                        result_payload["metadata_warnings"].append(warning)
                        _emit(f"  ⚠ 元信息字段变化: {changed}")
                    else:
                        _emit("  元信息校验通过: title/desc/tags/tid 未变化")
                else:
                    detail["metadata_check"] = "after_context_failed"
                    result_payload["metadata_warnings"].append({
                        "video_id": video_id,
                        "aid": aid,
                        "fields": ["after_context_failed"],
                    })
                    _emit("  ⚠ 替换后远端上下文加载失败，无法校验元信息")
        else:
            result_payload["failed"] += 1
            _emit(f"  替换失败: {detail['message']}")

        result_payload["details"].append(detail)
        if idx < total and sleep_seconds > 0:
            time.sleep(sleep_seconds)

    _progress(100)
    _emit_result_json(result_payload)
    if result_payload["failed"] == 0 and not result_payload["metadata_warnings"]:
        _success(f"完成: {result_payload['success']} 个稿件已替换")
    elif result_payload["failed"] == 0:
        _failed(f"替换完成但有 {len(result_payload['metadata_warnings'])} 个元信息校验警告")
    else:
        _failed(f"{result_payload['success']} 成功, {result_payload['failed']} 失败")


# =============================================================================
# sync-db: 从B站合集同步信息回数据库
# =============================================================================

@tools.command('sync-db')
@click.option('--season', required=True, type=int, help='B站合集ID')
@click.option('--playlist', required=True, help='Playlist ID')
@click.option('--dry-run', is_flag=True, help='仅预览不执行')
@click.pass_context
def tools_sync_db(ctx, season, playlist, dry_run):
    """将B站合集中的视频信息同步回数据库"""
    import re
    from ..services.playlist_service import PlaylistService

    config, db = _get_config_and_db(ctx)

    try:
        service = PlaylistService(db)
        pl = service.get_playlist(playlist)
        if not pl:
            _failed(f"Playlist 不存在: {playlist}")
            return

        uploader = _get_bilibili_uploader(config)

        _emit(f"获取合集 {season} 的视频列表...")
        season_info = uploader.get_season_episodes(season)
        if not season_info:
            _failed("无法获取合集信息")
            return

        episodes = season_info.get('episodes', [])
        _emit(f"合集中共 {len(episodes)} 个视频")
        _progress(10)

        # 建立映射
        pl_videos = service.get_playlist_videos(playlist)
        vid_to_video = {}
        aid_to_video = {}
        for v in pl_videos:
            vid_to_video[v.id] = v
            if v.source_url:
                yt_match = re.search(r'[?&]v=([a-zA-Z0-9_-]+)', v.source_url)
                if yt_match:
                    vid_to_video[yt_match.group(1)] = v
            meta = v.metadata or {}
            if meta.get('bilibili_aid'):
                aid_to_video[int(meta['bilibili_aid'])] = v

        # 匹配
        matched = []
        unmatched = []
        for i, ep in enumerate(episodes):
            aid = ep.get('aid')

            if aid in aid_to_video:
                matched.append((ep, aid_to_video[aid], 'aid'))
                continue

            detail = uploader.get_video_detail(aid)
            if detail:
                desc = detail.get('desc', '')
                yt_match = re.search(r'youtube\.com/watch\?v=([a-zA-Z0-9_-]+)', desc)
                if yt_match:
                    yt_vid = yt_match.group(1)
                    if yt_vid in vid_to_video:
                        matched.append((ep, vid_to_video[yt_vid], 'desc'))
                        continue

            unmatched.append(ep)
            pct = 10 + int(40 * (i + 1) / len(episodes))
            _progress(pct)

        _emit(f"匹配结果: {len(matched)} 匹配, {len(unmatched)} 未匹配")
        _progress(60)

        if dry_run:
            for ep, v, method in matched:
                _emit(f"  ✓ av{ep.get('aid')} → {v.id} ({method})")
            for ep in unmatched:
                _emit(f"  ✗ av{ep.get('aid')}: {ep.get('title', '')[:40]} (未匹配)")
            _success(f"dry-run 完成: {len(matched)} 匹配, {len(unmatched)} 未匹配")
            return

        # 写入 DB
        updated = 0
        for ep, v, method in matched:
            meta = v.metadata or {}
            meta['bilibili_aid'] = ep.get('aid')
            meta['bilibili_bvid'] = ep.get('bvid', '')
            meta['bilibili_target_season_id'] = season
            meta['bilibili_season_added'] = True
            db.update_video(v.id, metadata=meta)
            updated += 1

        _progress(100)
        _success(f"已更新 {updated} 条记录")

    except Exception as e:
        _failed(str(e))


# =============================================================================
# season-sync: B站合集同步（添加视频到合集+排序）
# =============================================================================

@tools.command('season-sync')
@click.option('--playlist', required=True, help='Playlist ID')
@click.pass_context
def tools_season_sync(ctx, playlist):
    """将 Playlist 中已上传的视频同步到B站合集（添加+排序）"""
    config, db = _get_config_and_db(ctx)

    try:
        from ..uploaders.bilibili import season_sync
        uploader = _get_bilibili_uploader(config)

        _emit(f"开始 season sync (playlist={playlist})...")
        _progress(10)

        result = season_sync(db, uploader, playlist)

        _emit(f"结果: {result['success']} 成功, {result['failed']} 失败, 共 {result['total']} 个")
        _progress(100)

        if result['failed'] == 0:
            _success(f"同步完成: {result['success']} 个视频已添加到合集")
        else:
            _failed(f"部分失败: {result['success']} 成功, {result['failed']} 失败")

    except Exception as e:
        _failed(str(e))


# =============================================================================
# watch: 自动监控 Playlist 并处理新视频
# =============================================================================

@tools.command('watch')
@click.option('--playlist', '-p', multiple=True, required=True, help='Playlist ID（可多次指定）')
@click.option('--interval', '-i', default=None, type=int, help='轮询间隔（分钟）')
@click.option('--once', is_flag=True, help='单次模式')
@click.option('--stages', '-s', default=None, help='处理阶段')
@click.option('--gpu', '-g', default='auto', help='GPU 设备')
@click.option('--concurrency', '-c', default=None, type=int, help='并发处理数')
@click.option('--force', '-f', is_flag=True, help='强制重处理')
@click.option('--fail-fast', is_flag=True, help='失败时停止')
@click.pass_context
def tools_watch(ctx, playlist, interval, once, stages, gpu, concurrency, force, fail_fast):
    """自动监控 Playlist 并处理新视频"""
    from ..services.watch_service import WatchService
    from ..services.process_job_submitter import build_process_job_submitter

    config, db = _get_config_and_db(ctx)
    process_job_submitter = build_process_job_submitter(
        config,
        config_path=ctx.obj.get('config_path'),
    )

    watch_config = config.watch
    effective_interval = interval if interval is not None else watch_config.default_interval
    effective_stages = stages if stages is not None else watch_config.default_stages
    effective_concurrency = concurrency if concurrency is not None else watch_config.default_concurrency

    _emit(f"启动 Watch: playlists={list(playlist)}, interval={effective_interval}min")
    _progress(5)

    try:
        service = WatchService(
            config=config,
            db=db,
            playlist_ids=list(playlist),
            interval_minutes=effective_interval,
            stages=effective_stages,
            gpu_device=gpu,
            concurrency=effective_concurrency,
            force=force,
            fail_fast=fail_fast,
            once=once,
            process_job_submitter=process_job_submitter,
        )
        service.run()
        _progress(100)
        _success("Watch 已完成" if once else "Watch 已停止")
    except RuntimeError as e:
        _failed(str(e))
    except Exception as e:
        _failed(str(e))
