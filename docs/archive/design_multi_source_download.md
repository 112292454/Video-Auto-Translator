# 多源视频下载设计文档

> **实现状态**：✅ **已全部实现**（2026-03-02）
> 
> 本文档中描述的所有功能（Step 1–7）均已实现并通过集成测试。
> 包括：三个下载器（YouTubeDownloader / LocalImporter / DirectURLDownloader）、
> executor 线性化重构、CLI 多源支持、上传兼容性、WebUI 四 Tab 添加视频弹窗。
> 
> 已验证的测试场景：LOCAL 端到端 pipeline、DIRECT_URL 端到端 pipeline、
> YouTube 无回归、WebUI API 添加视频（三种类型）、文件上传端点、
> LOCAL 视频完整 pipeline（download→whisper→split→optimize→translate→embed）。

## 1. 需求概述

### 1.1 背景

VAT 当前的下载阶段仅完整支持 YouTube 一个源。虽然 `SourceType` 枚举已包含 `LOCAL` 和 `BILIBILI`，但 `LOCAL` 仅做文件存在性检查（无 metadata 提取），`BILIBILI` 未实现下载。

实际使用场景远比 YouTube 广泛：用户可能从任意平台手动下载视频、自己录制/拍摄、或拥有一个裸视频文件/直链——只要有文件即可走完翻译流水线。

### 1.2 核心思路

**LOCAL/直链是基础通用模式，YouTube 是其增强版。**

- **基础模式**（LOCAL / DIRECT_URL）：只需一个文件或裸链接即可处理。无需任何平台信息，缺失的 metadata 优雅降级。
- **增强模式**（YouTube 等平台）：在基础模式之上，自带 playlist 管理、视频信息、主播名称、可校验的原始 URL 等。

### 1.3 功能范围

| 功能 | 描述 |
|---|---|
| 本地文件导入 | 指定本地视频文件路径，走完整 pipeline（含 upload） |
| 直链下载 | 指定 HTTP/HTTPS 视频直链，下载后走完整 pipeline |
| 自动源类型检测 | CLI 统一用 `--url` 参数，内部自动判断是本地文件、YouTube 还是直链 |
| metadata 优雅降级 | 非平台源缺少标题/描述/作者等信息时，跳过对应处理步骤并记录日志 |
| 内容哈希 ID | 本地文件基于文件内容生成 ID（非路径），同文件不同路径产生相同 ID |

### 1.4 不在范围内

- Bilibili 下载器实现（未来扩展）
- Upload 阶段的多平台支持（当前只上传 B 站，不在本次改动范围）

---

## 2. 现状分析

### 2.1 现有架构

```
vat/downloaders/
├── base.py           # BaseDownloader 抽象基类
└── youtube.py        # YouTubeDownloader 实现

vat/pipeline/executor.py
├── _run_download()   # 按 source_type 分支处理
├── _find_video_file()# LOCAL 特殊分支
└── create_video_from_url()  # 创建视频记录，ID = md5(url)
```

### 2.2 当前 BaseDownloader 接口

```python
class BaseDownloader(ABC):
    @abstractmethod
    def download(self, url, output_dir) -> Dict[str, Any]
    @abstractmethod
    def get_playlist_urls(self, playlist_url) -> List[str]
    @abstractmethod
    def validate_url(self, url) -> bool
    @abstractmethod
    def extract_video_id(self, url) -> Optional[str]
```

**问题**：所有方法都是 abstract，但 `get_playlist_urls` 只对 YouTube 有意义。LocalImporter 和 DirectURLDownloader 无法合理实现该方法——要么抛 NotImplementedError（违反 LSP），要么返回空（掩盖误调用）。

### 2.3 当前 _run_download 结构

```python
def _run_download(self) -> bool:
    if self.video.source_type == SourceType.LOCAL:
        # 5 行：检查文件存在，return True
        # 无 metadata 提取，无 ffprobe，无场景识别
    
    if self.video.source_type == SourceType.YOUTUBE:
        # ~190 行：下载 + metadata + 字幕 + 场景识别 + 视频信息翻译 + 封面下载
    
    raise DownloadError("不支持的视频来源类型")
```

**问题**：
1. LOCAL 分支过于简陋——无 ffprobe、无 metadata、无 title
2. 两个分支代码结构完全不同，新增源类型需要加更多 if/elif
3. 场景识别、视频信息翻译等"后处理"逻辑嵌入在 YOUTUBE 分支内，其他源类型无法复用

### 2.4 当前 Video ID 生成

```python
# create_video_from_url
video_id = hashlib.md5(url.encode()).hexdigest()[:16]
```

对 LOCAL 文件，路径变化会产生不同 ID——同一文件移动位置后被视为新视频。

### 2.5 上传阶段对 metadata 的依赖

`_run_upload` 通过 `render_upload_metadata()` 渲染上传标题/描述，依赖：
- `metadata['translated']['title_translated']` — 翻译后标题（**必需**，< 5 字符会报错）
- `metadata['translated']['description_translated']` — 翻译后描述
- `metadata['uploader']` / `metadata['channel_id']` — 频道信息
- `metadata['thumbnail']` — 封面 URL

