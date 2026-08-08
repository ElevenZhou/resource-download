# GitHub 调研：视频站「播放/资源链接抓取」+「批量下载服务」

> 调研日期：2026-08-08  
> 目标能力：抓播放页 / 列表中的 **图片 URL、视频 URL（含 m3u8）** → 入库 → **独立下载服务**批量落盘。

## 1. 问题拆两层（几乎所有成熟项目都这么拆）

```text
┌─────────────────────┐     链接清单 / 队列      ┌──────────────────────┐
│  A. 发现 & 解析层    │  ──────────────────►   │  B. 下载执行层         │
│  列表/详情/API/浏览器 │   (URL + 元数据 + 头)   │  HTTP / HLS / DASH    │
│  产出：资源链接表    │                         │  并发、重试、进度      │
└─────────────────────┘                         └──────────────────────┘
```

| 层 | 干什么 | 典型输出 |
|----|--------|----------|
| **A 抓取** | 目录、详情、播放页；解出封面图、直链 mp4、m3u8、DASH | JSON/TSV/DB 队列 |
| **B 下载** | 按链接批量拉文件；HLS 拼片；鉴权头/Referer | 本地目录 + 状态 |

混在一个脚本里短期能跑，**批量 + 失败重试 + 多站点** 时建议队列解耦（本仓库已有 GoodShort 片库模型）。

---

## 2. 推荐开源项目（按职责）

### 2.1 通用视频解析 + 下载（站多、链路成熟）

| 项目 | 仓库 | 适合 |
|------|------|------|
| **yt-dlp** | https://github.com/yt-dlp/yt-dlp | 已知站点 extractor；`-a urls.txt` 批量；HLS 分片并行；`--download-archive` 断点台账 |
| **lux** | https://github.com/iawia002/lux | 多站点 CLI 视频下载（Go，轻量） |
| **you-get** | https://github.com/soimort/you-get | 老牌多站点，覆盖面与 yt-dlp 有重叠 |

**用法启示：** 抓取层只负责「得到可播 URL 或页面 URL」；若 yt-dlp 已支持该站，下载层可直接喂页面 URL。

### 2.2 图库 / 多图多视频集合

| 项目 | 仓库 | 适合 |
|------|------|------|
| **gallery-dl** | https://github.com/mikf/gallery-dl | 图站、画廊、部分视频；元数据；可与 yt-dlp 协作 |
| **gallery-dl-server** | https://github.com/qx6ghqkz/gallery-dl-server | gallery-dl + yt-dlp 的 **Web UI / 服务化** |
| **Media Downloader** | https://github.com/mhogomchungu/media-downloader | Qt 前端聚合 yt-dlp / gallery-dl / lux / aria2 等 |

**用法启示：** Explore/Collection 类「卡片墙」更接近 gallery-dl 模型，而不是纯 m3u8 工具。

### 2.3 纯流媒体链接批量下载（已有 m3u8/mpd）

| 项目 | 仓库 | 适合 |
|------|------|------|
| **N_m3u8DL-RE** | https://github.com/nilaoda/N_m3u8DL-RE | 工业级 HLS/DASH/MSS；点播+直播；多线程；加密流 |
| **lzwme/m3u8-dl** | https://github.com/lzwme/m3u8-dl | 批量 m3u8；CLI / WebUI / API / Docker |
| **ffmpeg** | 系统工具 | 标准 HLS `ffmpeg -i xx.m3u8 -c copy`（本仓库 GoodShort 已用） |

**用法启示：** 抓取层只吐 `m3u8` + Header；下载服务用 N_m3u8DL-RE 或 ffmpeg 执行。

### 2.4 页面嗅探 / 浏览器辅助（无专用 extractor 时）

| 项目/类型 | 说明 |
|-----------|------|
| **Playwright / Puppeteer / CDP** | 打开播放页，拦截 Network 中的 `.m3u8` / `.mp4` / 图片 CDN |
| **Obscura** | 轻量无头浏览器（见 `docs/research/obscura.md`） |
| 浏览器扩展类 m3u8 sniffer | 人工验证链路；批量时改为 headless 复现 |
| **codyklr/M3U8-Scraper** 等 PoC | Selenium + ffmpeg，适合单页验证，不宜直接当生产队列 |

### 2.5 下载器 / 队列基建

| 工具 | 用途 |
|------|------|
| **aria2c** | HTTP(S) 直链高并发（图、mp4） |
| **Motrix** | aria2 图形前端（偏人工） |
| SQLite / Redis 队列 | 自建状态机：pending → downloading → done/failed |

---

## 3. 常见架构模式（GitHub 项目归纳）

### 模式 A：一体化 CLI（yt-dlp / gallery-dl）

```text
urls.txt → yt-dlp/gallery-dl → 本地文件
```

- **优点**：快、成熟、站点适配多  
- **缺点**：目标站无 extractor 时要自己写插件或先抓直链  

### 模式 B：嗅探 + 专用流下载（扩展 / headless + N_m3u8DL-RE）

```text
打开播放页 → 拦截 m3u8 → 写入任务表 → N_m3u8DL-RE / ffmpeg 批量
```

- **优点**：不依赖官方 API 文档  
- **缺点**：登录态、签名 URL、反爬成本高  

### 模式 C：站点 Adapter + 片库 + Worker（本仓库方向）

```text
Catalog Crawler → Library(DB) → Claim Queue → Download Workers → disk
```

- **优点**：可多站点、可进度、可重试、可限流  
- **缺点**：每个站要写 Adapter  

**对本仓库建议：模式 C 为主；下载执行层可外包给 yt-dlp / N_m3u8DL-RE / aria2 / ffmpeg。**

---

## 4. 链接类型与下载策略

| 资源类型 | 识别 | 下载策略 |
|----------|------|----------|
| 图片 jpg/png/webp | `img` / API `cover` / CDN | HTTP 并发（aria2/自写） |
| 视频直链 mp4/webm | API / Network | HTTP 或 aria2 |
| HLS m3u8 | 播放器 / 拦截 | ffmpeg 或 N_m3u8DL-RE |
| DASH mpd | 同上 | N_m3u8DL-RE |
| 加密 / 签名 URL | 带 `exp`/`token` | **下载前刷新**；短 TTL 勿只存旧链 |
| 需 Cookie/Referer | 登录页、防盗链 | 任务带 headers；Worker 注入 |

---

## 5. 合规边界（方案层必须写死）

1. 只处理**账号已有权访问**或站点明确允许获取的公开资源。  
2. 不实现破解付费、绕过 DRM、盗刷积分。  
3. 控制并发与频率，遵守站点 ToS / robots。  
4. 成人 / AI 生成站注意当地法律与内容合规，**仅自用归档**。  

---

## 6. 与本仓库的映射

| 能力 | 本仓库位置 |
|------|------------|
| 多 case 隔离 | `cases/<site>/` |
| 片库 + 状态机 | GoodShort `gsdl/store.py` 可复用模式 |
| HLS 下载 | `downloader.py` + ffmpeg；复杂流可调 N_m3u8DL-RE |
| 浏览器回退 | `docs/research/obscura.md` |
| Playbox 方案 | `cases/playbox/README.md` |
