# 更新日志

本文件记录 NJU QQ Audit 插件的版本变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [v0.2.4] - 2026-07-09

### 修复

- `probe_api` 调用兼容旧签名（热重载新旧代码混用时不再因多传 `event` 崩溃）

## [v0.2.3] - 2026-07-09

### 修复

- 平台缓存改为 `onebot/platform_cache.py` 直接 setattr，避免热重载后 `remember_event` 方法缺失导致崩溃

## [v0.2.2] - 2026-07-09

### 修复

- 修复 AstrBot 4.x 下 `adapter_found: no` / `aiocqhttp adapter not available` 问题
- adapter 查找兼容 `get_platform_inst`、`event.bot`、遍历已加载平台实例
- 收到任意事件或管理员命令时缓存 `platform_id` / bot client，供 approve/reject 使用
- `/audit probe api` 在命令上下文中优先使用当前 `event.bot` 检测
- 修复热重载后 `remember_event_platform` 缺失导致 `on_all_events` 崩溃；`initialize()` 重建 PluginContext

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