`template.py` 中 `source_url` 有 YouTube URL 硬编码 fallback：
```python
'source_url': video_record.source_url or f"https://www.youtube.com/watch?v={video_id}",
```

---

## 3. 设计方案

### 3.1 SourceType 枚举扩展

```python
class SourceType(Enum):
    YOUTUBE = "youtube"
    LOCAL = "local"
    BILIBILI = "bilibili"
    DIRECT_URL = "direct_url"   # 新增
```

**DIRECT_URL 独立于 LOCAL 的理由**：
- **可追溯**：`source_url` 保留原始下载链接，便于溯源和重新下载
- **行为差异**：DIRECT_URL 需要 HTTP 下载阶段，LOCAL 直接读取本地文件
- **下载后等价**：下载完成后，后续所有阶段行为与 LOCAL 完全一致
- **UI 区分**：用户在界面上看到 `direct_url` 标签，知道这是从链接下载的

### 3.2 下载器接口：两层层级设计

```
BaseDownloader (抽象)
├── LocalImporter             # 本地文件导入
├── DirectURLDownloader       # HTTP 直链下载
└── PlatformDownloader (抽象) # 平台下载器
    └── YouTubeDownloader     # YouTube 下载
    └── (未来) BilibiliDownloader
```

#### 3.2.1 BaseDownloader（基础层）

所有下载器的通用接口，定义最小契约：

```python
class BaseDownloader(ABC):
    """基础下载器接口 — 所有源类型都实现"""
    
    @abstractmethod
    def download(self, source: str, output_dir: Path, **kwargs) -> Dict[str, Any]:
        """下载/导入视频到 output_dir
        
        Returns:
            标准化结果字典（见 3.4 节）
        """
    
    @abstractmethod
    def validate_source(self, source: str) -> bool:
        """验证源是否有效（路径存在 / URL 格式正确）"""
    
    @abstractmethod
    def extract_video_id(self, source: str) -> str:
        """从源提取/生成稳定的视频 ID"""
    
    @property
    @abstractmethod
    def guaranteed_fields(self) -> set:
        """该下载器保证在 download() 返回值中提供的字段集合
        
        executor 会断言这些字段存在且非空。
        缺失 = 下载器 bug，应 fail-fast。
        """
    
    @staticmethod
    def probe_video_metadata(video_path: Path) -> Dict[str, Any]:
        """通过 ffprobe 提取视频元数据（共享工具方法）
        
        返回 duration, size, video/audio codec 等信息。
        复用已有的 FFmpegWrapper.get_video_info 逻辑。
        """
```

#### 3.2.2 PlatformDownloader（平台层）

在 BaseDownloader 基础上，增加平台特有能力：

```python
class PlatformDownloader(BaseDownloader):
    """平台下载器接口 — 有平台概念的源（YouTube, Bilibili 等）"""
    
    @abstractmethod
    def get_playlist_urls(self, playlist_url: str) -> List[str]:
        """获取播放列表中的所有视频 URL"""
    
    # 未来可扩展：
    # def get_channel_info(self, url: str) -> Dict
    # def get_video_comments(self, url: str) -> List
```

#### 3.2.3 调用侧类型约束

| 调用场景 | 需要的接口 | 说明 |
|---|---|---|
| `executor._run_download` | `BaseDownloader` | 只需 download() |
| `create_video_from_source` | `BaseDownloader` | 只需 extract_video_id() |
| CLI `pipeline --playlist` | `PlatformDownloader` | 需要 get_playlist_urls() |
| `playlist_service.sync` | `PlatformDownloader` | 需要 get_playlist_urls() |

### 3.3 Video ID 策略

| SourceType | ID 生成方式 | 理由 |
|---|---|---|
| **YOUTUBE** | `md5(url)[:16]` | 不变。URL 天然稳定标识 |
| **DIRECT_URL** | `md5(url)[:16]` | URL 是稳定标识，创建记录时尚未下载内容 |
| **LOCAL** | `md5(文件前1MB + file_size)[:16]` | 基于内容。同文件不同路径 → 同 ID |
| **BILIBILI** | `md5(url)[:16]` | 不变 |

**LOCAL 的 content hash 实现**：

```python
def _generate_content_based_id(file_path: Path) -> str:
    """基于文件内容生成稳定的视频 ID
    
    读取文件前 1MB + 文件大小作为哈希输入。
    - 前 1MB：区分不同视频文件
    - 文件大小：防止前 1MB 相同但长度不同的文件碰撞
    """
    hasher = hashlib.md5()
    with open(file_path, 'rb') as f:
        hasher.update(f.read(1024 * 1024))
    hasher.update(str(file_path.stat().st_size).encode())
    return hasher.hexdigest()[:16]
```

### 3.4 download() 返回值标准化

所有下载器的 `download()` 方法返回统一结构：

```python
{
    'video_path': Path,               # output_dir 内的视频文件路径（必须）
    'title': str,                     # 视频标题（平台源必须，LOCAL 可为空）
    'subtitles': Dict[str, Path],     # {lang: path}，无字幕则为空 dict
    'metadata': {
        # 核心字段（所有源都应尽量提供）
        'duration': float,            # 时长（秒），ffprobe 提取
        'url': str,                   # 原始 source URL / 路径
        
        # 平台字段（非平台源为空字符串）
        'video_id': str,              # 平台视频 ID
        'description': str,           # 视频描述
        'uploader': str,              # 上传者/频道名
        'upload_date': str,           # 上传日期 YYYYMMDD
        'thumbnail': str,             # 封面 URL
        'channel_id': str,            # 频道 ID
        
        # 字幕信息（YouTube 特有）
        'available_subtitles': list,      # 可用人工字幕语言
        'available_auto_subtitles': list, # 可用自动字幕语言
    }
}
```

