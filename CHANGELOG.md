# 更新日志

本文件记录 NJU QQ Audit 插件的版本变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [v0.2.1] - 2026-07-09

### 变更

- OneBot 主动操作默认改经 **AstrBot aiocqhttp adapter**，不再要求配置 SnowLuma HTTP
- 新增 `onebot_action_backend` 配置（`astrbot_adapter` / `http`），HTTP 仅作 fallback
- 管理员主动通知优先使用 `context.send_message` + UMO 缓存（`admin_sessions.json`）
- 新增 `/audit probe api`，用于检测 adapter 是否能调用 OneBot API
- `/audit status` 显示 `event_source`、`action_backend`、`adapter_action_available` 等字段

### 新增

- `onebot/actions.py` — ActionClient 抽象与工厂
- `onebot/astrbot_adapter_actions.py` — AstrBot adapter 实现
- `storage/admin_session_store.py` — 管理员私聊 UMO 持久化

## [v0.2.0] - 2026-07-09

### 新增

- 正式入群审核功能：解析 comment、匹配 NJUTable/mock 学生缓存、四模式运行
- 管理员命令：`/audit help|status|mode|sync|pending|request|approve|reject|process|stats`
- NJUTable / SeaTable 同步与本地缓存
- 存储层：`requests.json`、`audit.jsonl`、`runtime.json`、`students.cache.json`
- 34+ 单元测试

### 说明

- 默认 `mode=record-only`，不自动 reject
- `auto` 模式仅 strong match（姓名+学号 / 姓名+通知书）自动 approve

## [v0.1.0] - 2026-07-09

### 新增

- Phase 0 OneBot 事件探针（`/audit probe *`、`/audit_probe` 兼容命令）
- 通过 AstrBot aiocqhttp 适配器接收 `request.group.add` 原始事件

### 修复

- 修复 AstrBot 加载时报 `No module named 'probe'` 的导入问题

## [v0.0.1] - 2026-07-09

### 新增

- 初始仓库与插件骨架
