# 从零启动 VAT：克隆到可运行的完整流程

本文档记录从一个完全空的目录克隆仓库，到能够正常运行 VAT 的必要步骤。建议第一次部署时只先跑通“本地视频最小路径”，确认环境、配置、LLM、Whisper、ffmpeg 都正常后，再启用 YouTube 下载、WebUI、B站上传和 Watch 模式。

## 目标与范围

按本文档完成后，应至少具备：

- 能在新目录中安装 VAT，并执行 `vat --help`。
- 能生成 `config/config.yaml`，并把路径、LLM、GPU/ASR、字幕嵌入等关键配置改成当前机器可用的值。
- 能用一个本地视频跑通 `download -> whisper -> split -> optimize -> translate -> embed`。
- 能按需启动 WebUI，使用 `/test` 页面做环境自检。
- 如果需要上传，能完成 B站登录、上传模板配置、单视频上传或批量上传前的检查。

本文档不假设你有作者机器上的 `/local/gzy/...` 路径、代理、cookie 或云模型凭据。`config/default.yaml` 是完整字段参考，第一次部署请优先运行 `vat init` 生成 starter config。

## 1. 准备系统环境

推荐环境：

- Linux 服务器，Python 3.10 或 3.11。
- NVIDIA CUDA GPU。ASR 和硬字幕嵌入都明显受益于 GPU。
- 系统级 `ffmpeg` 和 `ffprobe`。
- 可访问 LLM API、HuggingFace 或你自己的模型缓存。
- 足够磁盘空间。长视频的原始视频、中间音频、字幕、最终视频、模型缓存都会占用空间。
- `requirements.txt` 已包含字幕预览、封面处理和字体解析所需的 `Pillow`、`fonttools`。

Ubuntu 常用系统依赖示例：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip ffmpeg sqlite3
```

如果使用 NVIDIA GPU，先确认驱动和 CUDA 运行时可见：

```bash
nvidia-smi
ffmpeg -hide_banner -encoders | grep -E "nvenc|av1_nvenc|h264_nvenc|hevc_nvenc"
```

如果第二条没有输出，硬字幕 GPU 编码不可用。你仍可以改用 CPU 编码或软字幕，但首次部署建议先解决 ffmpeg/NVENC 环境，避免长视频处理耗时过长。

## 2. 克隆仓库并安装

从空目录开始：

```bash
mkdir -p ~/work && cd ~/work
git clone https://github.com/ZeyuanGuo/Video-Auto-Translator.git vat && cd vat

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
pip install -e .
```

检查 CLI 是否可用：

```bash
vat --help
python -m vat --help
```

两者等价。若 `vat` 不在 PATH 中，直接使用 `python -m vat`。

## 3. 生成 starter config

运行：

```bash
vat init
```

默认会生成：

```text
config/config.yaml
```

配置加载优先级为：

1. 命令行显式传入的 `--config PATH`
2. 当前工作目录下的 `config/config.yaml`
3. 仓库内的 `config/default.yaml`

因此日常运行建议始终在仓库根目录执行命令，或者显式传入：

```bash
vat --config /abs/path/to/config/config.yaml pipeline --url /abs/path/to/video.mp4 --title demo
```

## 4. 必改配置项

打开 `config/config.yaml`：

```bash
nano config/config.yaml
```

### 4.1 存储路径

starter config 默认使用相对路径，适合单机快速跑通：

```yaml
storage:
  work_dir: ./work
  output_dir: ./data/videos
  database_path: ./data/database.db
  models_dir: ./models
  resource_dir: vat/resources
  fonts_dir: vat/resources/fonts
  subtitle_style_dir: vat/resources/subtitle_style
  cache_dir: ~/.vat/cache
```

生产环境建议改成容量充足的绝对路径，例如：

```yaml
storage:
  work_dir: /data/vat/work
  output_dir: /data/vat/videos
  database_path: /data/vat/database.db
  models_dir: /data/vat/models
```

说明：

- `output_dir` 下会按 `video_id` 创建每个视频的目录。
- SQLite 数据库在 `database_path`。
- Whisper 等模型默认下载到 `models_dir/asr.models_subdir`，通常是 `models/whisper`。
- 字体和字幕样式已经随仓库打包，一般不需要额外下载字体。

### 4.2 LLM 配置

断句、优化、翻译、视频信息翻译都需要 LLM。starter config 默认使用 OpenAI-compatible 形式：

```yaml
llm:
  provider: openai_compatible
  auth_mode: api_key
  api_key: ${VAT_LLM_APIKEY}
  base_url: https://api.openai.com/v1
  model: gpt-4o-mini