### 3.5 guaranteed_fields 契约机制

每个下载器声明自己保证返回的字段。executor 在 download() 后断言这些字段非空：
- 保证字段缺失 = 下载器 bug → **fail-fast**
- 非保证字段缺失 = 正常情况 → **跳过对应步骤并 log**

| 下载器 | guaranteed_fields |
|---|---|
| **YouTubeDownloader** | `{'title', 'duration', 'description', 'uploader', 'thumbnail'}` |
| **LocalImporter** | `{'duration'}` |
| **DirectURLDownloader** | `{'duration'}` |

**executor 中的断言逻辑**：

```python
def _run_download(self) -> bool:
    result = self.downloader.download(self.video.source_url, self.output_dir, ...)
    
    # 断言下载器的数据承诺
    metadata = result.get('metadata', {})
    for field in self.downloader.guaranteed_fields:
        value = result.get(field) or metadata.get(field)
        assert value is not None and value != '', (
            f"[数据契约违反] {type(self.downloader).__name__} 保证返回 '{field}' "
            f"但实际为空/缺失。这是下载器实现 bug，请检查。"
        )
    
    # 后续步骤按数据可用性执行（不按 source_type 分支）
    title = result.get('title', '')
    ...
```

**效果**：
- YouTube 下载后缺 title → 断言失败 → 立刻报错（因为 `'title'` 在 guaranteed_fields 中）
- LOCAL 导入后无 title → 不触发断言 → 正常跳过场景识别并 log

### 3.6 output_dir 统一规范化

**所有源类型**在 download 阶段结束后，output_dir 内都有 `original.{ext}` 文件：

| SourceType | download 行为 | output_dir 结果 |
|---|---|---|
| **YOUTUBE** | yt-dlp 下载到 output_dir | `{yt_video_id}.mp4`（现有行为，后续 `_find_video_file` 已兼容） |
| **LOCAL** | 在 output_dir 创建软链接 | `original.{ext} → /原始/路径/video.mp4` |
| **DIRECT_URL** | HTTP 下载到 output_dir | `original.{ext}` |

> **注**：YouTube 的文件命名 `{yt_video_id}.{ext}` 是 yt-dlp 的行为，不做改动。`_find_video_file` 已有兼容逻辑（先找 `original.*`，再找其他视频文件）。

**LOCAL 软链接的意义**：
- output_dir 结构一致，后续阶段不需要 source_type 特殊分支
- 不复制文件（视频可能几 GB），几乎零磁盘开销
- `_find_video_file` 可简化：统一在 output_dir 查找，不再需要 LOCAL 特殊路径返回

### 3.7 _run_download 线性化重构

改造前（分支结构）：
```
_run_download:
  if LOCAL:    5 行简单检查
  if YOUTUBE:  190 行（下载+metadata+字幕+场景+翻译+封面）
  raise 不支持
```

改造后（线性结构）：
```
_run_download:
  1. result = self.downloader.download(...)           # 委托给下载器
  2. 断言 guaranteed_fields                            # 契约验证
  3. 提取 title, metadata, subtitles                   # 统一处理
  4. if subtitles: 处理字幕信息                         # YouTube 有，其他为空
  5. 决定 subtitle_source (manual/auto/asr)            # 按数据可用性
  6. if title: 场景识别                                 # 有标题就做
  7. if title: 视频信息翻译                             # 有标题就做
  8. if thumbnail_url: 下载封面                         # 有封面就下
  9. 更新 DB                                           # 统一保存
```

**关键改变**：
- 每个步骤的执行条件是**数据是否存在**，而非 source_type
- guaranteed_fields 保证平台源不会因 bug 而静默跳过步骤
- 新增源类型不需要修改 _run_download——只需实现新的下载器

### 3.8 上传阶段兼容性改造

#### 3.8.1 template.py 修复

当前硬编码 YouTube URL fallback：
```python
# 当前
'source_url': video_record.source_url or f"https://www.youtube.com/watch?v={video_id}",
```

改为：
```python
# 改后
'source_url': video_record.source_url or '',
```

#### 3.8.2 上传标题 fallback 链

当前逻辑：`rendered_title < 5 字符 → raise UploadError`。

对非平台源，如果没有执行视频信息翻译（无 title），`translated_title` 为空，渲染结果可能只剩模板骨架。

**改造方案**：在 `_run_upload` 中增加 fallback 链：

```python
# 上传标题 fallback：翻译标题 → 原始标题 → 文件名 → video_id
rendered_title = rendered['title']
if not rendered_title or len(rendered_title.strip()) < 5:
    # 尝试 fallback
    fallback_title = (
        self.video.title                           # 原始标题（从 filename 提取或用户手动指定）
        or Path(self.video.source_url).stem         # 文件名 stem
        or self.video.id                            # video_id
    )
    if fallback_title and len(fallback_title.strip()) >= 5:
        rendered_title = fallback_title
        self.progress_callback(f"使用 fallback 上传标题: {rendered_title}")
    else:
        raise UploadError("上传标题过短且无可用 fallback...")
```

