"""Web 路由层复用的 job orchestration helpers。"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional

from vat.web.jobs import JobStatus


_ACTIVE_DEFAULT = [JobStatus.PENDING, JobStatus.RUNNING]


def submit_or_reuse_job(
    job_manager,
    *,
    task_type: str,
    task_params: Dict,
    task_params_subset: Optional[Dict] = None,
    steps: List[str],
    active_statuses: Optional[Iterable[JobStatus]] = None,
    limit: int = 200,
) -> Dict[str, str]:
    """复用同参数的活跃任务，否则提交新的 tools job。"""
    statuses = list(active_statuses or _ACTIVE_DEFAULT)
    existing_job = job_manager.find_latest_job(
        task_type,
        task_params_subset=task_params_subset or task_params,
        statuses=statuses,
        limit=limit,
    )
    if existing_job:
        job_manager.update_job_status(existing_job.job_id)
        refreshed_job = job_manager.get_job(existing_job.job_id)
        if refreshed_job and refreshed_job.status in statuses:
            return {"job_id": refreshed_job.job_id, "status": refreshed_job.status.value}

    job_id = job_manager.submit_tools_job(
        task_type=task_type,
        task_params=task_params,
        steps=steps,
    )
    return {"job_id": job_id, "status": "submitted"}


def build_job_status_payload(
    job_manager,
    job_id: str,
    *,
    status_map: Optional[Dict[str, str]] = None,
    tail_lines: int = 3,
    result_loader: Optional[Callable[[str], Dict]] = None,
) -> Optional[Dict]:
    """统一构造 job 状态响应。"""
    job_manager.update_job_status(job_id)
    job = job_manager.get_job(job_id)
    if not job:
        return None

    payload = {
        "job_id": job.job_id,
        "status": (status_map or {}).get(job.status.value, job.status.value),
        "progress": job.progress,
        "message": job.error or "",
    }

    if not payload["message"] and hasattr(job_manager, "get_log_content"):
        log_lines = job_manager.get_log_content(job_id, tail_lines=tail_lines)
        payload["message"] = log_lines[-1] if log_lines else ""

    if result_loader:
        payload["result"] = result_loader(job_id)

    return payload
