# 1.4.0 — 2026-10-30

> 按高考 B 篇的取材口味加了一条来源线，同时把「53 个来源怎么挑」这件事重做了。

## 新增

- **B 篇线 24 个来源**：American Scholar、Narratively、Brevity、Literary Hub、Yes!、Orion、
  Hippocampus、Edutopia、Cult of Pedagogy、Guideposts、StoryCorps、Big Issue、Positive News、
  Fatherly、Motherly、Science News Explores 等。来源总数 **29 → 53**，分 9 类，
  新增「随笔 / 教育 / 人物 / 成长」四类并置于分类导航最前。
- **来源管理重构**：左侧分类导航（全部 + 9 类，显示「已选/总数」）、关键词搜索（英文名/中文名/id/简介/分类，空格为 AND）、全选与清空（只作用于当前筛选结果）。
- **4 个快捷方案**：叙事向·B 篇 20 / 教育观察 7 / 均衡默认 25 / 科普时评·C·D 14，一键套用。
- 来源行支持悬停看简介，26px 一行、两列排布，一屏能看到更多。

## 改进

- 默认勾选 25 个来源，兼顾叙事线与科普线。
- 设置窗口 780×640 → 880×690，否则 10 个分类加两列列表放不下。
- 筛选逻辑抽成纯函数 `feeds.filter_sources()`，界面只是它的渲染结果，便于直接测。

## 修复

- 部分 feed 在 `<?xml ?>` 前带空行或 BOM，解析直接报「XML or text declaration not at start」，
  导致列表为空 —— 解析前统一清掉前置空白（Guideposts、eSchool News 由此恢复）。

## 收录结论（当时网络）

- 默认关闭（403/502 或需订阅）：Education Week、Reader's Digest、CS Monitor、Times Higher Ed、
  TIME for Kids、Washington Post、EdSurge、Chronicle、Guernica、KQED MindShift。

## 测试

180 个单元测试（来源筛选新增 9 项）、GUI 冒烟 36 项（搜索、分类导航、批量取消、快捷方案）；exe 13.1 MB 重打包。
