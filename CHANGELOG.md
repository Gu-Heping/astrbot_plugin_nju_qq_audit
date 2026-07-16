# 更新日志

本文件记录 NJU QQ Audit 插件的版本变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [v0.4.0] - 2026-07-16

### 新增

- **研究生群自动审核**（与本科完全分离）：独立 `grad_*` 配置、群号、NJUTable token/表、缓存文件 `grad_students.cache.json` / `grad_sync_state.json`
- profile 路由：`undergraduate` / `graduate`；重叠群号拒绝处理并报警
- 研究生 parser / matcher / decision：姓名 + 硕/博 + 专业（或专业代码）唯一命中才 strong approve；不自动 reject
- `PendingRequest.profile`；`/audit list` / `view` 显示「本科/研究生」
- `/audit sync grad`、`/audit sync-grad`、`/audit list grad` / `list undergraduate`
- `/audit debug` 输出 grad_enabled、grad 群、缓存人数、同步状态、重叠警告
- **永不读取/缓存/展示「证件号码末三位」**

### 修复

- 研究生 parser：显式解析「专业代码」标签；姓名标签在「专业」前停住，避免粘连误吃
- `grad_enabled=false` 时忽略研究生群列表，重叠不再拦截本科
- 入退群 / 外部同意对账覆盖已启用的研究生目标群
- 恢复误删的 `run_sync`；研究生同步加独立锁防并发
- sweep / release rematch 默认仅处理本科，避免误关或改写研究生 pending

### 说明

- 不改变本科 matcher/parser/decision 与现有状态机
- 研究生 strong 在 `auto` 下可自动通过；release/catchup 仍仅面向本科 26 级 strong

## [v0.3.23] - 2026-07-15

### 新增

- **`/audit sweep`**：本地批量 dismiss **非 strong** pending（不调 QQ），保留 strong 给 auto / release / catchup
- `/audit sweep preview` 会先按当前缓存 rematch，再列出候选与将保留的 strong 数
- `/audit sweep confirm <原因>`：统一原因关闭全部候选；写审计 `bulk_dismiss_non_strong`

### 说明

- 适用：其他管理员在 QQ 客户端拒绝后框架不上报，导致 auto 下 weak/none 僵尸 pending 堆积
- 仍在 QQ 等待审核请用 `/audit no`；确认已入群用 `/audit mark-external`；单条本地关闭仍用 `/audit dismiss`

## [v0.3.22] - 2026-07-14

### 新增

- **`/audit catchup`**：先同步校对表，再 rematch 全部 pending，preview/confirm 补放新 strong；校对表刚更新时优先于 `release`
- **`/audit dismiss <n> confirm <原因>`**：本地关闭无效 pending（不调 QQ）；与 `no`（QQ 拒绝）、`mark-external`（QQ 侧已处理）区分
- **`/audit lookup <姓名> <学号> [专业]`**：用当前本地缓存诊断匹配（strong/weak/none），不改 pending、不调 QQ
- **`/audit release preview|confirm`**：放人前也会按当前本地缓存 rematch pending

### 修复

- **拒绝后改答案立即重申被 debounce 永久丢掉**：15 秒 burst 仅拦**同 comment** 平台连发；comment 已变则立即新建 attempt（`auto` + strong 仍自动通过）
- **定时同步双重加锁伪失败**：`SyncScheduler` 回调改为 `execute_sync`（无锁正文），忙时不覆盖上次成功状态；失败保留 `failed:异常类型`
- **SnowLuma 约 20 条截断**：达到 `FetchGroupRequests` 上限时标 `snapshot_saturated`，禁止「缺失即拒绝」推断
- **入群验证解析**：禁止把「学号」等标签当姓名；支持「我是/我叫…」「专业是…」自然语言

### 变更

- `duplicate_policy_version` 升至 `v8-reject-comment-change-bypass-burst`
- 插件版本 / metadata 升至 `v0.3.22`
- README、`/audit help`、`_conf_schema.json` 与当前命令集/配置对齐

## [v0.3.21] - 2026-07-13

### 修复

- **`get_group_system_msg` data 丢失**：aiocqhttp 解包后直接返回 list 时，`_normalize_response` 未写入 `ActionResult.data`，导致 debug 显示 `NoneType` / `parse_failed`，`/audit list` 对账失效
- 完整 OneBot 信封与解包后的 list/dict/scalar/null 均原样保留 `data`
- `/audit debug` 拆分 `adapter_found` 与 `group_system_msg_action_available`，避免 login_info probe 失败误报 adapter 不可用

