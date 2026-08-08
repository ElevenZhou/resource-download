# Case: GoodShort

> 本目录是多站点项目中的 **GoodShort** 案例：目录爬取、片库、批量 HLS 下载。  
> 仓库总览见根目录 [`README.md`](../../README.md)。其它案例见 `cases/`。

| 项 | 内容 |
|----|------|
| 站点 | https://www.goodshort.com/ |
| 状态 | **已落地**（`gsdl` v0.2.0） |
| 包 | `gsdl/` |
| 数据 | `data/goodshort.db` |
| 下载 | `downloads/goodshort/` |

---

# 短剧资源下载 — GoodShort 落地文档

> 将 GoodShort 的实际媒体资源下载、导出到本地，并支持**批量入库、批量下载、进度管理**。

---

## 1. 目标与范围

### 1.1 核心目的

| 目标 | 说明 |
|------|------|
| 自动拉目录 | 爬分类/首页，拿到剧名 + 链接（无需手输 URL） |
| 找视频源 | 从播放页 / API 拿到真实 m3u8（或等价流地址） |
| 下载导出 | 将 HLS 合并为本地 MP4 |
| 批量管理 | 多部剧入库、分集状态、并发队列、失败重试、进度台账 |

### 1.2 目标网站

| 站点 | 域名 | 落地状态 |
|------|------|----------|
| **GoodShort** | https://www.goodshort.com/ | **已实现**（`python3 -m gsdl`） |
| **ShortMax** | https://www.shorttv.live/ | 已探查方案，代码未落地 |

### 1.3 非目标（当前版本）

- 绕过付费墙 / 破解 DRM 账号权益（未登录仅处理免费可播集）
- ShortMax 批量下载（见第 7 节）
- GUI / Web 管理面板

---

## 2. 总体方案

两端均是「在线短剧站 + App」，Web 端可拿到播放相关接口。差异在于流是否加密：

```text
  自动: crawl 分类/首页 ──┐
  手动: URL / bookId ────┼──► Catalog / Adapter
                           │    剧名 + 链接 + bookId
                           ▼
                    ┌─────────────────┐
                    │  本地片库 SQLite │  状态：pending/done/failed/locked
                    └────────┬────────┘
                             │ claim 队列
                             ▼
                    ┌─────────────────┐
                    │  Downloader     │  并发 workers
                    │  · GoodShort: 标准 HLS + ffmpeg
                    │  · ShortMax:  自定义 AES 解密 + ffmpeg（待做）
                    └────────┬────────┘
                             ▼
                    downloads/{site}/{剧名}_{id}/EPxxx.mp4
```

| 维度 | GoodShort | ShortMax |
|------|-----------|----------|
| 源发现 | REST：`chapter/page`、`chapter/detail` | Nuxt SSR payload 中的 `encryptedVideoUrl` |
| 流格式 | **标准 HLS**（无 `#EXT-X-KEY`） | HLS，路径 `/hls-encrypted/`，**ts 自定义 AES-CBC** |
| 下载 | `ffmpeg -c copy` 即可 | 需按段解密后再拼 MP4 |
| 批量 | **已落地 `gsdl`** | 待实现 |

---

## 3. GoodShort 技术方案（已验证）

### 3.1 页面与 ID

| 类型 | URL 形态 | 关键 ID |
|------|----------|---------|
| 剧详情 | `/drama/{slug}-{bookId}` | `bookId`，如 `31000865838` |
| 分集播放 | `/episode/{slug}-{bookId}/{001}-{chapterId}` | `chapterId`，如 `10276261` |
| 分集列表页 | `/episodes/{slug}-{bookId}` | 同 bookId |

工具入参支持：剧 URL、分集 URL、纯 `bookId`。

### 3.2 使用的 Web API

Base：`https://www.goodshort.com`  
方法：`POST`，`Content-Type: application/json`  
成功：`status == 0`（或 `success`）

| 路径 | 用途 | 请求体（要点） |
|------|------|----------------|
| `/hwycreels/book/detail` | 剧元数据 | `{ "bookId": "..." }` |
| `/hwycreels/chapter/page` | 分集列表（分页） | `{ "bookId", "pageNo", "pageSize" }` |
| `/hwycreels/chapter/detail` | 单集详情 / 刷新 m3u8 | `{ "bookId", "chapterId", "num": 16 }` |
| `/hwycreels/home/index` | 首页栏目（banner/频道） | `{ "language": "en" }` |
| `/hwycreels/home/second/list` | 首页栏目分页 | `{ "columnResourceUrl", "pageNo", ... }` |
| **SSR 目录页** | 分类剧表 | `GET /dramas/{category}?page=N`（HTML 内 `__INITIAL_STATE__`） |

