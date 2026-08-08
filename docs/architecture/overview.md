# 项目架构：爬虫 + 资源批量获取

## 设计原则

1. **案例隔离**：每个目标站点 / 渠道是一个独立 case，自带代码、数据、下载目录与说明。
2. **共享内核最小**：`core/` 只放跨 case 的通用工具（路径、命名、ffmpeg 探测等）。
3. **可扩展**：新增站点 = 在 `cases/<name>/` 下新建目录，不改动其它 case。
4. **权限边界**：只导出账号已有权访问的流；不绕过付费墙 / DRM。

## 目录树

```text
资源下载/
├── README.md                 # 总览 & 快速导航
├── requirements.txt          # 全局依赖说明
├── core/                     # 跨 case 共享工具
│   ├── paths.py
│   └── ffmpeg_util.py
├── cases/                    # ★ 每个具体案例一个子目录
│   ├── goodshort/            # GoodShort 短剧（已落地 gsdl）
│   │   ├── README.md
│   │   ├── gsdl/             # Python 包
│   │   ├── data/             # SQLite 片库、目录 TSV
│   │   └── downloads/        # 媒体输出
│   └── shortmax/             # ShortMax（方案已探查，代码待做）
│       └── README.md
├── docs/
│   ├── architecture/         # 架构说明
│   ├── research/             # 工具与技术调研（含 Obscura）
│   └── share/                # 可分享的调研页
└── scripts/                  # 根目录入口脚本
    └── gsdl
```

## 单 case 推荐布局

```text
cases/<site>/
├── README.md           # 该站点：API、用法、限制、验收
├── <pkg>/              # 实现包（api / store / downloader / cli）
├── data/               # 片库 DB、目录导出、缓存元数据
├── downloads/          # 媒体文件（可 gitignore）
└── notes/              # 可选：抓包、接口笔记
```

## 典型数据流

```text
  目录爬取 (catalog)  ──►  片库 (SQLite)  ──►  队列 claim
       ▲                        │                  │
       │                        ▼                  ▼
  分类/首页/搜索            状态机 pending…    Downloader workers
                                                    │
                                                    ▼
                                          downloads/{title}_{id}/EPxxx.mp4
```

| 阶段 | 职责 |
|------|------|
| Catalog | 发现剧名 + 稳定 ID + 链接 |
| Library | 入库、分集状态、进度台账 |
| Source resolve | 刷新可播放 URL（m3u8 等，注意签名过期） |
| Download | 并发拉取 + 转封装/合并（ffmpeg 或自定义解密） |
| Export | TSV/JSON 台账，便于人工筛选 |

## 状态机（建议各 case 对齐）

```text
pending → queued → downloading → done
                              ↘ failed → (retry) → pending
无权限 / 无流 → locked
中断卡死 → reset-stuck → pending
```

## 新增一个 case 的步骤

1. `mkdir -p cases/<name>/{data,downloads,notes}`
2. 写 `cases/<name>/README.md`（目标站、探查结论、CLI 设计）
3. 实现 `<pkg>/api.py` + `store.py` + `downloader.py` + `cli.py`
4. 在根 `README.md` 案例表中登记状态
5. 若需共享逻辑，抽到 `core/`，避免 case 互相 import

## 与 Obscura 的关系

[Obscura](https://github.com/h4ckf0r0day/obscura) 是 **Rust 无头浏览器**，适合需要 JS 渲染 / 反爬 / CDP 的目录页或播放页。  
本仓库的 GoodShort 当前走 **纯 HTTP API + ffmpeg**，多数路径不需要浏览器；当遇到：

- SSR/SPA 状态只在浏览器里出现
- 强 TLS/指纹风控
- 必须执行页面脚本才能拿到源

再在对应 case 中可选接入 Obscura（或 Playwright），见 `docs/research/obscura.md`。
