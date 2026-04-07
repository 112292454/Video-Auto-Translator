# Manual Playlist And Batch Video Add Implementation Plan

> **For agentic workers:** Execute this plan using the current harness capabilities. Use checkboxes (`- [ ]`) for tracking. If subagents are explicitly requested or clearly beneficial, delegate bounded subtasks; otherwise execute directly.

**Goal:** 支持手动创建 list、手动批量添加视频并可选择归属 playlist，同时补上手动添加 YouTube 视频的信息抓取与详情页重抓按钮。

**Architecture:** 保持现有 CLI/DB/Web 分层不变，在 Web API 层扩展请求模型与批量入口，在服务层补一个“手动 playlist + 单视频信息抓取”公共能力，在模板层补充多条输入与 playlist 归属选择。继续沿用 `Playlist` / `playlist_videos` 现有数据结构，通过 metadata 标记 manual/default 类型，避免引入新的表。  
手动视频添加仍复用 `create_video_from_source()` 建立记录，再由新服务逻辑补抓可用的平台信息并写入 playlist 关联。

**Tech Stack:** FastAPI, Pydantic, Jinja2, SQLite, pytest

---

## Chunk 1: 数据与 API 边界

### Task 1: 明确 manual playlist 约定并补测试

**Files:**
- Modify: `tests/test_playlists_api.py`
- Modify: `tests/test_database.py`
- Modify: `vat/web/routes/playlists.py`
- Modify: `vat/database.py`

- [x] Step 1: 为“手动创建 playlist / 默认 playlist”写 failing tests
- [x] Step 2: 跑对应测试确认按预期失败
- [x] Step 3: 实现 manual playlist 创建、metadata 标记、必要的 DB 辅助方法
- [x] Step 4: 重跑测试确认通过

### Task 2: 扩展视频添加 API 到批量模式

**Files:**
- Modify: `tests/test_videos_api.py`
- Modify: `vat/web/routes/videos.py`

- [x] Step 1: 为多 source 请求、playlist 归属模式、单条兼容性写 failing tests
- [x] Step 2: 跑对应测试确认失败原因正确
- [x] Step 3: 实现批量请求解析与返回结构
- [x] Step 4: 重跑测试确认通过

## Chunk 2: 服务逻辑

### Task 3: 新增单视频信息抓取与手动 playlist 归属能力

**Files:**
- Modify: `tests/test_services.py`
- Modify: `vat/services/playlist_service.py`

- [x] Step 1: 为 YouTube 信息抓取写 failing tests
- [x] Step 2: 跑对应测试确认失败
- [x] Step 3: 实现单视频 info fetch / translate 提交 / manual playlist attach / default playlist ensure
- [x] Step 4: 重跑测试确认通过

## Chunk 3: Web UI

### Task 4: Playlist 页面支持手动创建 list

**Files:**
- Modify: `vat/web/templates/playlists.html`
- Modify: `vat/web/templates/playlist_detail.html`
- Modify: `vat/web/app.py`

- [x] Step 1: 调整 playlist 新建弹窗，支持 YouTube / 手动两种模式
- [x] Step 2: 手动 list 详情页正确显示无来源/无频道场景，并隐藏无意义操作
- [x] Step 3: 本地检查模板与路由数据字段一致

### Task 5: 首页视频添加弹窗支持四种方式批量添加与 playlist 选择

**Files:**
- Modify: `vat/web/templates/index.html`
- Modify: `vat/web/app.py`

- [x] Step 1: 将平台链接/直链/本地路径改为多行输入，上传改为多文件
- [x] Step 2: 增加 playlist 归属选择（指定 / 默认 / 不添加）
- [x] Step 3: 适配批量上传接口与前端提交流程
- [x] Step 4: 本地检查交互逻辑与返回结构一致

### Task 6: 视频详情页补“尝试获取信息”按钮

**Files:**
- Modify: `vat/web/templates/video_detail.html`
- Modify: `vat/web/routes/videos.py`

- [x] Step 1: 新增按钮与调用逻辑
- [x] Step 2: 对支持/不支持抓取的 source_type 给出明确反馈
- [x] Step 3: 本地检查页面行为

## Chunk 4: 文档与验证

### Task 7: 更新使用文档并完成验证

**Files:**
- Modify: `docs/webui_manual.md`
- Modify: `docs/superpowers/plans/2026-04-05-manual-playlist-and-batch-video-add.md`

- [x] Step 1: 跑最小必要 pytest 集合
- [x] Step 2: 更新 WebUI 手册中的 playlist / 手动添加视频说明
- [x] Step 3: 回填计划执行状态、记录剩余风险