**目录爬取（已实现）：**

| 源 | 说明 |
|----|------|
| `/dramas/playlets?page=N` | 全部分类，约 180+ 页、2000+ 部 |
| `/dramas/romance-137-playlets` 等 | 各 genre 分页 |
| 首页 `home/index` + `second/list` | 热门 / 栏目推荐 |

**分集列表字段（关键）：**

| 字段 | 含义 |
|------|------|
| `id` | chapterId |
| `chapterName` | 集名，如 `001` |
| `chapterResourceUrl` | 如 `001-10276261`（用于集号） |
| `price` | `0` 免费；`>0` 付费 |
| `m3u8Path` | 有值则可播；付费未解锁常为 `null` |
| `playTime` | 时长（秒） |

**流地址特征：**

```text
https://v3.goodshort.com/mts/books/.../xxx.m3u8?expiredTime=...&tul=...
```

- 标准 HLS VOD，分片为 `.ts`
- 带 **过期签名**（`expiredTime` + `tul`），不宜长期缓存 URL
- 下载前应再调 `chapter/detail` 刷新（`gsdl` 已做）

### 3.3 收费规则与「想全下」怎么付（重要）

> 以下根据 **Web API 实测 + 站点能力** 归纳，**不是**官方完整价目表。  
> 金币单价、会员权益、是否整剧解锁，**以官方 App 内充值/订阅页为准**。

#### 3.3.1 平台怎么收费

GoodShort 属于短剧常见的 **「前几集免费 + 后面按集扣金币」**，并辅以充值 / 订阅入口。

| 规则 | 说明 |
|------|------|
| 计费粒度 | **按集**（接口字段 `unit: CHAPTER`） |
| 免费集 | `price = 0`，响应里带 `m3u8Path`，未登录也可播/下 |
| 收费集 | `price > 0`（实测常见约 **28–80 金币/集**），未解锁时 **`m3u8Path = null`** |
| 免费集数量 | **每部剧不同**，不是固定「前 3 集」 |
| 其他付费形态 | 站点有 **TOP UP（充值）**、**Subscription（订阅/会员）**；Web 页多半是入口，细则看 **App** |

#### 3.3.2 实测样例（未登录 Web）

| 剧 | bookId | 总集数 | 免费集 | 收费集 | 按集买齐约需金币\* |
|----|--------|--------|--------|--------|-------------------|
| The Lady Boss is Done Pretending | `31000865838` | 72 | **7**（EP001–007） | 65 | **~2828** |
| Blood and Bones of the Disowned Daughter | `31001113972` | 64 | **11** | 53 | **~2758** |

\*把各集 `price` 相加；仅估算「按集解锁」成本，不含活动折扣/会员折扣。

边界示例（Lady Boss）：

```text
EP001–007  price=0   → 有 m3u8 → gsdl 可下（pending/done）
EP008+     price>0   → 无 m3u8 → gsdl 标 locked（默认跳过）
```

查看本机片库里某部剧的免费/锁定分布：

```bash
python3 -m gsdl episodes <bookId>
python3 -m gsdl episodes <bookId> --status locked
python3 -m gsdl status <bookId>
```

#### 3.3.3 会员 vs 充金币：怎么选

| 方式 | 适合 | 注意 |
|------|------|------|
| **不付费** | 只要预览/免费集 | `auto` / `download` 默认 `--free-only` |
| **充金币，按集解锁** | 只要 **1～几部** 全集 | 单部可能就要两三千币；多部剧成本很高 |
| **开会员 / 订阅** | 想长期、大量看 | **必须先在 App 确认权益**：有的 VIP 是去广告/送币，**不等于全站免费** |
| **整剧解锁包**（若 App 有） | 单部刚需 | 往往比一集一集点更省事 |

粗选流程：

```text
只试剧 / 免费集够用
  → 不付费，用 gsdl 即可

只要少数几部全集
  → App 里算：Σ(各集 price) 或整剧价
  → 对比会员是否覆盖这些剧
  → 通常金币/整剧更直观

要大批量「尽量全下」
  → 先确认会员是否真能看付费集
  → 若会员 = 全站或大量解锁 → 优先考虑会员
  → 若会员只是送币/折扣 → 仍是烧金币，全站不现实
```