### 3.9 源类型自动检测

```python
def detect_source_type(source: str) -> SourceType:
    """根据输入自动检测视频源类型
    
    检测优先级：
    1. 本地文件路径（路径存在）→ LOCAL
    2. YouTube URL 模式匹配 → YOUTUBE
    3. HTTP/HTTPS URL → DIRECT_URL
    4. 其他 → 报错
    """
    # 1. 本地文件
    source_path = Path(source)
    if source_path.exists() and source_path.is_file():
        return SourceType.LOCAL
    
    # 2. YouTube URL
    youtube_patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/',
        r'(?:https?://)?youtu\.be/',
    ]
    for pattern in youtube_patterns:
        if re.match(pattern, source):
            return SourceType.YOUTUBE
    
    # 3. HTTP/HTTPS 直链
    if source.startswith(('http://', 'https://')):
        return SourceType.DIRECT_URL
    
    raise ValueError(
        f"无法识别的视频源: {source}\n"
        f"支持的格式: 本地文件路径 / YouTube URL / HTTP(S) 直链"
    )
```

### 3.10 CLI 入口改造

#### 3.10.1 pipeline 命令

```python
@cli.command()
@click.option('--url', '-u', multiple=True, help='视频源（本地路径/YouTube URL/直链）')
@click.option('--playlist', '-p', help='YouTube 播放列表 URL')
@click.option('--file', '-f', type=click.Path(exists=True), help='源列表文件（每行一个）')
@click.option('--title', '-t', help='手动指定视频标题（仅对单个视频有效）')
@click.option('--gpus', help='使用的GPU列表')
@click.option('--force', is_flag=True, help='强制重新处理')
```

**变化**：
- `--url` 不再仅限 YouTube URL，接受任意源（路径/URL），内部自动检测类型
- 新增 `--title` 参数，可手动指定标题（对 LOCAL/DIRECT_URL 有用）
- `--file` 读取文件每行作为源，通过 `detect_source_type` 自动检测
- `--playlist` 仅对 YouTube 有效（需要 PlatformDownloader 接口）

#### 3.10.2 create_video_from_source（替代 create_video_from_url）

```python
def create_video_from_source(
    source: str,
    db: Database,
    source_type: SourceType,
    title: str = "",
) -> str:
    """从任意源创建视频记录
    
    Args:
        source: 视频源（URL 或本地文件路径）
        db: 数据库实例
        source_type: 来源类型
        title: 可选手动标题
    
    Returns:
        视频 ID
    """
    # ID 生成
    if source_type == SourceType.LOCAL:
        video_id = _generate_content_based_id(Path(source))
    else:
        video_id = hashlib.md5(source.encode()).hexdigest()[:16]
    
    # 创建 Video 记录
    video = Video(
        id=video_id,
        source_type=source_type,
        source_url=source,
        title=title or None,
    )
    
    # ... 清理旧任务 + 插入 DB（同现有逻辑）
```

> `create_video_from_url` 保留为兼容别名，内部调用 `create_video_from_source`。

### 3.11 WebUI 改造

#### 3.11.1 添加视频弹窗重设计

当前「添加视频」弹窗仅有一个 URL 输入框，只支持 YouTube/Bilibili 链接。改造为支持四种添加方式：

| 方式 | 说明 | source_type |
|---|---|---|
| **平台链接** | YouTube / Bilibili 视频 URL | 自动检测（youtube / bilibili） |
| **直链** | HTTP/HTTPS 视频文件直链 | `direct_url` |
| **服务器路径** | 本机视频文件绝对路径 | `local` |
| **上传视频** | 浏览器上传视频文件到服务器 | `local`（上传后转为服务器路径） |

**UI 设计**：使用 Tab 切换四种模式

```
┌─ 添加视频 ──────────────────────────────────┐
│                                               │
│  [平台链接] [直链] [服务器路径] [上传视频]      │
│  ─────────────────────────────────────────── │
│                                               │
│  (根据 Tab 显示不同输入区域)                    │
│                                               │
│  标题（可选）: [________________]               │
│  提示：不填则自动从文件名/URL推导               │
│                                               │
│                     [取消]  [添加]             │
└───────────────────────────────────────────────┘
```

**Tab 1: 平台链接**（默认）
- 输入框 placeholder: "YouTube 或 Bilibili 视频链接"
- 行为与当前一致，后端自动检测 source_type

**Tab 2: 直链**
- 输入框 placeholder: "HTTP/HTTPS 视频文件直链"
- 提示文字: "直接指向视频文件的下载链接（如 .mp4, .mkv）"

**Tab 3: 服务器路径**
- 输入框 placeholder: "/path/to/video.mp4"
- 提示文字: "VAT 服务器上的视频文件绝对路径"

**Tab 4: 上传视频**
- 拖拽上传区域 + 点击选择文件按钮
- 支持格式: mp4, mkv, webm, avi, mov, flv
- 上传进度条
- 上传完成后自动填入服务器路径，走 LOCAL 流程
- 提示文字: "上传视频文件到服务器（文件将保存到数据目录）"

