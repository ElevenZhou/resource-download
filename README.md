# 资源下载 — 爬虫 / 批量获取项目

多目标、多案例的 **目录爬取 + 片库管理 + 批量下载** 仓库。  
每个具体站点 / 渠道是一个 **独立 case 子目录**，互不耦合；公共工具放在 `core/`。

```text
发现目录 → 入库 SQLite → 队列 claim → 并发下载 → 本地媒体 + 台账
```

**GitHub：** https://github.com/ElevenZhou/resource-download

```bash
git clone https://github.com/ElevenZhou/resource-download.git
cd resource-download

# Playbox（图/封面/视频，仅 Python 标准库）
./scripts/playbox crawl --pages 1 --max-items 10
./scripts/playbox download --workers 2

# GoodShort（短剧 HLS，需系统 ffmpeg）
# brew install ffmpeg
./scripts/gsdl --help
```

本地下载与 SQLite **不入库**（见 `.gitignore`），每人 clone 后自行生成。

---

## 目录结构

```text
资源下载/
├── README.md
├── requirements.txt
├── core/
├── cases/
│   ├── goodshort/     # 短剧 gsdl（已落地）
│   ├── playbox/       # AI 图/视频 pbdl（已落地）
│   └── shortmax/      # 方案已探查
├── docs/
└── scripts/           # gsdl / playbox / list-cases
```

架构细节：[`docs/architecture/overview.md`](docs/architecture/overview.md)

---

## 案例一览

| Case | 目录 | 状态 | 说明 |
|------|------|------|------|
| **GoodShort** | [`cases/goodshort/`](cases/goodshort/) | **已落地** | REST + 标准 HLS + ffmpeg；工具 `gsdl` |
| **ShortMax** | [`cases/shortmax/`](cases/shortmax/) | 方案已探查 | 自定义 AES-CBC HLS，adapter 待实现 |
| **Playbox** | [`cases/playbox/`](cases/playbox/) | **已落地** | Explore 免登录爬取 + 人物图/封面/视频批量下载；`./scripts/playbox` |

新增案例：在 `cases/<name>/` 按 [`overview.md`](docs/architecture/overview.md) 建目录即可。

---

## 快速开始

依赖：Python 3.9+。GoodShort 另需系统 `ffmpeg`。Playbox 仅标准库 HTTP。

### Playbox（图 / 封面 / 视频）

```bash
./scripts/playbox crawl --pages 1 --max-items 10
./scripts/playbox download --workers 4
./scripts/playbox auto --max-items 5 --workers 3
./scripts/playbox status
```

说明：[`cases/playbox/README.md`](cases/playbox/README.md)

### GoodShort（短剧 HLS）

```bash
ffmpeg -version
./scripts/gsdl auto --pages 1 --max-dramas 5 --workers 2 --limit 10
./scripts/gsdl list
```

说明：[`cases/goodshort/README.md`](cases/goodshort/README.md)

---

## 工具调研

| 文档 | 内容 |
|------|------|
| [`docs/research/github-media-scrape-download.md`](docs/research/github-media-scrape-download.md) | GitHub：播放/资源链接抓取 + 批量下载（yt-dlp、gallery-dl、N_m3u8DL-RE…） |
| [`docs/research/obscura.md`](docs/research/obscura.md) | Obscura 无头浏览器（SPA/反爬可选） |

[Obscura](https://github.com/h4ckf0r0day/obscura)：轻量无头浏览；**媒体下载本身**仍用 ffmpeg / HTTP / N_m3u8DL-RE 等。

---

## 设计约定

1. **一 case 一目录**：代码、数据、下载、说明放在一起。  
2. **`core/` 保持薄**：只放真正跨站复用的工具。  
3. **默认 free-only**：只处理有权访问的流；不绕过付费墙。  
4. **限流**：`--pages` / `--max-dramas` / `--workers` / `--sleep` 控制规模。  
5. **版权**：仅个人有权内容自用，勿公开传播。

---

## 路线图

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0–P2 | GoodShort 探查 + 片库/CLI + crawl/auto | 完成（`cases/goodshort`） |
| — | 多 case 仓库结构 | 完成 |
| — | Playbox `pbdl` Explore 爬取 + 批量下载 | **完成** |
| P3 | GoodShort 登录 Cookie / 付费集验证 | 待做 |
| P4 | ShortMax 解密下载 adapter | 待做 |
| P5 | 统一多站点 CLI / 进度 TUI | 待做 |
| 可选 | Obscura 接入层（浏览器回退） | 调研完成，未接入 |

---

## 版本

| 项 | 值 |
|----|------|
| 仓库布局 | multi-case `cases/*` + `core` + `docs` |
| GoodShort 工具 | `gsdl` **0.2.0** |
| 结构整理 | 2026-08-08 |
