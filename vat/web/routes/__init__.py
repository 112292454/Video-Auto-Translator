"""
Web API 路由
"""
from .videos import router as videos_router
from .playlists import router as playlists_router
from .tasks import router as tasks_router
from .files import router as files_router
from .prompts import router as prompts_router
from .bilibili import router as bilibili_router
from .watch import router as watch_router
from .database import router as database_router
from .test_center import router as test_center_router

__all__ = [
    "videos_router",
    "playlists_router",
    "tasks_router",
    "files_router",
    "prompts_router",
    "bilibili_router",
    "watch_router",
    "database_router",
    "test_center_router",
]