**标题输入**（所有 Tab 共用）
- 可选字段，不填则自动推导
- 对平台链接无意义（YouTube 自带标题），但不阻止用户手动覆盖

#### 3.11.2 后端 API 改造

**AddVideoRequest 扩展**：

```python
class AddVideoRequest(BaseModel):
    """添加视频请求"""
    url: str                          # 视频源（URL 或本地路径）
    source_type: str = "auto"         # 默认改为 auto（自动检测）
    title: Optional[str] = None       # 可选手动标题
```

**add_video API 改造**：

```python
@router.post("")
async def add_video(request: AddVideoRequest, db: Database = Depends(get_db)):
    if request.source_type == "auto":
        source_type = detect_source_type(request.url)
    else:
        source_type = SourceType(request.source_type)
    
    video_id = create_video_from_source(
        request.url, db, source_type, title=request.title or ""
    )
    return {"video_id": video_id, "status": "created"}
```

**新增文件上传 API**：

```python
@router.post("/upload")
async def upload_video(file: UploadFile = File(...), db: Database = Depends(get_db)):
    """上传视频文件到服务器数据目录
    
    上传后保存到 {storage.output_dir}/_uploads/{timestamp}_{filename}，
    返回服务器路径供前端创建 LOCAL 类型视频记录。
    """
    config = load_config()
    upload_dir = Path(config.storage.output_dir) / "_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # 带时间戳避免同名冲突
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = f"{timestamp}_{file.filename}"
    target_path = upload_dir / safe_name
    
    # 流式写入（避免内存爆炸）
    with open(target_path, "wb") as f:
        while chunk := await file.read(8192):
            f.write(chunk)
    
    return {
        "status": "uploaded",
        "server_path": str(target_path),
        "filename": file.filename,
        "size": target_path.stat().st_size,
    }
```

#### 3.11.3 source_type 标签样式

当前 `index.html` 对 `youtube` 显示红色、`bilibili` 显示粉色，其他为灰色。新增：

```html
{% if video.source_type == 'youtube' %}bg-red-100 text-red-800
{% elif video.source_type == 'bilibili' %}bg-pink-100 text-pink-800
{% elif video.source_type == 'local' %}bg-green-100 text-green-800
{% elif video.source_type == 'direct_url' %}bg-blue-100 text-blue-800
{% else %}bg-gray-100 text-gray-800{% endif %}
```

---

## 4. 三个下载器的详细设计

### 4.1 LocalImporter

**文件**：`vat/downloaders/local.py`

**职责**：验证本地文件 → 在 output_dir 创建软链接 → ffprobe 提取 metadata → 返回标准化结果

```python
class LocalImporter(BaseDownloader):
    """本地视频文件导入器
    
    不执行下载，仅：
    1. 验证文件存在且为支持的视频格式
    2. 在 output_dir 创建软链接（统一 output_dir 结构）
    3. 通过 ffprobe 提取视频元数据
    4. 从文件名推导标题（如果未手动指定）
    """
    
    SUPPORTED_EXTENSIONS = {'.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', '.ts', '.m4v'}
    
    @property
    def guaranteed_fields(self) -> set:
        return {'duration'}
    
    def download(self, source: str, output_dir: Path, **kwargs) -> Dict[str, Any]:
        """导入本地视频文件
        
        Args:
            source: 本地视频文件绝对路径
            output_dir: 输出目录
            **kwargs:
                title: 手动指定标题（可选）
        """
        source_path = Path(source)
        
        # 验证
        assert source_path.exists(), f"本地视频文件不存在: {source}"
        assert source_path.is_file(), f"路径不是文件: {source}"
        assert source_path.suffix.lower() in self.SUPPORTED_EXTENSIONS, (
            f"不支持的视频格式: {source_path.suffix}，"
            f"支持: {self.SUPPORTED_EXTENSIONS}"
        )
        
        # 在 output_dir 创建软链接
        output_dir.mkdir(parents=True, exist_ok=True)
        link_name = f"original{source_path.suffix.lower()}"
        link_path = output_dir / link_name
        if link_path.exists() or link_path.is_symlink():
            link_path.unlink()
        link_path.symlink_to(source_path.resolve())
        
        # ffprobe 提取 metadata
        probe_data = self.probe_video_metadata(source_path)
        duration = probe_data.get('duration', 0) if probe_data else 0
        
        # 标题：手动指定 > 文件名 stem
        title = kwargs.get('title', '') or source_path.stem
        
        return {
            'video_path': link_path,
            'title': title,
            'subtitles': {},
            'metadata': {
                'duration': duration,
                'url': str(source_path.resolve()),
                'video_id': '',
                'description': '',
                'uploader': '',
                'upload_date': '',
                'thumbnail': '',
                'channel_id': '',
                'subtitle_source': 'asr',
                'available_subtitles': [],
                'available_auto_subtitles': [],
            }
        }
    
    def validate_source(self, source: str) -> bool:
        p = Path(source)
        return p.exists() and p.is_file() and p.suffix.lower() in self.SUPPORTED_EXTENSIONS
    
    def extract_video_id(self, source: str) -> str:
        return _generate_content_based_id(Path(source))
```