## [v0.3.20] - 2026-07-13

### 新增

- **`group_decrease` 退群强信号**：提取并处理 OneBot `group_decrease`（leave/kick）；按 `group_id+user_id` 记录成员状态，退群后设 `reapply_eligible`
- 明确 `reapply_eligible` 时强制新建 attempt：忽略 debounce、同 flag/comment、旧 `event.time`；创建成功后消费该标志，避免同一事件连续建单
- **`/audit list` 自动对账**：列出前对当前 active pending 轻量同步 QQ 侧申请队列，并在列表末尾输出同步摘要
- SnowLuma `get_group_system_msg`：`ActionResult.data` 支持任意 JSON；顶层 **list** 解析（`parser_variant=snowluma_list`），兼容 NapCat `join_requests` dict
- 匹配优先级：完整 flag → `slreq` 解析的 request_id+group_id → `group_id+requester_uin`（仅 uin>0）→ comment 辅助
- `/audit debug` 增加脱敏的 `group_system_msg_probe`（data_type / request_count / top_level_shape / parser_variant 等）

### 变更

- 外部拒绝改为保守推断：需多次间隔成功空快照 + 等待窗口 + 成员明确不存在，才标记 `external_rejected_inferred`
- 配置项：`audit_list_reconcile_timeout_ms`（默认 4s）、`audit_list_reject_confirm_snapshots`、`audit_list_reject_wait_seconds`
- 对账超时/查询失败时不删 pending，仍输出本地列表并提示「QQ 状态同步失败」

### 说明

- SnowLuma 内部查询失败也可能返回成功空数组，插件无法与真实空队列区分；彻底消除误删风险需上游提供显式 success/error 接口

## [v0.3.19] - 2026-07-13

### 修复

- **退群再申请仍无提醒**：v0.3.18 的 120 秒 debounce 与 `event_time <= processed_at` 判定过严，NapCat 复用旧 `time` + 同 comment 时 1–2 分钟内再申请会被静默丢弃
- 终态 reapply 改为 **15 秒 burst 窗口**（仅挡 `/audit ok` 后 QQ 平台连发）；窗口外一律允许新 attempt，NapCat 复用旧 event_time 时写 `fallback=recycled_event_time`
- 日志 `[audit] reapply burst blocked` 便于排查

## [v0.3.18] - 2026-07-13

### 修复

- **`processed + approve` / `external` 退群再申请**：与 reject 一样支持事件指纹 reapply，新建 attempt 并通知管理员
- 修复全局 `seen_fingerprint` 在 reapply 判定前抢先拦截，导致同 flag 再申请不进 list、不发通知
- 旧 attempt 指纹冲突时使用 `#a{attempt_no}` 后缀登记，不复活旧记录

### 变更

- `duplicate_policy_version` 升至 `v7-terminal-reapply-fingerprint`
- 永久忽略同 flag 的终态仅剩 **`stale` / `ignored`**

## [v0.3.17] - 2026-07-13

### 新增

- **事件指纹**（`core/event_fingerprint.py`）：从 `raw_event.time` 提取事件时间，生成稳定指纹（group/user/flag/time/comment_hash/sub_type）
- **`processed + reject` 再申请**：同 flag 允许新建 attempt（`reapply_of` / `attempt_no` / `received_event_time`），旧拒绝记录不变
- 存储 v3：`seen_fingerprints` 索引；`by_flag` 指向最新 attempt，`by_id` 保留全部历史
- 配置项 `reapply_debounce_seconds`（默认 120）：无 event_time 时同 comment 防抖

### 变更

- `duplicate_policy_version` 升至 `v6-reject-reapply-fingerprint`
- 审计事件：`reapplication_created`、`duplicate_event_replayed`

## [v0.3.16] - 2026-07-13

### 修复

- 管理员通知双发：adapter 报失败但消息已送达时不再回退 UMO；**有 UMO 会话时只走 UMO**

## [v0.3.15] - 2026-07-13

### 新增

- **pending comment 原地更新**：同 flag + pending + comment 变化时保留 id/flag/created_at，更新解析与审核字段
- 通知「入群申请内容已更新」；审计 `duplicate_pending_comment_updated`
- `/audit list` / `view` 显示新内容与「历史填写：N 次」

### 修复

- **external reconcile** 遍历全部 `admin_qq_ids` 通知，不因申请人与管理员同号跳过

## [v0.3.14] - 2026-07-13

### 变更

- **简化重复请求状态机**：`processed` / `external` / `ignored` / `stale` 同 flag 一律 `duplicate_request_ignored`，不 release_flag、不复活 pending
- `duplicate_policy_version: v5-terminal-never-reapply`