```

推荐用环境变量保存 key：

```bash
export VAT_LLM_APIKEY="your-api-key"
```

如果使用中转站或私有 OpenAI-compatible 服务，改：

```yaml
llm:
  provider: openai_compatible
  api_key: ${VAT_LLM_APIKEY}
  base_url: https://your-openai-compatible-endpoint/v1
  model: your-model-name
```

阶段级覆写规则：

- `asr.split.model/api_key/base_url` 控制智能断句。
- `translator.llm.model/api_key/base_url` 控制翻译。
- `translator.llm.optimize.model/api_key/base_url` 控制字幕优化。
- 这些字段留空时继承全局 `llm`。

首次跑通时建议先让这些阶段都使用同一个可靠模型，减少排障变量。

### 4.3 ASR 与 Whisper 模型

关键字段：

```yaml
asr:
  backend: faster-whisper
  model: large-v3
  language: ja
  device: cuda
  compute_type: float32
  models_subdir: whisper
```

首次运行 ASR 时，`faster-whisper` 会下载模型到：

```text
storage.models_dir/asr.models_subdir
```

如果服务器不能直连 HuggingFace，可以先在有网络的机器下载模型，再复制到该目录。也可以配置代理：

```yaml
proxy:
  http_proxy: http://127.0.0.1:7890
```

CPU 临时验证可改：

```yaml
asr:
  device: cpu
  compute_type: int8

gpu:
  device: cpu
  allow_cpu_fallback: true
```

但 CPU 跑长视频会很慢，生产使用不建议依赖 CPU。

### 4.4 字幕嵌入与 GPU 编码

`vat init` 生成的 starter config 会沿用当前默认编码设置，关键字段通常类似：

```yaml
embedder:
  embed_mode: hard
  output_container: mp4
  video_codec: libx265
  audio_codec: copy
  use_gpu: true
  subtitle_style: default
  max_nvenc_sessions_per_gpu: 5
```

`use_gpu: true` 表示启用硬字幕相关的 GPU 路径；具体编码器仍由 `video_codec` 决定。如果希望使用 NVIDIA NVENC 编码器，可以把 `video_codec` 改成当前 ffmpeg 支持的编码器，例如：

```yaml
embedder:
  video_codec: h264_nvenc
  use_gpu: true
```

先用 WebUI `/test` 或下面的命令确认 NVENC 是否可用：

```bash
vat tools test-center --kind ffmpeg
```

CPU fallback 示例：

```yaml
embedder:
  video_codec: libx264
  use_gpu: false
```

### 4.5 并发与 GPU

常用字段：

```yaml
gpu:
  device: auto
  allow_cpu_fallback: false
  min_free_memory_mb: 2000

concurrency:
  gpu_devices: [0]
  max_concurrent_per_gpu: 1
  max_concurrent_downloads: 1
  max_concurrent_uploads: 1
```

多 GPU 机器可以改：

```yaml
concurrency:
  gpu_devices: [0, 1, 2, 3]
  max_concurrent_per_gpu: 1
```

单次命令也可临时指定：

```bash
vat pipeline --url /abs/path/video.mp4 --title demo --gpus 0,1
vat process -p PLAYLIST_ID -s embed -c 4 -g auto
```

### 4.6 YouTube 下载配置

本地视频最小路径不需要配置 YouTube cookie。需要下载 YouTube 时再配置：

```yaml
downloader:
  youtube:
    cookies_file: cookies/www.youtube.com_cookies.txt
    remote_components: ["ejs:github"]
    download_delay: 30
```

说明：

- `cookies_file` 使用 Netscape 格式 cookie，用于绕过 YouTube bot 检测。
- `remote_components` 可帮助 yt-dlp 处理 JS challenge。
- 批量下载建议保留 `download_delay`，防止限流。

### 4.7 B站上传配置

本地视频最小路径不需要 B站 cookie。需要上传时：

```bash
pip install stream_gears qrcode
vat bilibili login
vat bilibili status
```

`vat bilibili login` 会把登录信息保存到 `uploader.bilibili.cookies_file` 指定的位置。starter config 中该字段为空，需要先设置：

```yaml
uploader:
  bilibili:
    cookies_file: cookies/bilibili/account.json
    line: AUTO
    threads: 3
    upload_interval: 60