#### 3.3.4 和 `gsdl` 的关系（权限边界）

| 你的账号权限 | 工具行为 |
|--------------|----------|
| 未登录 / 未付费 | 只能下免费集；收费集 → `locked` |
| 已登录且已解锁（金币或会员权益） | 接口**可能**返回 `m3u8Path`；可试 Cookie + `--no-free-only` |
| 未解锁的收费集 | **下不了**——是权限问题，不是再写代码就能绕过 |

原则：

1. **先在官方渠道获得播放权**（金币 / 会员等）  
2. **再用工具导出你已有权访问的流**  
3. **工具不绕过付费墙**（见 §1.3、§6）

登录态实验（需浏览器已登录且能看该集）：

```bash
# 将 Cookie 换成你的登录态；先 sync 再尝试非 free-only
python3 -m gsdl sync <bookId>
python3 -m gsdl download <bookId> --cookie "你的Cookie" --no-free-only --workers 2
```

说明：

- 当前 **未完整验证** 会员/金币解锁后 Web Cookie 是否一定返回 m3u8  
- 若 App 可看但 Web Cookie 仍无流，可能是 **仅 App 鉴权 / 另一套接口**，属后续「登录态适配」（路线图 P3）

#### 3.3.5 「全部下载」落地清单

1. 在 **官方 App** 查清：金币价格、会员是否解锁付费集、有无整剧买断  
2. 对目标剧用 `episodes` 看 `locked` 数量，粗估成本  
3. 付费解锁后，同一账号在浏览器登录，导出 Cookie 试下载  
4. 批量仍用 `auto`/`download`，但预期只能覆盖 **已解锁** 的集  
5. 全站 2000+ 部 × 大部分付费集：即使开会员，也要考虑 **磁盘、时间、ToS**，务必限流

### 3.4 下载方式

1. 从片库取出 `pending` 分集  
2. `chapter/detail` 取最新 `m3u8Path`  
3. 调用 ffmpeg：

```bash
ffmpeg -y -loglevel error \
  -user_agent "Mozilla/5.0 ..." \
  -headers "Referer: https://www.goodshort.com/\r\n" \
  -i "<m3u8Path>" \
  -c copy -bsf:a aac_adtstoasc \
  "EP001_....mp4"
```

无需额外 Python 依赖；系统需安装 `ffmpeg`。

---

## 4. 已落地实现：`gsdl`

### 4.1 目录结构

```text
cases/goodshort/
├── README.md                 # 本文档
├── gsdl/                     # GoodShort 工具包
│   ├── __init__.py           # 版本号
│   ├── __main__.py           # python3 -m gsdl
│   ├── cli.py                # 命令行入口
│   ├── api.py                # GoodShort HTTP 客户端 + URL 解析
│   ├── store.py              # SQLite 片库
│   └── downloader.py         # 并发 HLS 下载（ffmpeg）
├── data/
│   ├── goodshort.db          # 片库数据库（运行后生成）
│   └── catalog*.tsv          # crawl --export 目录清单（可选）
└── downloads/
    └── goodshort/
        └── {剧名}_{bookId}/
            └── EP001_{name}_{chapterId}.mp4
```

### 4.2 模块职责

| 模块 | 职责 |
|------|------|
| `api.py` | 请求 book/chapter API；解析 drama/episode URL；映射分集元数据 |
| `store.py` | SQLite：剧表、分集表、状态迁移、队列 claim、统计 export |
| `downloader.py` | 刷新 m3u8、ffmpeg 下载、已存在文件跳过、写回状态 |
| `cli.py` | `add/sync/list/episodes/download/status/retry/remove/export` |

### 4.3 数据模型（SQLite）

**表 `dramas`（剧）**

| 字段 | 说明 |
|------|------|
| `book_id` | 主键 |
| `book_name` / `book_resource_url` | 标题与 SEO 路径 |
| `chapter_count` / `cover` / `introduction` / … | 元数据 |
| `dir_name` | 本地下载目录名 |
| `added_at` / `synced_at` | 入库与最近同步时间 |
| `note` | 可选备注 |

**表 `episodes`（分集）**

| 字段 | 说明 |
|------|------|
| `chapter_id` | 主键 |
| `book_id` | 外键 → dramas |
| `ep_index` | 集号（优先从 `001-xxx` 解析，1-based） |
| `price` / `m3u8_path` / `play_time` | 是否免费、流地址、时长 |
| `download_status` | 见状态机 |
| `file_path` / `file_size` | 本地文件 |
| `error` / `attempts` / `updated_at` | 失败信息与重试次数 |

