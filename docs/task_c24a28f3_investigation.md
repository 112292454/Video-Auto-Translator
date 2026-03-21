# Task `c24a28f3` 排查记录

## 原始目标

排查 WebUI 任务 `c24a28f3` 的实际运行情况，确认：

- 任务本身最终是什么状态
- 200 个视频里有多少已经完成、多少失败
- 失败主要发生在哪个阶段、为什么失败
- 接下来应该优先做什么

## 排查计划

1. 读取运行时配置，确认任务库和日志路径。
2. 查询 `web_jobs`、`tasks` 和任务日志，分别统计任务级、视频级、阶段级状态。
3. 对失败原因做聚类，区分下载问题、LLM 断句问题、人工取消影响。
4. 回读相关实现，判断日志报错是否准确反映根因。

## 当前状态

- 运行时数据库：`/local/gzy/4090/vat/data/database.db`
- 任务日志：`/local/gzy/4090/vat/data/job_logs/job_c24a28f3.log`
- 任务最终状态：`cancelled`
- `cancel_requested=1`
- 任务开始时间：`2026-03-21 08:32:40`
- 任务结束时间：`2026-03-21 23:34:16`

## 当前结论

### 1. 任务级结论

- `c24a28f3` 不是系统自动判定的 `failed`，而是被取消的 `cancelled`。
- 取消发生在第 1 轮失败重试刚开始后不久，日志末尾停在 `23:33:57`，数据库结束时间是 `23:34:16`，符合人工取消/发送终止信号后的表现。

### 2. 视频级结论

- 任务目标视频数：`200`
- 全部完成 `download+whisper+split` 的视频：`35`
- 当前最新状态下失败的视频：`165`
- 没有处于“仅未完成但尚未失败”的视频；剩余视频都已经有明确失败记录。

### 3. 失败原因聚类

- `113` 个：`download` 阶段报“下载完成但找不到视频文件”
- `47` 个：`download` 阶段报“无法获取视频信息”
- `4` 个：`split` 阶段报 `Invalid OpenAI API response: empty choices or content`
- `1` 个：`split` 阶段报 `Request timed out.`

### 4. 根因判断

#### 下载失败是主因

日志中大量出现两类底层错误：

- `EOF occurred in violation of protocol (_ssl.c:997)`
- `The page needs to be reloaded`

代表性现象：

- `job_c24a28f3.log` 中 `EOF occurred...` 出现 `117` 次
- `The page needs to be reloaded` 出现 `47` 次
- 第 `1` 轮失败重试时，重新尝试的前几个视频仍然立刻命中 `The page needs to be reloaded`

#### “找不到视频文件”多半是误导性上层报错，不是输出格式问题

抽查失败目录：

- `NsueHCfU1Ak/` 下存在 `NsueHCfU1Ak.mp4.part`
- `WXFhn1yGZRw/` 下存在 `WXFhn1yGZRw.mp4.part`

这说明：

- yt-dlp 实际只留下了未完成的分片文件
- 上层在 `_download_with_retry()` 返回后，只检查 `mp4/webm/mkv` 成品文件
- 找不到成品时统一抛出“可能是输出格式不匹配”

因此这里的真实前因更接近：

- 下载中断 / SSL 断流 / YouTube 页面风控

而不是：

- 输出格式扩展名不在扫描列表里

#### “无法获取视频信息”有两层直接诱因

追加最小复现后，确认这类失败不是单纯“代理坏了”：

- 只要给 yt-dlp 强制传 `--extractor-args "youtube:player_client=web,web_safari,tv,mweb"`，公开视频也会稳定报 `The page needs to be reloaded`
- 不强制 `player_client` 时，同样的视频可以正常 `extract_info`
- 当前 `cookies/www.youtube.com_cookies.txt` 也会触发 `page reload`
  - `plain_no_cookie`：成功
  - `plain_with_cookie`：失败

因此这类错误在当前环境下的主要来源是：

- 下载路径里强制 `player_client` 的策略不适配当前 yt-dlp / YouTube 行为
- 当前 cookie 状态会让公开视频的信息提取失败

而不是：

- 只有代理节点不可用

#### 2026-03-22 的 cookie 续期复测结果

在 `2026-03-22 00:34:06` 更新 `cookies/www.youtube.com_cookies.txt` 后重新复测：

- `yt-dlp --cookies cookies/www.youtube.com_cookies.txt --skip-download ...`
  - `NsueHCfU1Ak`：仍然 `The page needs to be reloaded`
  - `e11fsGDFB-E`：仍然 `The page needs to be reloaded`
- `yt-dlp --cookies cookies/www.youtube.com_cookies.txt --test ...`
  - 这两个视频仍在真正下载前就直接 `page reload`

因此当前结论不是“cookie 一更新就解决”，而是：

- renew cookie 仍然是优先建议动作
- 但本次新 cookie 依然不兼容当前请求路径，fallback 仍然必要

追加一次更规范的导出后（`2026-03-22 00:54:00`，文件大小约 `2988` 字节）：