```

投稿参数、标题/简介模板等内容配置来自：

```text
config/upload.yaml
```

可以通过 WebUI 编辑，也可以直接修改该文件。首次上传前建议先执行 dry-run 或只传一个测试视频，不要直接批量上传。

## 5. 运行自检

安装和配置后先做这些检查：

```bash
python -V
which ffmpeg && ffmpeg -version | head
which ffprobe && ffprobe -version | head
vat --help
vat tools test-center --kind ffmpeg
vat tools test-center --kind llm-all
vat tools test-center --kind whisper
```

如果有测试视频，也可以探测：

```bash
vat tools test-center --kind video-probe --path /abs/path/to/video.mp4
```

WebUI 中同样可以打开 `/test` 页面做这些检查。

## 6. 跑通本地视频最小路径

准备一个较短的本地视频，例如：

```text
/data/samples/demo.mp4
```

本地视频路径请使用绝对路径，或先用 `realpath` 转成绝对路径。相对路径在部分入口中可能不会被识别成本地文件源。

运行：

```bash
vat pipeline --url /data/samples/demo.mp4 --title "demo"
```

如果文件在当前目录：

```bash
vat pipeline --url "$(realpath ./demo.mp4)" --title "demo"
```

该命令会自动检测本地文件源，并执行：

```text
download -> whisper -> split -> optimize -> translate -> embed
```

输出位置通常为：

```text
storage.output_dir/<video_id>/
```

常见产物包括：

- 原始或导入视频文件。
- `audio.*` 或中间音频文件。
- `original.srt` / `translated.srt` / `translated.ass`。
- `final.mp4` 或最终封装视频。

查看状态：

```bash
vat status
vat status -v VIDEO_ID
```

也可直接看数据库：

```bash
sqlite3 data/database.db ".tables"
sqlite3 data/database.db "select id,title,output_dir from videos order by created_at desc limit 5;"
```

如果 `data/database.db` 不是你的实际路径，请以 `config/config.yaml` 的 `storage.database_path` 为准。

## 7. 分阶段重跑

跑通后可以按阶段重跑：

```bash
# 只重跑字幕嵌入
vat process -v VIDEO_ID -s embed --force

# 只重跑 ASR 相关阶段
vat process -v VIDEO_ID -s whisper,split --force

# 只重跑翻译和嵌入
vat process -v VIDEO_ID -s optimize,translate,embed --force