### 4.2 DirectURLDownloader

**文件**：`vat/downloaders/direct_url.py`

**职责**：HTTP 下载视频文件 → ffprobe 提取 metadata → 返回标准化结果

```python
class DirectURLDownloader(BaseDownloader):
    """HTTP/HTTPS 直链视频下载器
    
    支持：
    - 流式下载（避免内存爆炸）
    - 从 Content-Disposition 或 URL 推导文件名
    - 进度回调
    - 代理支持
    """
    
    def __init__(self, proxy: str = "", timeout: int = 300):
        self.proxy = proxy
        self.timeout = timeout
    
    @property
    def guaranteed_fields(self) -> set:
        return {'duration'}
    
    def download(self, source: str, output_dir: Path, **kwargs) -> Dict[str, Any]:
        """从直链下载视频
        
        Args:
            source: HTTP/HTTPS 视频 URL
            output_dir: 输出目录
            **kwargs:
                title: 手动指定标题（可选）
                progress_callback: 进度回调（可选）
        """
        import requests
        from urllib.parse import urlparse, unquote
        
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 流式下载
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        resp = requests.get(source, stream=True, timeout=self.timeout, proxies=proxies)
        resp.raise_for_status()
        
        # 推导文件扩展名
        ext = self._guess_extension(source, resp)
        output_path = output_dir / f"original{ext}"
        
        # 写入文件
        total_size = int(resp.headers.get('content-length', 0))
        downloaded = 0
        progress_callback = kwargs.get('progress_callback')
        
        with open(output_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback and total_size:
                    pct = downloaded / total_size * 100
                    progress_callback(f"下载进度: {pct:.1f}%")
        
        assert output_path.stat().st_size > 0, f"下载的文件大小为 0: {source}"
        
        # ffprobe 提取 metadata
        probe_data = self.probe_video_metadata(output_path)
        duration = probe_data.get('duration', 0) if probe_data else 0
        
        # 标题：手动指定 > URL 文件名
        title = kwargs.get('title', '') or self._title_from_url(source)
        
        return {
            'video_path': output_path,
            'title': title,
            'subtitles': {},
            'metadata': {
                'duration': duration,
                'url': source,
                'video_id': '',
                'description': '',
                'uploader': '',
                'upload_date': '',
                'thumbnail': '',
                'channel_id': '',
                'subtitle_source': 'asr',
                'available_subtitles': [],
                'available_auto_subtitles': [],
            }
        }
    
    def validate_source(self, source: str) -> bool:
        return source.startswith(('http://', 'https://'))
    
    def extract_video_id(self, source: str) -> str:
        return hashlib.md5(source.encode()).hexdigest()[:16]
    
    def _guess_extension(self, url: str, resp) -> str:
        """从 URL 路径或 Content-Type 推导文件扩展名"""
        # 优先从 URL 路径
        from urllib.parse import urlparse
        path = urlparse(url).path
        ext = Path(path).suffix.lower()
        if ext in {'.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', '.ts'}:
            return ext
        
        # 从 Content-Type
        content_type = resp.headers.get('content-type', '')
        type_map = {
            'video/mp4': '.mp4',
            'video/webm': '.webm',
            'video/x-matroska': '.mkv',
            'video/quicktime': '.mov',
            'video/x-flv': '.flv',
        }
        for ct, ext in type_map.items():
            if ct in content_type:
                return ext
        
        return '.mp4'  # 默认
    
    def _title_from_url(self, url: str) -> str:
        """从 URL 推导标题"""
        from urllib.parse import urlparse, unquote
        path = urlparse(url).path
        filename = unquote(Path(path).stem)
        return filename if filename and filename != '/' else ''
```

### 4.3 YouTubeDownloader 适配

**文件**：`vat/downloaders/youtube.py`（已有，需适配）

**改动**：
1. 继承从 `BaseDownloader` 改为 `PlatformDownloader`
2. `validate_url` 重命名为 `validate_source`
3. 新增 `guaranteed_fields` 属性
4. `download()` 返回值结构不变（已符合标准化格式）

```python
class YouTubeDownloader(PlatformDownloader):
    
    @property
    def guaranteed_fields(self) -> set:
        return {'title', 'duration', 'description', 'uploader', 'thumbnail'}
    
    def validate_source(self, source: str) -> bool:
        return self.validate_url(source)  # 内部兼容
    
    # download(), get_playlist_urls(), extract_video_id() 不变
    # get_video_info(), get_playlist_info() 等 YouTube 特有方法保留
```

---

## 5. executor.py _run_download 改造详细设计

### 5.1 改造前后对比

**改造前**（~200 行，两个大分支）：
```python
def _run_download(self) -> bool:
    if self.video.source_type == SourceType.LOCAL:
        # 5 行
        return True
    
    if self.video.source_type == SourceType.YOUTUBE:
        # ~190 行 YouTube 特有逻辑
        return True
    
    raise DownloadError("不支持")
```

