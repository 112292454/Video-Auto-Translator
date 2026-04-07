# VAT 模块文档：Web（管理界面）

> 基于 FastAPI + Jinja2 的 Web 管理界面。
>
> WebUI 是增强管理层，所有处理能力不依赖 WebUI。任务执行通过子进程调用 CLI 命令，与 Web 层完全解耦。
>
> 面向用户的使用手册见 `docs/webui_manual.md`，本文档侧重模块架构。

---

## 1. 模块组成

| 文件/目录 | 职责 |
|-----------|------|
| `app.py` | FastAPI 应用入口：路由注册、模板引擎、全局异常处理、页面路由 |
| `jobs.py` | 任务管理器 `JobManager`：子进程调度、状态追踪、日志解析、SQLite 持久化 |
| `deps.py` | 依赖注入（Database 单例） |
| `routes/` | API 路由模块（按功能拆分） |
| `templates/` | Jinja2 HTML 模板 |
| `services/` | Web 层业务服务（当前包含测试中心的检测/配置编辑逻辑） |

---

## 2. 架构设计

```
浏览器
  │
  ▼
FastAPI (app.py)
  │
  ├─ 页面路由 (app.py)          → Jinja2 模板渲染 → HTML
  │    ├─ /                     → index.html (视频总览)
  │    ├─ /video/{id}           → video_detail.html
  │    ├─ /playlists            → playlists.html
  │    ├─ /playlist/{id}        → playlist_detail.html
  │    ├─ /tasks                → tasks.html
  │    ├─ /tasks/new            → task_new.html
  │    ├─ /bilibili             → bilibili.html
  │    ├─ /prompts              → prompts.html
  │    ├─ /test                 → test_center.html
  │    ├─ /watch                → watch.html
  │    └─ /database             → database.html
  │
  ├─ API 路由 (routes/)         → JSON 响应
  │    ├─ videos.py             → /api/videos/...
  │    ├─ playlists.py          → /api/playlists/...
  │    ├─ tasks.py              → /api/tasks/...
  │    ├─ files.py              → /api/files/...
  │    ├─ bilibili.py           → /bilibili/...
  │    ├─ prompts.py            → /api/prompts/...
  │    ├─ test_center.py        → /api/test-center/...
  │    ├─ watch.py              → /api/watch/...
  │    └─ database.py           → /api/database/...
  │
  └─ JobManager (jobs.py)       → 子进程管理
       ├─ submit_job()          → 启动 `vat process ...` 子进程
       ├─ submit_tools_job()    → 启动 `vat tools ...` 子进程
       └─ 解析 stdout           → 进度/状态更新
```

---

## 3. 路由模块（routes/）

| 文件 | 路由前缀 | 功能 |
|------|----------|------|
| `videos.py` | `/api/videos` | 视频列表、详情、元数据查询 |
| `playlists.py` | `/api/playlists` | Playlist CRUD、同步、刷新、重翻译 |
| `tasks.py` | `/api/tasks` | 任务创建、状态查询、取消、日志查看 |
| `files.py` | `/api/files` | 字幕文件查看/编辑、视频文件服务 |
| `bilibili.py` | `/bilibili` | B 站合集管理、违规修复、单视频/整合集元信息同步 |
| `prompts.py` | `/api/prompts` | 提示词查看/编辑（热重载） |
| `test_center.py` | `/api/test-center` | 测试中心 API；轻量接口内联，重检测通过 `vat tools test-center` 子进程执行 |
| `watch.py` | `/api/watch` | Watch 会话管理（启动/停止/删除/轮次查询） |
| `database.py` | `/api/database` | 数据库只读浏览（表列表/分页查询/行详情） |

---

## 4. 任务管理器（jobs.py）

### 设计原则

- **子进程解耦**：所有处理任务通过 `subprocess.Popen` 执行 CLI 命令，WebUI 不直接调用处理模块
- **SQLite 持久化**：任务记录存储在独立的 `web_jobs.db`，Web 服务重启后可恢复状态
- **孤儿检测**：启动时检查 RUNNING 状态但 PID 已不存在的任务，标记为 FAILED