## [v0.3.13] - 2026-07-09

### 修复

- 管理员通知优先 **adapter `send_private_msg`**，再 UMO、再 HTTP fallback

## [v0.3.12] - 2026-07-09

### 修复

- UMO 不可用时通过 adapter 发送管理员私聊通知

## [v0.3.11] - 2026-07-09

### 修复

- `manual_review` 通知在管理员列表仅含申请人时仍发送（fallback 到全部 admin）

## [v0.3.10] - 2026-07-09

### 变更

- external 同 flag 收到 group_request 允许重新审核（后被 v0.3.14 收回）

## [v0.3.9] - 2026-07-09

### 修复

- 退群后同 flag 重新申请可创建新 pending（后被 v0.3.14 调整）

## [v0.3.8] - 2026-07-09

### 变更

- 终态同 flag 禁止复活 pending；`/audit debug` 输出 reconcile / duplicate 逻辑版本

## [v0.3.7] - 2026-07-09

### 新增

- **external 对账**：`group_increase`（invite/approve/add）匹配 pending add 时标记 external、清 list_cache、通知管理员
- 无 pending 的 invite notice 忽略（`invite_notice_no_pending`）
- `/audit mark-external` 命令

### 修复

- v0.3.7.1：澄清 external 通知中 invite 为 OneBot 事件类型，不代表实际入群路径

## [v0.3.6] - 2026-07-09

### 新增

- **stale 状态**：QQ 侧申请已失效时对账与 `/audit stale` / `restore` / `mark-external`
- 完善 external / stale 管理员通知

### 修复

- v0.3.6.1 / v0.3.6.2：热重载下 stale 列表格式化导入兼容

## [v0.3.5] - 2026-07-09

### 修复

- `reconcile_external_join` 兼容 `list_cache`；事件处理异常隔离

## [v0.3.4] - 2026-07-09

### 新增

- **QQ 问答模板解析**（`extract_answer_segment`、token 黑名单）
- 审批失败可重试状态机（`failed` → pending retry）
- 8 位学号前缀匹配；剥离姓名前「26级」前缀；马理论专业别名

### 修复

- 管理员 approve/reject 失败时状态与 resolve/view/list 一致

## [v0.3.3] - 2026-07-09

### 新增

- **分批放人**：`/audit release [preview | N confirm | all confirm]`，别名 `batch strong` / `temp`；仅 strong 26 级 pending，间隔与 max_count 可配置，不改变 runtime mode
- **运营复盘**：`/audit unknown [N]`、`/audit report`（近 7 天原因分布、样例、同步摘要）
- **定时同步**：`auto_sync_*` 配置 + `SyncScheduler`；`/audit sync status` 查看状态
- **解析增强**：通知书多标签 + `notice_no_candidates`；专业 fuzzy 匹配（difflib 0.85）；NJUTable QQ 列映射与 `qq_match` 展示
- 首页显示可分批数量与 release 任务状态

### 变更

- `/audit off` / `/audit record` / `/audit auto` 文案区分 record-only 与完全停用
- `/audit process strong confirm` 委托 `ReleaseService`（兼容保留）
- help / README 运营手册导向更新

### 安全（不变）

- 不 auto reject；weak/auxiliary/非 26 级/关键词不批量通过
- 输出不含 flag/token/敏感字段

## [v0.3.2] - 2026-07-09

### 修复

- 移除 `main.py` 对 `admin.handlers.list_pending_for_admin` 的导入，避免热重载/部分更新时文件版本不一致导致插件无法加载

## [v0.3.1] - 2026-07-09

### 修复

- 热重载后旧版 `PluginContext` 缺少 `list_pending_for_admin`/`list_cache` 导致 `/audit list` 崩溃；命令执行前自动补齐兼容

## [v0.3.0] - 2026-07-09

### 新增

- 管理员操作台：`/audit` 首页、`/audit list/view/ok/no` 短编号审批（无需 confirm、无需复制 REQ id）
- 短编号缓存 `list_cache.json`：每管理员独立，30 分钟有效，最多 50 条
- 模式快捷命令：`/audit auto|manual|record|off` 及 `reset-mode confirm`
- `/audit debug` 保留技术状态；`/audit status` 与裸 `/audit` 改为人话首页
- 入群待审通知含 `/audit view/ok/no` 指引与短编号

### 变更

- 分层帮助与 README 管理员手册风格
- 旧命令 `pending/request/approve/reject/mode/probe/*` 全部保留兼容

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