### 4.4 下载状态机

```text
                 add/sync
                    │
                    ▼
              ┌─────────┐
              │ pending │◄──────── retry / --include-failed
              └────┬────┘
                   │ download claim
                   ▼
              ┌─────────┐
              │ queued  │
              └────┬────┘
                   │ worker 领取
                   ▼
           ┌──────────────┐
           │ downloading  │
           └──────┬───────┘
          成功 │        │ 失败
               ▼        ▼
          ┌──────┐  ┌────────┐
          │ done │  │ failed │
          └──────┘  └────────┘

  无流 / 付费未解锁 ──► locked（默认不下）
  进程中断 stuck ──► download --reset-stuck 回到 pending
```

---

## 5. 使用说明

### 5.1 环境依赖

| 依赖 | 要求 |
|------|------|
| Python | 3.9+（使用标准库即可） |
| ffmpeg | 已安装且在 `PATH` 中 |

```bash
# macOS
brew install ffmpeg

# 检查
python3 --version
ffmpeg -version
```

无需 `pip install`（见 `requirements.txt`）。

### 5.2 工作目录

**推荐**：在本 case 目录执行，或从仓库根用入口脚本：

```bash
# 方式 A：进入 case
cd /path/to/资源下载/cases/goodshort
PYTHONPATH=. python3 -m gsdl --help

# 方式 B：仓库根入口
cd /path/to/资源下载
./scripts/gsdl --help
```

默认路径（相对本 case 根 `cases/goodshort/`）：

| 项 | 默认 |
|----|------|
| 数据库 | `data/goodshort.db` |
| 下载目录 | `downloads/goodshort/` |
| 站点 | `https://www.goodshort.com` |

可用全局参数覆盖：

```bash
python3 -m gsdl --db /tmp/gs.db --download-dir /data/gs --cookie "..." <子命令>
```

### 5.3 命令一览

| 命令 | 作用 |
|------|------|
| **`auto`** | **一键：爬目录 → 入库 → 下免费集** |
| **`crawl`** | 只拉目录（剧名+链接），可选 `--ingest` 入库 |
| `categories` | 列出可爬分类 |
| `add <url\|bookId> ...` | 手动批量入库并同步分集 |
| `sync [bookId...]` | 刷新元数据；不传则同步全部 |
| `list` | 片库列表 + 完成度 |
| `episodes <bookId>` | 分集状态明细 |
| `download [bookId...]` | 批量下载 pending |
| `status [bookId]` | 总览或单剧统计 |
| `retry [bookId]` | failed → pending |
| `remove <bookId>` | 移出片库；可选删文件 |
| `export` | 导出 TSV 台账 |

### 5.4 快速开始

#### A. 全自动（推荐：自己拉目录并下载）

```bash
# 爬 playlets 第 1 页，最多 20 部剧 → 入库 → 下免费集
python3 -m gsdl auto --pages 1 --max-dramas 20 --workers 3

# 先看会拉到什么（不入库、不下）
python3 -m gsdl auto --pages 1 --max-dramas 10 --dry-run

# 多分类 + 每类 2 页
python3 -m gsdl auto --all-categories --pages 2 --max-dramas 50 --workers 3

# 指定分类
python3 -m gsdl auto --category romance-137-playlets --pages 3 --max-dramas 30
```

#### B. 分步：只爬目录 → 再入库 → 再下载

```bash
# 1) 拉目录，导出 TSV（剧名 + 链接）
python3 -m gsdl crawl --pages 2 --max-dramas 40 --export data/catalog.tsv

# 2) 拉目录并写入片库
python3 -m gsdl crawl --pages 2 --max-dramas 40 --ingest --skip-existing

# 3) 下载片库里所有免费 pending
python3 -m gsdl download --workers 3
```

#### C. 手动指定剧

```bash
python3 -m gsdl add \
  "https://www.goodshort.com/drama/the-lady-boss-is-done-pretending-31000865838"
python3 -m gsdl list
python3 -m gsdl download 31000865838 --workers 3
python3 -m gsdl episodes 31000865838
```