**改造后**（~120 行，线性流程）：
```python
def _run_download(self) -> bool:
    # Step 1: 委托下载器执行下载/导入
    result = self._execute_download()
    
    # Step 2: 验证下载器契约
    self._validate_download_contract(result)
    
    # Step 3: 提取核心数据
    title, metadata, subtitles = self._extract_download_result(result)
    
    # Step 4: 处理字幕信息（按数据可用性）
    self._process_subtitle_info(metadata, subtitles)
    
    # Step 5: 场景识别（需要 title）
    if title:
        self._run_scene_detection(title, metadata)
    else:
        self.progress_callback("无标题信息，跳过场景识别")
    
    # Step 6: 视频信息翻译（需要 title）
    if title:
        self._run_video_info_translation(title, metadata)
    else:
        self.progress_callback("无标题信息，跳过视频信息翻译")
    
    # Step 7: 下载封面（需要 thumbnail URL）
    thumbnail_url = metadata.get('thumbnail', '')
    if thumbnail_url:
        self._download_thumbnail(thumbnail_url)
    
    # Step 8: 更新 DB
    self.db.update_video(self.video_id, title=title, metadata=metadata)
    return True
```

### 5.2 辅助方法

```python
def _execute_download(self) -> Dict[str, Any]:
    """执行下载/导入，委托给对应的下载器"""
    self._progress_with_tracker(f"开始处理视频: {self.video.source_url}")
    
    # 构建下载参数
    download_kwargs = {}
    if self.video.title:
        download_kwargs['title'] = self.video.title
    download_kwargs['progress_callback'] = self.progress_callback
    
    # YouTube 特有参数
    if self.video.source_type == SourceType.YOUTUBE:
        yt_config = self.config.downloader.youtube
        download_kwargs['download_subs'] = yt_config.download_subtitles
        download_kwargs['sub_langs'] = yt_config.subtitle_languages
    
    try:
        result = self.downloader.download(
            self.video.source_url,
            self.output_dir,
            **download_kwargs
        )
        
        if 'video_path' not in result:
            raise DownloadError("下载器未返回视频路径")
        
        video_path = Path(result['video_path'])
        if not video_path.exists():
            raise DownloadError(f"下载/导入后文件不存在: {video_path}")
        
        if self._progress_tracker:
            self._progress_tracker.report_event(ProgressEvent.DOWNLOAD_VIDEO_DONE, "视频下载完成")
        
        return result
        
    except Exception as e:
        raise DownloadError(f"下载失败: {e}", original_error=e)


def _validate_download_contract(self, result: Dict[str, Any]) -> None:
    """验证下载器返回数据满足 guaranteed_fields 契约"""
    metadata = result.get('metadata', {})
    for field in self.downloader.guaranteed_fields:
        value = result.get(field) or metadata.get(field)
        if value is None or value == '' or value == 0:
            raise DownloadError(
                f"[数据契约违反] {type(self.downloader).__name__} 保证返回 '{field}' "
                f"但实际为空/缺失/零值。这是下载器实现 bug。"
            )
```

### 5.3 下载器初始化

当前 executor 在 `__init__` 或延迟初始化中硬编码创建 `YouTubeDownloader`。改为根据 `source_type` 选择：

```python
def _init_downloader(self) -> BaseDownloader:
    """根据 source_type 初始化对应的下载器"""
    if self.video.source_type == SourceType.YOUTUBE:
        return YouTubeDownloader(
            proxy=self.config.get_stage_proxy("downloader"),
            video_format=self.config.downloader.youtube.format,
            cookies_file=self.config.downloader.youtube.cookies_file,
            remote_components=self.config.downloader.youtube.remote_components,
        )
    elif self.video.source_type == SourceType.LOCAL:
        return LocalImporter()
    elif self.video.source_type == SourceType.DIRECT_URL:
        return DirectURLDownloader(
            proxy=self.config.get_stage_proxy("downloader"),
        )
    else:
        raise ValueError(f"不支持的视频来源类型: {self.video.source_type}")
```

---

## 6. 涉及文件清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `vat/models.py` | **修改** | 新增 `SourceType.DIRECT_URL` |
| `vat/downloaders/base.py` | **重写** | 两层接口：BaseDownloader + PlatformDownloader + probe_video_metadata |
| `vat/downloaders/local.py` | **新建** | LocalImporter 实现 |
| `vat/downloaders/direct_url.py` | **新建** | DirectURLDownloader 实现 |
| `vat/downloaders/youtube.py` | **修改** | 适配 PlatformDownloader + guaranteed_fields + validate_source |
| `vat/downloaders/__init__.py` | **修改** | 导出新类 |
| `vat/pipeline/executor.py` | **修改** | _run_download 线性化 + _init_downloader + create_video_from_source + detect_source_type |
| `vat/cli/commands.py` | **修改** | pipeline 命令支持自动检测源类型 + --title 参数 |
| `vat/uploaders/template.py` | **修改** | 移除 YouTube URL 硬编码 fallback |
| `vat/web/routes/videos.py` | **修改** | AddVideoRequest 扩展 + auto 检测 + upload API |
| `vat/web/templates/index.html` | **修改** | 添加视频弹窗重写（四种模式 Tab + 上传 + 标题）+ source_type 标签样式 |

---

## 7. 实现步骤

按提交粒度排列，每步可独立测试：

