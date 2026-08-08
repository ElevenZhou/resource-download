# Case: ShortMax

| 项 | 内容 |
|----|------|
| 站点 | https://www.shorttv.live/ |
| 状态 | **方案已探查，代码未落地** |
| 包名（规划） | `smdl` 或 `cases.shortmax` |
| 数据目录 | `data/`（待建） |
| 下载目录 | `downloads/`（待建） |

## 目标

将 ShortMax 上**有权访问**的短剧流导出为本地 MP4，并复用本仓库统一的「片库 + 队列 + 批量下载」模型。

## 技术摘要（探查结论）

| 项 | 内容 |
|----|------|
| 页面 | Nuxt 3 SSR；`/drama/{slug}-{id}`、`/episode/{slug}-{id}-{ep}` |
| 播放器 | xgplayer + hls.js；加密流走自定义 loader |
| 源字段 | payload 中 `encryptedVideoUrl` → JSON：`video_480/720/1080` m3u8 |
| CDN | `https://akamai-static.shorttv.live/hls-encrypted/.../main.m3u8` |
| 加密 | 路径含 `/hls-encrypted/`；ts **自定义 AES-CBC**（非标准 `#EXT-X-KEY` 全片） |
| 解密要点 | 段前 1024 字节 header；IV 固定 `shortmax00000000`；key 自 header 偏移截取 16 字节 |
| 下载思路 | 拉 m3u8 → 下 ts → 按段解密 → 拼 MPEG-TS → ffmpeg 转 MP4 |
| 验证 | 本地曾验证解密后为合法 H.264 + AAC |

## 建议目录（实现时）

```text
cases/shortmax/
├── README.md          # 本文件
├── smdl/              # 实现包
│   ├── __init__.py
│   ├── __main__.py
│   ├── api.py         # 页面/payload 解析、剧集列表
│   ├── crypto.py      # ts AES 解密
│   ├── store.py       # SQLite 片库（可对齐 goodshort 状态机）
│   ├── downloader.py  # 并发下载 + 解密 + ffmpeg
│   └── cli.py
├── data/
├── downloads/
└── notes/             # 抓包、样例 header、测试向量
```

## 与 GoodShort 的差异

| 维度 | GoodShort | ShortMax |
|------|-----------|----------|
| 源发现 | REST API | Nuxt SSR / payload |
| 流 | 标准 HLS | 自定义加密 HLS |
| 下载 | `ffmpeg -c copy` | 先解密再 ffmpeg |
| 浏览器 | 通常不需要 | 可选 Obscura/Playwright 辅助抓页 |

浏览器工具调研见：[`docs/research/obscura.md`](../../docs/research/obscura.md)。

## 权限与合规

- 不绕过付费墙 / DRM。  
- 仅导出账号已解锁、可合法访问的内容，供个人自用。  
- 控制请求频率，遵守站点条款。

## 下一步

1. 固化解密测试向量到 `notes/`  
2. 实现 `crypto.py` + 单集下载 PoC  
3. 对齐 `store` 状态机与 CLI（`add/sync/download/status`）  
4. 根 README 将本 case 状态改为「已落地」