### 5.5 `crawl` / `auto` 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--category X` | `playlets` | 分类 path，可重复；如 `romance-137-playlets` |
| `--all-categories` | 关 | 爬内置全部分类（playlets + 各 genre） |
| `--pages N` | `1`（crawl）/ `1`（auto） | **每个分类**最多爬 N 页 |
| `--page-from` / `--page-to` | 1 / 无 | 页码区间 |
| `--max-dramas N` | 无 / auto 默认 **20** | 全局去重后最多处理 N 部剧 |
| `--sleep` | `0.35` | 翻页间隔（秒），降低风控 |
| `--no-home` | 关 | 不拉首页栏目 |
| `--ingest` | crawl 默认关 | 同步进 SQLite 片库 |
| `--export path` | 无 | 写出 `book_id/name/url` TSV |
| `--skip-existing` | 关 | 已在片库的剧跳过 detail 同步 |
| `--dry-run` | auto 专用 | 只打印目录，不入库不下 |
| `--workers` / `--limit` / `--free-only` | 同 download | auto 下载阶段参数 |

```bash
# 查看分类
python3 -m gsdl categories
python3 -m gsdl categories --online
```

### 5.6 批量管理场景

**全自动小批量（试跑）：**

```bash
python3 -m gsdl auto --pages 1 --max-dramas 5 --workers 2 --limit 10
```

**目录先落盘，人工筛选后再下：**

```bash
python3 -m gsdl crawl --all-categories --pages 1 --max-dramas 100 \
  --export data/catalog.tsv --no-home
# 编辑 catalog.tsv 后，对需要的 bookId：
python3 -m gsdl add 31001113972 31000881454
python3 -m gsdl download --workers 3
```

**多部剧手动词库再统一下：**

```bash
python3 -m gsdl add \
  "https://www.goodshort.com/drama/aaa-3100..." \
  31000865838
python3 -m gsdl download --workers 4
```

**按集号区间 / 限额：**

```bash
python3 -m gsdl download 31000865838 --from 1 --to 7 --limit 10
```

**中断后续跑：**

```bash
python3 -m gsdl download --reset-stuck --workers 3
```

**失败重试：**

```bash
python3 -m gsdl retry
python3 -m gsdl download --include-failed
```

**导出台账：**

```bash
python3 -m gsdl export > library.tsv
```

**移出片库：**

```bash
python3 -m gsdl remove 31000865838
python3 -m gsdl remove 31000865838 --delete-files
```

### 5.7 `download` 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `book_ids` | 全部剧 | 不传则下片库中所有符合条件的集 |
| `--workers` | `3` | 并发数，建议 2–4 |
| `--free-only` | 开 | 只下 `price=0` 且有 m3u8 的集 |
| `--no-free-only` | — | 尝试非 free（通常仍需有效 Cookie） |
| `--include-failed` | 关 | 把 `failed` 一并进队列 |
| `--limit N` | 无 | 本轮最多 N 集 |
| `--from` / `--to` | 无 | 按 `ep_index` 过滤 |
| `--timeout` | `600` | 单集 ffmpeg 超时（秒） |
| `--reset-stuck` | 关 | 先把 `downloading/queued` 重置为 `pending` |

全局：

| 参数 | 说明 |
|------|------|
| `--cookie "..."` | 浏览器登录 Cookie（付费集实验用） |
| `--db` / `--download-dir` / `--base-url` | 路径与站点覆盖 |

### 5.8 输出文件命名

```text
downloads/goodshort/{安全剧名}_{bookId}/EP{集号三位数}_{集名}_{chapterId}.mp4
```

示例：

```text
downloads/goodshort/The Lady Boss is Done Pretending_31000865838/EP001_001_10276261.mp4
```

- 已存在且体积 > 10KB 的文件会 **跳过** 并标为 `done`
- 下载中使用 `.part.mp4` 临时文件，成功后原子替换

---

## 6. 限制、风险与合规

| 项 | 说明 |
|----|------|
| 免费集 | 未登录仅能下站点允许预览的集；其余为 `locked` |
| 付费规则 | 多为按集金币；免费集数因剧而异；详见 **§3.3** |
| 付费集 / 会员 | 须官方解锁；Cookie + `--no-free-only` **实验性**，不保证全集可下 |
| 不绕过付费墙 | 无 m3u8 = 无下载源；不提供破解、盗刷、绕过 DRM 的方案 |
| URL 过期 | m3u8 签名有时效；工具每次下载前刷新 |
| 频率 | 控制 `--workers` / `--max-dramas` / `--sleep`，避免压垮站点 |
| 版权 | 仅限个人有权访问的内容自用；勿公开传播 |
| 接口变更 | 站点改 API / 风控后需同步改 `api.py` |

---

## 7. ShortMax