# 对 playlist 批量执行某阶段
vat process -p PLAYLIST_ID -s embed -c 4 --force
```

阶段名：

```text
download, whisper, split, optimize, translate, embed, upload
```

`all` 表示全流程。阶段组 `asr` 表示 `whisper,split`。

## 8. 启动 WebUI

启动：

```bash
vat web
```

默认端口是 `13579`。也可以指定：

```bash
vat web --host 0.0.0.0 --port 13579
```

打开：

```text
http://localhost:13579
```

首次部署建议先打开：

```text
http://localhost:13579/test
```

在测试中心检查：

- LLM 连通性。
- ffmpeg/ffprobe。
- Whisper 模型。
- 样例视频探测。

WebUI 只是管理层，实际任务通过 CLI 子进程执行。Web 服务重启不应影响已经提交的后台任务。

## 9. YouTube 与 Playlist 路径

单视频：

```bash
vat pipeline --url "https://www.youtube.com/watch?v=VIDEO_ID"
```

Playlist：

```bash
vat pipeline --playlist "https://www.youtube.com/playlist?list=PLAYLIST_ID"
```

如果 YouTube 下载失败，按顺序检查：

1. `yt-dlp` 是否为较新版本。
2. `downloader.youtube.cookies_file` 是否存在且是 Netscape 格式。
3. `remote_components` 是否设置为 `["ejs:github"]`。
4. 代理是否需要设置到 `proxy.downloader` 或 `proxy.http_proxy`。
5. 是否触发限流，需要增大 `download_delay`。

## 10. 上传到 B站

上传前确认：

- 本地视频已经完成 `embed` 阶段。
- `final.mp4` 存在且能播放。
- `uploader.bilibili.cookies_file` 可用。
- `config/upload.yaml` 中的标题、简介、标签、分区符合预期。
- Playlist 场景下，`upload_order_index` 已经正确分配。

单视频上传：

```bash
vat upload video VIDEO_ID
```

指定合集：

```bash
vat upload video VIDEO_ID --season SEASON_ID
```

批量上传 playlist：

```bash
vat upload playlist PLAYLIST_ID --limit 1
```

上传后同步合集：

```bash
vat upload sync -p PLAYLIST_ID
```

从 B站合集反查并补齐数据库：

```bash
vat upload sync-db -s SEASON_ID -p PLAYLIST_ID --dry-run
vat upload sync-db -s SEASON_ID -p PLAYLIST_ID
```

更新已上传视频信息：

```bash
vat upload update-info -p PLAYLIST_ID --dry-run
vat upload update-info -p PLAYLIST_ID -y
```

注意：不要用上传命令替代“替换已有视频”的需求。如果只是要替换已投稿稿件的视频文件，应使用项目已有的 replace/fix 工作流，避免重新创建稿件。

## 11. 常见问题

### `vat init` 后仍然加载了作者路径

确认当前目录下是否有 `config/config.yaml`：

```bash
pwd
ls config/config.yaml
```

如果没有，VAT 会回退到 `config/default.yaml`。第一次部署必须运行 `vat init`，或显式指定 `--config`。

### LLM 报认证或 base_url 错误

检查：

```bash
echo "$VAT_LLM_APIKEY"
grep -n "llm:" -A20 config/config.yaml
vat tools test-center --kind llm-all
```

确认阶段级配置没有填错。例如 `translator.llm.api_key`、`asr.split.api_key` 留空时才会继承全局 `llm.api_key`。

### Whisper 模型下载失败

检查网络、代理和模型目录权限：

```bash
grep -n "models_dir" -A5 config/config.yaml
ls -la models
vat tools test-center --kind whisper
```

无法联网时，把模型提前放到：

```text
storage.models_dir/asr.models_subdir
```

### ffmpeg 找不到或 NVENC 不可用

检查：

```bash
which ffmpeg
ffmpeg -hide_banner -encoders | grep nvenc
vat tools test-center --kind ffmpeg
```

临时绕过可改 `embedder.video_codec: libx264` 与 `embedder.use_gpu: false`，但速度会慢很多。

### 数据库里看不到上传字段

当前数据库把很多扩展信息保存在 `videos.metadata` JSON 中，而不是全部展开成独立列。查询 B站字段时可以用：

```bash
sqlite3 data/database.db \
  "select id, json_extract(metadata,'$.bilibili_aid'), json_extract(metadata,'$.bilibili_bvid') from videos limit 5;"
```

具体数据库路径以 `storage.database_path` 为准。

### WebUI 端口打不开

检查配置和进程：

```bash
grep -n "web:" -A5 config/config.yaml
vat web --host 0.0.0.0 --port 13579
```

服务器远程访问时，还要确认防火墙、安全组或 SSH 隧道。

## 12. 最小复现检查清单

从零部署时，按下面顺序打勾：

- [ ] `git clone https://github.com/ZeyuanGuo/Video-Auto-Translator.git vat && cd vat`
- [ ] 创建并激活 `.venv`
- [ ] `pip install -r requirements.txt`
- [ ] `pip install -e .`
- [ ] `vat --help` 正常输出
- [ ] `vat init` 生成 `config/config.yaml`
- [ ] 修改 `storage.*` 到当前机器路径
- [ ] 配置 `VAT_LLM_APIKEY` 或 `llm.api_key`
- [ ] `vat tools test-center --kind ffmpeg` 通过
- [ ] `vat tools test-center --kind llm-all` 通过
- [ ] `vat tools test-center --kind whisper` 通过
- [ ] `vat pipeline --url /abs/path/demo.mp4 --title demo` 跑通
- [ ] `storage.output_dir/<video_id>/final.mp4` 存在且能播放
- [ ] 如需 WebUI，`vat web` 后 `/test` 页面可访问
- [ ] 如需 YouTube，配置 cookie/remote components 后跑通单个公开视频
- [ ] 如需 B站，`vat bilibili login` 和 `vat bilibili status` 通过

## 13. 推荐的首次运行策略

建议顺序：

1. 本地短视频最小路径。
2. 本地长视频，验证 ASR 分块和嵌字速度。
3. 单个 YouTube 视频，验证下载和 cookie。
4. 单个视频上传 B站，验证 cookie、模板、分区、简介完整性。
5. 小规模 playlist 批量处理。
6. Watch 模式或长期后台任务。

这样可以把问题限制在单个环节内排查，避免第一次运行就同时遇到下载限流、模型下载、LLM 认证、ffmpeg 编码、B站风控等多个问题。
