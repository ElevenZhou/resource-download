# Case: Playbox（playbox.com）

| 项 | 内容 |
|----|------|
| 站点 | https://www.playbox.com/ |
| 类型 | AI 图生视频 / Explore 图库 |
| 状态 | **已落地 `pbdl` v0.1.0**（免登录 Explore 爬取 + 三资源批量下载） |
| 包 | `pbdl/` |
| 数据 | `data/playbox.db` |
| 下载 | `downloads/{user}_{name}_{id}/` |

---

## 快速开始

依赖：Python 3.9+（标准库即可，无需 pip）。

**下载位置、命名、文件夹管理 → [`STORAGE.md`](STORAGE.md)**（建议先看）

```bash
# A) 只下列表卡片（轻量）
./scripts/playbox crawl --pages 1 --max-items 10
./scripts/playbox download --workers 4

# B) 点开一张卡的弹层画廊（内容多，务必限条数）
./scripts/playbox resolve <item_id> --max-extend 15
./scripts/playbox download --workers 4

# 一键列表试跑
./scripts/playbox auto --max-items 5 --workers 3

./scripts/playbox list
./scripts/playbox status
```

默认下载目录：`cases/playbox/downloads/`

---

## 清单页能抓到什么（免登录）

API：`GET https://api.playbox.com/api/model/explore/?page=N`

| 业务 | 字段 |
|------|------|
| 作品名 | `name` |
| 发布者 | `username` |
| 人物图 | `input.image`（及 `image2`… 若 API 返回）→ `character` / `character_2`… |
| 封面图 | `output.video.posterUrl` → asset `cover` |
| 视频 | `output.video.url` → asset `video` |
| 标签 | `tags[].name`（列表即有；详情 `GET /api/model/{id}` 可再取） |

### 点进弹层（Modal）里为什么「人物图更多」？

点击卡片后前端会再请求：

```http
GET https://api.playbox.com/api/model/{_id}
```

详情里除了当前作品，还有：

| 字段 | 含义 | 和工具的关系 |
|------|------|----------------|
| `input` / `output` | **当前这条** 人物图+封面+视频 | 默认 crawl 就有 |
| **`extend[]`** | 弹层画廊里的 **多组完整样例**（每组各自有人物图/封面/视频） | 要用 `resolve` 或 `--with-extend` |
| `related[]` | 相关推荐 | 可选 `--with-related` |

所以你在弹层上看到的「多个人物图」，多半是 **`extend` 画廊里不同生成结果**，不是同一条视频的多张输入人脸。

```bash
# 模拟点击弹层：展开 extend 画廊并入库（可限条数）
./scripts/playbox resolve <item_id> --max-extend 20
./scripts/playbox download --workers 4

# 爬列表时直接打开每张卡片的弹层数据
./scripts/playbox crawl --pages 1 --max-items 3 --with-extend --max-extend 10
```

单条生成的多输入图（`image2`…）公开接口仍很少返回；原图 403 会自动 fallback `resizedImage`。

探查记录：[`notes/probe-2026-08-08.md`](notes/probe-2026-08-08.md)

---

## 命令

| 命令 | 作用 |
|------|------|
| `crawl` | 分页 Explore → SQLite；可选 `--export jsonl`、`--dry-run` |
| `download` | 下载 pending 资源（`--kinds character,cover,video`） |
| `auto` | crawl + download |
| `list` / `show` | 片库浏览 |
| `status` / `retry` | 进度与失败重试 |
| `resolve <id>` | 调详情 API 刷新 URL/标签 |
| `export` | 导出 JSONL 台账 |
| `remove` | 移出片库；`--delete-files` 删本地目录 |

常用参数：

```bash
./scripts/playbox crawl --pages 3 --max-items 50 --sleep 0.4
./scripts/playbox crawl --export data/exports/explore.jsonl --dry-run
./scripts/playbox download --kinds video --workers 2 --limit 20
./scripts/playbox download --reset-stuck --include-failed
./scripts/playbox auto --max-items 8 --kinds character,cover,video
```

---

## 目录结构

```text
cases/playbox/
├── README.md
├── STORAGE.md          # 命名与目录管理说明
├── pbdl/
├── data/playbox.db
└── downloads/
    └── {user}_{title}_{id10}/          # 主卡片（列表/点开的那张）
        ├── character.webp | cover.png | video.mp4
        └── gallery/                    # 弹层 extend 画廊
            └── 001_{user}_{title}_{id10}/
                ├── character.*
                ├── cover.*
                └── video.mp4
```

---

## 下载行为

1. 从库中 claim `pending` assets。  
2. HTTP GET（带 Playbox Referer）；**403 时自动去掉 Referer 再试**（部分 CDN）。  
3. 失败则 `GET /api/model/{id}` 刷新签名 URL 后重试一次。  
4. 已存在且 >512B 的文件跳过并标 `done`。  
5. 使用 `.part` 临时文件，成功后替换。

### 已知限制

| 情况 | 行为 |
|------|------|
| Picture / Edit Image 类 | 可能无 `cover`/`video`，只下人物图 |
| 部分 train 素材在私有 Spaces | 人物图 URL 可能长期 **403**（封面/视频仍可能 OK） |
| 签名 URL 过期 | 自动 detail 刷新；仍失败则 `failed`，可 `retry` |
| 合规 | 仅公开 Explore；不登录破解 VIP 生成 |

---

## 数据模型（简）

**items**：`item_id, name, username, 三 URL, tags, keywords, dir_name…`  
**assets**：`item_id + kind(character|cover|video)`，状态机：

```text
pending → queued → downloading → done
                              ↘ failed → retry → pending
无 URL → skipped
```

---

## 验收自检

```bash
./scripts/playbox crawl --pages 1 --max-items 5
./scripts/playbox download --workers 3
./scripts/playbox status
ls cases/playbox/downloads/*/*
```

期望：DB 有条目；多数视频作品下有 `character.*` / `cover.*` / `video.mp4`。