ShortMax 已拆到独立 case：[`../shortmax/README.md`](../shortmax/README.md)（方案已探查，代码未落地）。

---

## 8. 路线图

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0 | GoodShort 技术探查（API + HLS） | 完成 |
| P1 | `gsdl` 片库 + 批量下载 + CLI | **完成** |
| P2 | 自动爬目录（剧名+链接）+ `auto` 一键下载 | **完成** |
| P3 | 登录 Cookie / 付费集验证 | 待做 |
| P4 | ShortMax 解密下载 adapter | 待做 |
| P5 | 统一多站点 CLI / 进度 TUI | 待做 |

---

## 9. 自检清单（落地验收）

在本 case 目录或通过 `./scripts/gsdl` 执行：

```bash
cd cases/goodshort
export PYTHONPATH=.

# 环境
ffmpeg -version >/dev/null && echo "ffmpeg ok"

# A) 自动拉目录（不下载）
python3 -m gsdl crawl --pages 1 --max-dramas 5 --export data/catalog_sample.tsv
# 期望：打印剧名+链接，TSV 有 5 行数据

# B) 一键：目录 → 入库 → 下最多 3 集免费
python3 -m gsdl auto --pages 1 --max-dramas 2 --workers 2 --limit 3 --no-home

# C) 状态与文件
python3 -m gsdl list
ls downloads/goodshort/*/EP*.mp4 | head
```

期望：

1. `crawl` 能列出剧名与 `https://www.goodshort.com/drama/...` 链接  
2. `auto` 后 `data/goodshort.db` 有剧，分集有 `done`  
3. `downloads/goodshort/` 下出现可播放 MP4  

---

## 10. 常见问题

**Q: `nothing to download`？**  
A: 可能已全部 `done`，或只剩 `locked`。用 `episodes <bookId>` 查看；免费集下完属正常。

**Q: 大量 `failed`？**  
A: 检查网络 / ffmpeg；执行 `retry` 后 `download --include-failed`。签名过期会在重试时重新拉 m3u8。

**Q: 进程被杀掉后状态卡在 downloading？**  
A: `python3 -m gsdl download --reset-stuck`

**Q: 如何换下载目录？**  
A: `python3 -m gsdl --download-dir /path/to/out download ...`

**Q: 能否只同步不下载？**  
A: 可以，只用 `add` / `sync` / `list`，或 `crawl` 不加 `--ingest`，或 `auto --dry-run`。

**Q: 自动爬目录会下完全站吗？**  
A: 不会。必须用 `--pages` / `--max-dramas` 控制规模；`auto` 默认最多 20 部剧。全站约 2000+ 部，请务必限流。

**Q: 爬到的剧没有免费集？**  
A: 入库后会标 `locked`；`download`/`auto` 默认 `--free-only` 会跳过。换一批剧或加大 `pages`。

**Q: 收费规则是什么？要全部下载是开会员还是充币？**  
A: 见 **§3.3**。概要：前几集免费（数量因剧而异），后面按集扣金币；另有充值/订阅。  
- 只追几部全集 → 先在 App 比「按集金币 / 整剧 / 会员」  
- 想大量全下 → 先确认会员是否真解锁付费集，再决定；**不要假设开会员 = 全站免费**  
- `gsdl` 只能导出 **账号已有权访问** 的集，不能白嫖付费墙

**Q: 如何看某部剧还有多少集要付费？**  
A:

```bash
python3 -m gsdl add "<剧URL>"   # 或已在库则 sync
python3 -m gsdl episodes <bookId> --status locked
python3 -m gsdl status <bookId>
```

`locked` 条数 ≈ 未拿到流的付费/未解锁集（未登录时通常等于收费集）。

**Q: 付费后工具就能下全集了吗？**  
A: 前提是解锁后 Web 接口能返回 `m3u8Path`。请用登录 Cookie 试 `download --cookie ... --no-free-only`。若 App 可看但 Cookie 仍无流，需后续登录态适配（P3），不是再充一次钱就一定行。

---

## 11. 版本

| 项 | 值 |
|----|----|
| 工具 | `gsdl` |
| 版本 | **0.2.0**（见 `gsdl/__init__.py`） |
| 文档 | 与仓库同步的落地说明 |
| 0.2.0 | 新增 `crawl` / `auto` / `categories` 自动目录与一键下载 |
| 文档 2026-07-19 | 补充 §3.3 收费规则、会员/金币选择、与 gsdl 权限边界及 FAQ |