## Chunk 5: Playlist 成员手动管理跟进

### Task 8: 支持从 playlist 中移出单个视频，并添加已有视频到 playlist

**Files:**
- Modify: `tests/test_playlists_api.py`
- Modify: `tests/test_services.py`
- Modify: `vat/services/playlist_service.py`
- Modify: `vat/web/routes/playlists.py`
- Modify: `vat/web/templates/playlist_detail.html`
- Modify: `docs/webui_manual.md`

- [x] Step 1: 为“移出 playlist 成员 / 添加已有视频到 playlist / 查询可添加视频”补 failing tests
- [x] Step 2: 跑对应测试确认失败点在缺失接口而不是测试本身
- [x] Step 3: 实现服务与 API
- [x] Step 4: 在 playlist 详情页增加单条移出和已有视频添加交互
- [x] Step 5: 跑针对性验证并同步手册

## Chunk 6: 排序、去重与 richer metadata 跟进

### Task 9: 修复 playlist 跨分页排序，并统一旧视频 richer metadata 补抓逻辑

**Files:**
- Modify: `tests/test_downloaders.py`
- Modify: `tests/test_videos_api.py`
- Modify: `tests/test_services.py`
- Modify: `vat/pipeline/executor.py`
- Modify: `vat/services/playlist_service.py`
- Modify: `vat/web/app.py`
- Modify: `vat/web/routes/videos.py`
- Modify: `vat/web/templates/playlist_detail.html`
- Modify: `docs/webui_manual.md`

- [x] Step 1: 为 YouTube 平台 ID 复用、重复添加拦截、playlist 全量排序、refresh richer metadata 补 failing tests
- [x] Step 2: 跑对应测试确认失败点准确
- [x] Step 3: 实现 YouTube 平台 ID 复用与重复添加 409 拦截
- [x] Step 4: 实现 playlist 服务端排序并让分页跟随排序
- [x] Step 5: 修复 refresh 路径，安全补 richer metadata 且不覆盖既有信息
- [x] Step 6: 跑针对性验证并同步手册

## Chunk 7: 复用现有单视频接口的批量前端操作

### Task 10: 在首页与 playlist 详情页支持批量补全/重翻视频信息

**Files:**
- Modify: `vat/web/templates/base.html`
- Modify: `vat/web/templates/index.html`
- Modify: `vat/web/templates/playlist_detail.html`
- Modify: `docs/webui_manual.md`

- [x] Step 1: 采用最小方案，不新增任务类型，只在前端批量调用现有单视频 API
- [x] Step 2: 在基础模板增加小并发 helper
- [x] Step 3: 首页增加“批量补全信息 / 批量重翻信息”
- [x] Step 4: playlist 详情页增加对应批量按钮
- [x] Step 5: 跑现有后端回归并同步手册

## Chunk 8: 批量信息按钮视觉收敛

### Task 11: 将批量补全/重翻入口收纳为不显眼的下拉菜单

**Files:**
- Modify: `vat/web/templates/index.html`
- Modify: `vat/web/templates/playlist_detail.html`

- [x] Step 1: 移除主操作区里过大的“补全信息 / 重翻信息”按钮
- [x] Step 2: 首页改成 `⋯` 小菜单
- [x] Step 3: playlist 详情页改成 `⋯` 下拉菜单
- [x] Step 4: 跑现有回归确认无后端副作用

## Chunk 9: Review 修复与入口约束

### Task 12: 修复 batch preflight、JS 传参与非 YouTube fetch 入口

**Files:**
- Modify: `tests/test_videos_api.py`
- Modify: `tests/test_services.py`
- Modify: `tests/test_downloaders.py`
- Modify: `docs/webui_manual.md`
- Modify: `vat/pipeline/executor.py`
- Modify: `vat/web/routes/videos.py`
- Modify: `vat/web/app.py`
- Modify: `vat/web/templates/index.html`
- Modify: `vat/web/templates/playlist_detail.html`
- Modify: `vat/web/templates/video_detail.html`

- [x] Step 1: 为 batch preflight/重复冲突补 failing tests
- [x] Step 2: 实现 preflight，避免批量添加半成功半失败
- [x] Step 3: 改成 JS-safe 的“移出”传参
- [x] Step 4: 详情页仅对 YouTube 显示 fetch-source-info
- [x] Step 5: 批量补全信息入口自动跳过非 YouTube 视频
- [x] Step 6: 跑针对性验证并同步手册