- 文件头部已经变成以 `.youtube.com` 为主，不再像上一版那样混入大量 Google 域 cookie
- 但 `yt-dlp --cookies cookies/www.youtube.com_cookies.txt --skip-download ...` 仍然对 `NsueHCfU1Ak` / `e11fsGDFB-E` 报 `The page needs to be reloaded`
- `yt-dlp --test ... --cookies ...` 也仍然在真正下载前直接 `page reload`

因此“这次 cookie 仍失败”的原因不能再简单归结为“导出了一整包 Google cookies”。
更准确地说，是：

- 只要 yt-dlp 识别到 **YouTube account cookies**
- 它就会进入账号态播放器请求路径
- 而这条路径在当前环境下对这些公开视频返回 `UNPLAYABLE`

#### 为什么新 cookie 仍然不工作

增加 `yt-dlp -v` 对照后，现象进一步明确：

- **无 cookie** 时，yt-dlp 会走：
  - `android vr player API JSON`
  - 然后成功拿到格式，视频可正常 `extract_info`
- **带当前 cookie** 时，yt-dlp 会先识别：
  - `Found YouTube account cookies`
  - 然后走 `tv downgraded / web / web_safari` 这组已登录路径
  - 这些路径的 `playability status` 都变成 `UNPLAYABLE`
  - 最后报 `The page needs to be reloaded`

这说明“更新后的 cookie 仍失败”并不一定等于“cookie 过期”，更可能是：

- 这份 cookie 让 yt-dlp 进入了已登录账号分支
- 但这组账号态请求在当前环境下对公开视频反而不可播放

在上一版 cookie 中，文件确实还包含大量 `.google.com / .google.co.jp / accounts.google.com` 账号态 cookie；
但在更精简的 YouTube-only cookie 复测中，问题依然存在。
所以“混入 Google 域 cookie”最多只能算风险放大因素，不能算唯一根因。

#### 断句失败是次要问题

仅 `5` 个视频在 `split` 阶段失败，日志中可以对应到：

- `GKAe402iYe8`
- `bOZ_AO23Dp0`
- `qUpVUfAZElo`
- `stg52WsaSko`
- `JBda7pOKlI8`

这些失败来自 LLM 返回空响应或超时，不是 GPU / Whisper 崩溃。

## 下一步任务

1. 先把下载问题当作主阻塞处理，不要先盯着 ASR/断句。
2. 若只求尽快跑通，优先修复 YouTube 下载环境后重跑这 `165` 个失败视频。
3. 若要避免后续误判，应该把下载器里“`.part` 残留 + yt-dlp 非零返回码”单独识别成网络/下载中断错误，而不是统一报“找不到视频文件/格式不匹配”。
4. 下载路径的 `extract_info` 不应默认强制 `player_client`；命中 `page reload` 时，应优先回退为无 cookie / 默认 client。
5. 对那 `5` 个 split 失败视频，下载链路稳定后再单独重跑 `split`，不必和 165 个下载失败视频绑在一起处理。

## 已实施修复

已在 `vat/downloaders/youtube.py` 中实施两类修复：

1. 下载路径 `_get_ydl_opts()` 默认不再强制注入 `extractor_args.player_client`
2. `_extract_info_with_retry()` 命中 `The page needs to be reloaded` 时：
   - 若携带 cookie，先移除 cookie 重试
   - 若仍携带 `extractor_args`，再移除 `player_client` 覆写重试
   - 并输出显式 warning：当前 cookie 可能失效或不兼容，建议重新导出 cookie 文件
3. `_download_with_retry()` 命中 `The page needs to be reloaded` 时，也会执行同样的“去 cookie / 去 player_client 覆写”回退
   - 同样会 warning 用户应优先重新导出 cookie，而不是依赖 fallback
4. `_download_with_retry()` 对 `yt-dlp` 非零返回码不再直接放过：
   - 会读取 yt-dlp 记录下来的最后错误
   - 若属于可重试网络错误（如 `EOF occurred...`），进入等待重试
   - 否则直接抛出真实下载失败，不再落到后面的“找不到视频文件”误报

## 验证结果

- 单元测试：`pytest tests/test_downloaders.py tests/test_youtube_downloader.py -q`
  - 结果：`34 passed`
- 真实代码路径验证：
  - 当前代码下，`NsueHCfU1Ak` / `e11fsGDFB-E` 经过“移除 cookie 后重试”都已能成功 `extract_info`
  - 控制台可见 `extract_info 命中 page reload，当前 YouTube cookie 可能已失效或不兼容；建议重新导出 ... 将移除 cookie 后重试`
- 2026-03-22 canary 小批次验证：
  - 选取 `c24a28f3` 开头 12 个失败样本（覆盖“无法获取视频信息”和“下载完成但找不到视频文件”两类旧错误）
  - 使用当前应用代码逐个执行：
    - `extract_info`：`12/12` 成功
    - 单文件 `mp4` test download：`12/12` 成功
  - 结论：当前修复已足以让这批任务的开头失败样本继续往下走，值得进入正式重跑