### Step 1: 基础接口层（无功能变化）
1. `models.py`：新增 `SourceType.DIRECT_URL`
2. `downloaders/base.py`：重写为 BaseDownloader + PlatformDownloader 两层接口
3. `downloaders/youtube.py`：适配新接口（继承 PlatformDownloader, 新增 guaranteed_fields, validate_source）
4. 验证：现有 YouTube 流程不受影响

### Step 2: LocalImporter 实现
1. `downloaders/local.py`：完整实现 LocalImporter
2. `downloaders/__init__.py`：导出
3. 验证：单元测试——验证软链接创建、ffprobe 提取、content hash ID

### Step 3: DirectURLDownloader 实现
1. `downloaders/direct_url.py`：完整实现 DirectURLDownloader
2. `downloaders/__init__.py`：导出
3. 验证：单元测试——验证 HTTP 下载、扩展名推导、URL hash ID

### Step 4: executor.py 改造
1. `_run_download` 线性化重构
2. `_init_downloader` 按 source_type 选择下载器
3. `create_video_from_source` 新函数 + `create_video_from_url` 改为兼容别名
4. `detect_source_type` 工具函数
5. `_find_video_file` 简化（移除 LOCAL 特殊分支，统一从 output_dir 查找）
6. 验证：用现有 YouTube 视频跑完整 pipeline 确认无回归

### Step 5: CLI 入口改造
1. `commands.py`：pipeline 命令支持 --url 自动检测 + --title
2. 验证：`vat pipeline --url /path/to/local.mp4` 能创建视频记录并执行 pipeline

### Step 6: 上传兼容性
1. `template.py`：移除 YouTube URL 硬编码 fallback
2. `executor.py _run_upload`：标题 fallback 链
3. 验证：非 YouTube 视频上传时标题不为空

### Step 7: WebUI 全面改造
1. `videos.py`：AddVideoRequest 增加 title 字段 + source_type 默认改为 auto + 新增 upload API
2. `index.html`：重写添加视频弹窗（Tab 切换四种模式 + 文件上传 + 标题输入）
3. `index.html`：source_type 标签样式（local 绿色、direct_url 蓝色）
4. 验证：通过前端分别测试四种添加方式

---

## 8. 测试计划

### 8.1 单元测试

| 测试目标 | 测试内容 |
|---|---|
| `LocalImporter.download` | 软链接创建、ffprobe metadata、title 从文件名推导 |
| `LocalImporter.extract_video_id` | 同文件不同路径 → 同 ID；不同文件 → 不同 ID |
| `LocalImporter.validate_source` | 文件存在/不存在/不支持的格式 |
| `DirectURLDownloader.download` | HTTP 下载、扩展名推导、文件大小校验 |
| `DirectURLDownloader._guess_extension` | 从 URL / Content-Type 推导 |
| `detect_source_type` | 本地路径/YouTube URL/直链/无效输入 |
| `create_video_from_source` | LOCAL content hash / URL hash / 重复创建 |
| `guaranteed_fields 断言` | YouTube 缺 title → fail；LOCAL 缺 title → pass |

### 8.2 集成测试

| 测试场景 | 步骤 |
|---|---|
| LOCAL 完整 pipeline | `vat pipeline --url /path/to/video.mp4` → download→whisper→split→optimize→translate→embed |
| LOCAL + 手动标题 | `vat pipeline --url /path/to/video.mp4 --title "我的视频"` |
| DIRECT_URL 下载 | `vat pipeline --url "https://example.com/video.mp4"` → 下载 + 后续 pipeline |
| YouTube 无回归 | 现有 YouTube 视频处理流程不受影响 |
| 源类型自动检测 | 同一 --url 参数传入不同类型的源，验证检测正确 |
| WebUI 平台链接添加 | 前端 Tab1 输入 YouTube URL → 创建视频记录 → 跳转详情页 |
| WebUI 直链添加 | 前端 Tab2 输入直链 → source_type=direct_url → 创建成功 |
| WebUI 服务器路径添加 | 前端 Tab3 输入路径 → source_type=local → 创建成功 |
| WebUI 上传视频 | 前端 Tab4 拖拽上传文件 → 上传完成 → 自动创建 LOCAL 记录 |

### 8.3 边界情况

- LOCAL 文件被删除后重新处理 → 报错（软链接断裂 + 明确错误信息）
- DIRECT_URL 404/超时 → 下载失败（明确错误信息）
- LOCAL 重复导入同一文件 → 相同 video_id（content hash 稳定）
- LOCAL 文件名含中文/特殊字符 → 标题正常显示
- 极大文件（>10GB）的 content hash 性能 → 只读前 1MB，可接受

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| 软链接在不同文件系统间不工作 | LOCAL 导入失败 | download 中检测，失败时 fallback 为复制并 warn |
| ffprobe 未安装 | metadata 提取失败 | probe_video_metadata 中捕获异常，返回空 dict + 明确错误提示 |
| content hash 碰撞（概率极低） | 两个不同视频被视为同一个 | 16 字符 hex = 64 bit，实际碰撞概率可忽略 |
| _run_download 线性化可能遗漏 YouTube 逻辑 | YouTube 流程回归 | Step 4 完成后，用现有 YouTube 视频完整跑一遍 pipeline |
| DIRECT_URL 下载大文件时内存/磁盘问题 | 下载失败 | 流式写入（chunk_size=8192），不一次性加载全文件 |
