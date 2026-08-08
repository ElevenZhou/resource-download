# 文字元数据怎么管（模板名 / 标签 / 创作者）

## 要不要单独建表？

**要，而且已经建了。** 建议分两层，不要只塞在文件名里。

| 层 | 存什么 | 干什么 |
|----|--------|--------|
| **1. items 宽表** | 标题、创作者、模板名、描述… | 一条作品一眼看全；导出/展示 |
| **2. 归类表（多对多）** | tags / keywords / categories | 按标签筛选、统计热门标签 |
| **3. 磁盘 meta.json** | 同上快照 | 不打开数据库也能看文字 |

媒体仍在 `downloads/...`；文字在 **SQLite + 每个目录的 meta.json**。

---

## 有哪些文字字段

| 业务叫法 | 字段 | 来源 |
|----------|------|------|
| 作品名 | `name` | 卡片标题 |
| 创作者 | `username` / `user_id` | 发布者 |
| 模板名 | `template_name`（无则 `model_name`） | 模板标题 / 内部模型名 |
| 模板作者 | `template_creator` | Feed 常有；Explore 可能空 |
| 模型代码 | `model_name` / `model_id` | 如 `PB_USER_...` |
| 类型 | `model_type` | GENERATE_VIDEO / BLEND_MODEL… |
| 标签 | `tags` | VIP / Free Trial / BLEND… |
| 内容词 | `keywords` | POV / Couple… |
| 分区 | `categories` | Trending / New… |
| 描述 | `description` | 可选 |
| 提示词 | `custom_prompt` | 可选 |

---

## 表结构（归类）

```text
items                  作品主表（含模板/作者等列 + tags_json 冗余）
  ├─ item_tags ── tags
  ├─ item_keywords ── keywords
  └─ item_categories ── categories
assets                 媒体下载状态（与文字分离）
```

- **冗余 JSON**：`tags_json` 方便列表展示、导出  
- **关联表**：方便 `search --tag VIP`、`tags` 统计  

---

## 磁盘上长什么样

```text
downloads/Playbox_KISSING_68e8ee8844/
  character.webp
  cover.png
  video.mp4
  meta.json                 ← 文字全在这
  gallery/001_.../
    ...
    meta.json
```

`meta.json` 示例字段：`name`, `username`, `template_name`, `tags`, `keywords`, `categories`, `files`…

下载成功时自动写；也可批量刷新：

```bash
./scripts/playbox meta
./scripts/playbox meta <item_id>
```

---

## 命令

```bash
# 看一条文字
./scripts/playbox show <item_id>

# 标签 / 关键词 / 分区 频次
./scripts/playbox tags
./scripts/playbox keywords
./scripts/playbox categories

# 筛选
./scripts/playbox search --tag VIP
./scripts/playbox search --keyword POV
./scripts/playbox search --username uranus
./scripts/playbox search --template cowgirl
./scripts/playbox search -q kissing

# 导出整库文字+路径
./scripts/playbox export -o cases/playbox/data/exports/lib.jsonl
```

旧数据若缺模板列：对关心的条目再 `resolve` 一次即可灌全。

---

## 不建议的做法

| 做法 | 原因 |
|------|------|
| 把标签全写进文件夹名 | 路径爆炸、难改、难筛 |
| 只存 raw_json 不建列 | 没法按标签统计 |
| 每个标签一个文件夹复制媒体 | 磁盘浪费、难同步 |

**正确姿势：** 媒体按「主卡/gallery」放；文字进库 + `meta.json`；用 `search`/`tags` 归类浏览。
