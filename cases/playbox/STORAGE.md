# Playbox 下载：怎么下、怎么命名、下到哪

## 1. 下到哪里

默认根目录（相对 case）：

```text
/Users/xmm/projects/资源下载/cases/playbox/downloads/
```

| 内容 | 路径 |
|------|------|
| 媒体文件 | `cases/playbox/downloads/` |
| 片库数据库 | `cases/playbox/data/playbox.db` |
| 链接导出 | `cases/playbox/data/exports/*.jsonl` |

换目录：

```bash
./scripts/playbox --download-dir /Volumes/Data/playbox download --workers 4
./scripts/playbox --db /tmp/pb.db --download-dir /tmp/pb-out crawl --max-items 5
```

---

## 2. 文件夹怎么排（推荐结构）

点击弹层后，一条「卡片」会带出很多 **画廊样例**（`extend[]`）。工具按 **一张主卡片一个大文件夹** 收纳：

```text
downloads/
└── Playbox_KISSING_68e8ee8844/              ← 你点开的那张主卡片
    ├── character.webp                       ← 主作品：人物图
    ├── cover.png                            ← 主作品：封面
    ├── video.mp4                            ← 主作品：视频
    └── gallery/                             ← 弹层画廊里的其它完整样例
        ├── 001_Cumtributerlost_POV..._69bdf421ee/
        │   ├── character.webp
        │   ├── cover.png
        │   └── video.mp4
        ├── 002_Flashstep_Playing..._69c2e95335/
        │   └── ...
        └── 003_...
```

| 层级 | 含义 |
|------|------|
| 主文件夹 | Explore 上点的那张 / `resolve` 的 `item_id` |
| 主文件夹内三个文件 | 当前作品三件套 |
| `gallery/NNN_.../` | 弹层 `extend` 里第 N 组（每组也是完整三件套） |

只爬列表、不展开弹层时：**没有 `gallery/`**，每张卡片各自一个平铺文件夹。

---

## 3. 命名规则

### 文件夹名

```text
{发布者}_{作品名}_{item_id前10位}
```

- 非法路径字符会去掉；过长会截断。
- 画廊子目录前缀：`001_` `002_` …（弹层顺序）。

### 文件名（固定，便于脚本）

| 资源 | 文件名 | 来源 |
|------|--------|------|
| 人物图 | `character.webp` / `.jpg` / `.png` | 按 URL 后缀 |
| 封面 | `cover.png`（或实际后缀） | `posterUrl` |
| 视频 | `video.mp4` | 成片 |
| 第 2 张人物图（若有） | `character_2.*` | 少见 |

同一目录下三种资源一眼能认；换条目靠**父文件夹名**区分。

---

## 4. 怎么下载（推荐流程）

内容分两档，**别一上来全站扫**。

### A. 只要列表卡片（轻量）

```bash
# 1) 爬 Explore 第 1 页，最多 20 张卡片
./scripts/playbox crawl --pages 1 --max-items 20

# 2) 只下这些卡片的三件套
./scripts/playbox download --workers 4

./scripts/playbox status
```

### B. 点开某张卡，把弹层画廊一起下（内容多）

```bash
# 1) 先入库列表，挑一张 item_id
./scripts/playbox crawl --pages 1 --max-items 10
./scripts/playbox list

# 2) 「点击」这张：展开 extend（建议限条数）
./scripts/playbox resolve 68e8ee8844453590ab10f429 --max-extend 15

# 3) 下载主卡片 + gallery 子项
./scripts/playbox download --workers 4
```

一条弹层若 `extend` 有 50 组 ≈ **50×3 ≈ 150 个文件**，务必加 `--max-extend`。

### C. 爬的时候就打开弹层（更重）

```bash
./scripts/playbox crawl --max-items 3 --with-extend --max-extend 10 --sleep 0.4
./scripts/playbox download --workers 3
```

### D. 一键试跑

```bash
./scripts/playbox auto --max-items 5 --workers 3
# 若要弹层画廊：
./scripts/playbox auto --max-items 2 --with-extend --max-extend 8 --workers 3
```

---

## 5. 控制规模（必看）

| 旋钮 | 作用 | 建议 |
|------|------|------|
| `--max-items` | 列表卡片数 | 试跑 5～20 |
| `--max-extend` | 每张卡弹层样例数 | 先 5～15 |
| `--pages` | Explore 页数 | 从 1 起 |
| `--workers` | 并发 | 3～5 |
| `--sleep` | 请求间隔 | 0.3～0.5 |
| `--kinds video` | 只下视频 | 省流量/磁盘 |
| `--limit N` | 本轮最多 N 个 asset | 分批下 |

体积粗算：每条样例人物图几十 KB + 封面几十 KB + 视频 1～5MB 起。  
**15 组画廊 ≈ 几十～上百 MB/卡片**，全开 `extend` 会非常大。

---

## 6. 日常命令对照

| 目的 | 命令 |
|------|------|
| 看库里有什么 | `./scripts/playbox list` |
| 看一条详情/本地路径 | `./scripts/playbox show <id>` |
| 进度 | `./scripts/playbox status` |
| 失败重试 | `./scripts/playbox retry && ./scripts/playbox download --include-failed` |
| 导出台账 | `./scripts/playbox export -o cases/playbox/data/exports/lib.jsonl` |
| 只下视频 | `./scripts/playbox download --kinds video` |

---

## 7. 和「页面上看到的」对应关系

```text
Explore 列表卡片
  → 主文件夹里的 character / cover / video

点击弹出层里的多组缩略图/样例
  → 主文件夹/gallery/001_...、002_... 各一套三件套
```

片库 `note` 字段会记：

- `crawl:explore` / `resolve:modal` — 主卡片  
- `*:extend_of:{parentId}` — 画廊子项  

---

## 8. 文字：模板名 / 标签 / 创作者

见 **[`METADATA.md`](METADATA.md)**。

摘要：

- SQLite：`items` 存标题/创作者/模板名；`tags` / `keywords` / `categories` 归类表  
- 每个作品目录写 **`meta.json`**（下载时自动生成）  
- 查询：`./scripts/playbox tags` / `search --tag VIP` / `show <id>`

## 9. 旧数据说明

升级「主卡 + gallery」之前，画廊项可能是 **平铺** 在 `downloads/` 根下。  
新 `resolve` / `--with-extend` 会尽量写到 `主卡/gallery/NNN_...`；已存在的旧文件夹不会自动搬家，可手动整理或删库重下。
