# 1.1.0 — 2026-10-29

> 来源从 14 个扩到 29 个，并补上「没有 RSS 的站点」这条抓取路径。

## 新增

- **来源 14 → 29 个，默认启用 18 个**。新增可用：National Geographic、Los Angeles Times、CBS News、
  Politico、Popular Science、CNET、ScienceDaily、ScienceAlert、Aeon、Nautilus、Astronomy、CNBC。
- **HTML 列表页抓取**（`Source.kind="html"` + `feeds.parse_listing()`）：给没有 RSS 的站点用。
  国家地理官网 2023 年底停掉 RSS，改为抓栏目页，按链接特征挑文章，标题取 `aria-label` 等卡片属性。
- 收录但默认关闭：TIME、纽约时报 —— 当前网络下 feed 与站点都 TCP 超时，留条目便于换网络后启用。

## 修复

- **被 WAF 拦掉的 403**：`urllib` 里自定义 TLS 上下文会改变指纹，Politico 等直接拒绝；
  改回默认上下文，并补齐 `Sec-Fetch-*` 请求头后恢复正常。
- 截图工具会抓到同名的旧窗口：改为按「启动前后 PID 差集」定位，并连同子进程一起结束。

## 改进

- feed 并发 6 → 8；18 个源冷启动 6.7 秒，10 分钟内再次进入 0.0 秒（feed 缓存 TTL）。
- 打包版的版本号断言不再写死 1.0.0，改为从 `__version__` 推导，避免每次发版都要改测试。

## 收录结论（当时网络）

- 被反爬拦掉、未收录：Ars Technica（405）、Phys.org（403）、Undark（403）、Longreads（feed 403）、
  Atlas Obscura（正文仅 75 词）、STAT（多为付费短讯）。
- 打包验证：只启用国家地理时能正常抓到文章，证明列表页链路在冻结环境里同样可用。