### 任务类型

| 类型 | 子进程命令 | 说明 |
|------|-----------|------|
| process | `vat process -v ... --stages ...` | 视频处理流水线 |
| fix-violation | `vat tools fix-violation --aid ...` | 违规视频修复 |
| sync-playlist | `vat tools sync-playlist --playlist ...` | Playlist 同步 |
| refresh-playlist | `vat tools refresh-playlist --playlist ...` | 元信息刷新 |
| upload-sync | `vat tools upload-sync --playlist ...` | 合集同步 |
| update-info | `vat tools update-info --playlist ...` | 批量更新视频信息 |
| watch | `vat tools watch --playlist ... --once` | Watch 监控任务 |
| test-center | `vat tools test-center --kind ...` | 测试中心重检测任务（LLM/ffmpeg/Whisper/视频探测） |

### 进度解析

JobManager 通过轮询子进程 stdout 解析标准化标记：

- `[N%]` → 更新 `progress` 字段
- `[RESULT_JSON]` → 解析结构化结果，供页面轮询接口读取
- `[SUCCESS]` → 标记任务为 COMPLETED
- `[FAILED]` → 标记任务为 FAILED

---

## 5. 模板（templates/）

| 模板 | 对应页面 |
|------|---------|
| `base.html` | 基础布局（导航栏、CSS/JS 引入） |
| `index.html` | 首页视频总览（搜索、过滤、批量操作） |
| `video_detail.html` | 视频详情（元数据、阶段时间线、字幕预览） |
| `playlists.html` | Playlist 列表 |
| `playlist_detail.html` | Playlist 详情（视频列表、同步操作） |
| `tasks.html` | 任务列表（状态、进度、日志） |
| `task_new.html` | 新建任务（选择视频/阶段/GPU） |
| `task_detail.html` | 任务详情（实时日志） |
| `bilibili.html` | B 站管理（合集、批量同步、违规修复） |
| `prompts.html` | 提示词编辑器 |
| `test_center.html` | 测试中心（接口连通性、配置编辑、环境自检） |
| `watch.html` | Watch 模式管理（会话列表、轮次详情、新建/停止/删除） |
| `database.html` | 数据库浏览（表列表、分页查询、行详情弹窗） |

---

## 6. 测试中心说明

测试中心的目标不是承载正式业务，而是把“安装/配置/环境/提示词”这类排查动作集中到一个独立页面，方便非专业用户和开发者自检。

当前包含：

- LLM 接口测试：按各阶段的有效配置解析后发送最小请求，检查接口是否能返回；真实请求通过后台子进程执行，避免把 Web 进程拖入长耗时网络调用
- Prompt 预览：直接复用 split / translate / optimize / scene_identify / video_info_translate 的真实 request builder，展示最终发出去的 model / proxy / messages
- 配置编辑：同时支持主配置与 `config/upload.yaml` 原文编辑；保存前自动备份，主配置保存后会重载 Web runtime、LLM client cache 与 prompt cache
- 环境检测：ffmpeg / ffprobe、硬件加速与 NVENC 信息、样例视频探测、音频提取、Whisper 模型来源/缓存/GPU/加载/最小推理、视频文件信息检查；这些检测也走 `JobManager -> vat tools test-center`，页面只负责提交和轮询

配置保护策略：

- 不把 YAML 转成表单后再回写，避免丢注释或重排结构
- 每次保存主配置或 upload 配置前都创建时间戳备份，可在页面直接恢复
- 页面额外展示基于 `config/default.yaml` 注释和 starter config 生成的参考项，便于查看字段说明、示例和当前值
- 重检测任务不在 HTTP 请求内直接执行，避免测试页请求与 Web 进程强耦合

---

## 7. 启动方式

```bash
# 开发模式
uvicorn vat.web.app:app --host 0.0.0.0 --port 8000 --reload

# 或通过 CLI
vat web --port 8000
```
