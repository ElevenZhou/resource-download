# Obscura 调研笔记

> 调研日期：2026-08-08  
> 仓库：https://github.com/h4ckf0r0day/obscura  
> 官网：https://obscura.sh/  
> 文档：https://docs.obscura.sh/

## 一句话

**Obscura** 是用 **Rust** 写的 **开源无头浏览器引擎**，面向 **Web 爬虫** 与 **AI Agent 自动化**，定位是轻量、可规模化的 Headless Chrome / Playwright 替代方案。

## 能干什么

| 能力 | 说明 |
|------|------|
| 无头浏览 | 打开页面、执行 JS、取 DOM / 标题 / 计算结果 |
| 爬虫友好 | 为 scraping / 批量会话设计，强调低内存、快启动 |
| AI Agent | 给每个 agent 独立浏览器会话（隔离、毫秒级拉起） |
| CDP 兼容 | 宣传可对 Puppeteer / Playwright 走 CDP 做替换（需实测） |
| CLI 一键 | 二进制可直接 `fetch` + `--eval` 跑 JS |
| Stealth / TLS | 发行包有标准版与 `-stealth`（TLS impersonation / BoringSSL 等）变体 |
| 部署 | 可自托管；另有 managed cloud / Docker 叙事 |

## 典型用法（CLI）

```bash
# 示例：Linux x86_64 发布包（以官方 Releases 为准）
curl -LO https://github.com/h4ckf0r0day/obscura/releases/latest/download/obscura-x86_64-linux.tar.gz
tar xzf obscura-x86_64-linux.tar.gz

# 打开页面并执行 JS
./obscura fetch https://example.com --eval "document.title"
```

macOS / ARM 另有对应 artifact（如 `obscura-aarch64-macos.tar.gz`），见 [Releases](https://github.com/h4ckf0r0day/obscura/releases)。

## 与 Chrome / Playwright 对比（宣传口径）

社区与项目文案常见对比点（**以实测为准**）：

| 维度 | Headless Chrome 系 | Obscura（宣称） |
|------|--------------------|-----------------|
| 实现 | Chromium + 驱动 | Rust + 嵌入式 V8 等 |
| 内存 | 数百 MB 级常见 | 约 ~30MB 量级会话 |
| 启动 | 偏慢 | 毫秒级 / 秒内 |
| 用途重心 | 通用浏览器自动化 | 爬虫 + Agent 规模化 |
| 协议 | CDP 原生 | 强调 CDP 兼容 / 替换 |

## 对本仓库（短剧批量下载）的价值

| 场景 | 是否需要 Obscura | 说明 |
|------|------------------|------|
| GoodShort REST API + 标准 HLS | **通常不需要** | 现有 `gsdl` 用 urllib + ffmpeg 已够 |
| 分类页 HTML / `__INITIAL_STATE__` | 可选 | 若纯 GET 被拦或改成强 JS，可用浏览器拉 HTML |
| ShortMax Nuxt SSR / 自定义 AES 流 | **源解密仍靠协议** | 浏览器可辅助抓 payload / 播放器行为，解密逻辑仍在 adapter |
| 强指纹 / 验证码 / 登录态页 | 有用 | 维护 Cookie、执行登录脚本、过简单前端门槛 |
| 全站大规模目录 | 视风控 | 轻量会话利于并发；仍须限流与合规 |

**结论：**

- Obscura 是 **浏览器层 / 页面执行层** 工具，不是「短剧一键下载器」。
- 适合放进本项目的 **`docs/research` + 未来 case 的可选依赖**，例如：
  - `cases/<site>/tools/fetch_with_obscura.sh`
  - 在 api 层提供 `BrowserFetcher` 接口，默认 HTTP，失败回退 Obscura。
- **媒体下载与解密** 仍应在 case 内用 ffmpeg / 自定义 pipeline 完成。

## 建议接入方式（若后续落地）

```text
cases/foo/
  api.py          # HttpClient 优先
  browser.py      # 可选：封装 obscura CLI 或 CDP
  downloader.py   # 与浏览器解耦：只吃 m3u8/ts URL
```

原则：

1. 目录发现优先 **HTTP/API**；浏览器只处理「非浏览器拿不到」的路径。  
2. 二进制放系统 PATH 或 `tools/bin/`，**不要**把大体积引擎塞进 git。  
3. 遵守目标站 ToS；控制并发；不用于绕过付费/DRM。

## 风险与注意

- 项目迭代快（多 release / stealth 构建变体），CLI 参数以官方文档为准。  
- 「杀死 Chrome」类营销文案不等于所有站点兼容；**关键路径必须实测**。  
- SECURITY：自动化/爬取责任在调用方，见仓库 `SECURITY.md`。  
- 合规：与本仓库其它工具一样，仅用于有权访问内容的自用导出。

## 参考链接

- GitHub：https://github.com/h4ckf0r0day/obscura  
- Docs：https://docs.obscura.sh/  
- Site：https://obscura.sh/  
- 安装/介绍文（第三方）：https://thecrazyalpaca.com/blog/obscura-headless-browser-ai-agents-rust-setup  

## 本仓库状态

| 项 | 状态 |
|----|------|
| 调研文档 | 本文 |
| 二进制安装 | 未纳入仓库 |
| case 接入 | 未做（GoodShort 暂不依赖） |
