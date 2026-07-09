# astrbot_plugin_nju_qq_audit

南京大学 26 级新生 QQ 群入群审核 **AstrBot 插件**（非独立 Node 服务）。

## 快速开始（管理员）

1. 在 AstrBot 插件面板配置 `target_group_ids`、`admin_qq_ids`、学生数据源（mock 或 NJUTable）
2. **完整重启 AstrBot**（不要只热重载插件）
3. 私聊机器人发送 `/audit`，确认首页状态正常
4. 发送 `/audit probe api`，确认「审批接口可用」
5. 发送 `/audit sync` 同步学生数据
6. 保持 **`record-only`**（默认），用小号申请入群
7. 收到通知后发送 `/audit list`，用 `/audit ok 1` 或 `/audit no 1 理由` 处理
8. 积累多条强匹配待审时，用 `/audit release preview` 预览，再 `/audit release 10 confirm` 分批放人
9. 确认流程无误后，可选 `/audit auto confirm` 开启自动强匹配（仅 26 级 strong）

> 短编号来自最近一次 `/audit list` 或入群通知；**30 分钟内有效**。无需复制 `REQ-xxx` 长 ID。

## 日常推荐流程

适合开学季大量入群申请：

1. **`/audit record`** — 保持只记录模式（日常默认，勿用 `/audit off`）
2. **`/audit sync`** — 同步 NJUTable 学生数据（或启用定时同步，见下文）
3. 等待入群申请 → 收到通知
4. **`/audit list`** — 查看待处理；强匹配会标记为可分批
5. **`/audit release preview`** — 预览可分批通过的 strong 26 级申请
6. **`/audit release 10 confirm`** — 分批通过最多 10 条（间隔可配置，不改变当前 mode）
7. 弱匹配 / 需人工的仍用 **`/audit ok/no <n>`** 逐条处理
8. 定期 **`/audit unknown`** / **`/audit report`** 复盘未识别原因，反哺 aliases 或提醒新生改格式

## 日常使用

| 命令 | 说明 |
|------|------|
| `/audit` | 管理员首页（状态、待处理数、可分批数、下一步指引） |
| `/audit list [n]` | 待处理列表（带短编号 1、2、3…） |
| `/audit view <n>` | 查看第 n 条详情（含 QQ 匹配状态） |
| `/audit ok <n>` | 同意第 n 条（无需 confirm） |
| `/audit no <n> [理由]` | 拒绝第 n 条，可附理由 |
| `/audit release` | 分批放人帮助 + 当前可释放数量 |
| `/audit release preview` | 预览可分批 strong 申请（无 flag） |
| `/audit release <N> confirm` | 分批通过最多 N 条 strong 26 级 |
| `/audit release all confirm` | 分批通过（受 max_count 限制） |
| `/audit unknown [N]` | 近 7 天未识别汇总 + 样例 |
| `/audit report` | 运营统计（今日/累计、原因分布、同步摘要） |
| `/audit sync` | 手动同步 NJUTable / mock 学生数据 |
| `/audit sync status` | 定时同步状态与下次计划时间 |
| `/audit record` | 只记录，不自动放人（**日常推荐**） |
| `/audit manual` | 人工审核模式 |
| `/audit auto confirm` | 自动通过强匹配（需 confirm） |
| `/audit off confirm` | 完全停用（不记录 pending，**慎用**） |

**别名：** `/audit batch strong N confirm`、`/audit temp N confirm` 等价于 `/audit release N confirm`。  
**兼容：** `/audit process strong confirm` 仍可用，建议改用 `/audit release`。

**不提供** `/audit group *`。修改目标群请编辑插件配置 `target_group_ids` 后重启。

## 模式说明

| 模式 | 行为 | 适用场景 |
|------|------|----------|
| `record-only` | 记录申请、通知管理员，不自动 approve/reject；可用 release 分批 | **日常运营（推荐）** |
| `manual` | 每条申请需人工处理 | 严格人工把关 |
| `auto` | 仅 **强匹配** 26 级自动 approve | 流量稳定、规则已验证 |
| `off` | 不处理、不记录新申请 | 维护/放假，**非日常** |

- **`/audit off`（无 confirm）** 会提示：完全停用且不记录；日常请用 **`/audit record`**
- 默认 **不自动 reject**
- 弱匹配、专业模糊、QQ 辅助、非 26 级只会通知或需人工，不会自动拒绝
- **`/audit release` 不改变 runtime mode**，只是临时批量通过已积累的 strong 申请

## 分批放人

仅以下申请可进入 release 批次（全部满足）：

- `pending` 且未处理、`decision=approve`、`match_strength=strong`
- 目标群、`sub_type=add`（非 invite）、有有效 flag
- 学号 26 级、comment 无「学长/家长」等关键词

**不会批量通过：** manual_review、weak、auxiliary、非 26 级、invite、已处理。

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `batch_approve_interval_ms` | 3000 | 每条间隔（毫秒），防 QQ 频控 |
| `batch_approve_max_count` | 20 | `release all` 单次上限 |

## 未识别复盘

| 命令 | 用途 |
|------|------|
| `/audit unknown [N]` | 近 7 天 manual_review / 解析失败汇总 + 最近 N 条样例（默认 5，最大 30） |
| `/audit report` | 今日/累计 pending、auto/admin/reject、top 原因、sync 摘要、可分批 strong 数 |

根据 `unknown` 中的原因分类（信息不足、仅姓名、专业弱匹配、非 26 级等）调整 parser 标签、aliases，或更新群公告引导填写格式。

## 推荐学生填写格式

**强匹配（可 auto / 可 release）：**

```
张三 261220001 计算机类
李四 通知书编号：20260002 电子信息类
```

**弱匹配（需人工）：**

```
王五 电子
赵六 123456789
```

- 姓名 + 学号 或 姓名 + 通知书编号 → strong（需 26 级）
- 专业可选；专业别名支持模糊（电子→电子信息类等），但仍为 weak，不 auto approve
- QQ 仅作辅助匹配，单独 QQ 或 QQ+专业 **不会** 自动通过

## 定时同步

在 `student_source=nju_table` 时可启用后台定时同步：

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `auto_sync_enabled` | false | 启用定时同步 |
| `auto_sync_on_startup` | false | 启动时同步一次 |
| `auto_sync_interval_minutes` | 360 | 间隔（最小 10 分钟） |
| `auto_sync_notify_admin` | false | 失败时通知管理员 |

- **`/audit sync status`** 查看最近同步、来源（manual/auto）、下次计划时间
- 手动 `/audit sync` 与定时任务互斥（同一 lock）
- 同步失败 **保留旧缓存**

## 排查与高级命令

| 命令 | 说明 |
|------|------|
| `/audit help` | 分层帮助 |
| `/audit debug` | 技术状态（backend、data_dir、probe 等） |
| `/audit probe api` | 测试 AstrBot adapter 能否调用 OneBot API |
| `/audit probe last` | 最近原始入群事件 |
| `/audit stats` | 统计 |

### 旧命令（仍可用）

`/audit status`、`/audit pending`、`/audit request <id>`、`/audit approve <id> confirm`、`/audit reject <id> confirm`、`/audit mode ...`

## 功能概览

- 通过 AstrBot aiocqhttp 适配器接收 `request.group.add/invite`
- 解析入群验证 comment，匹配 NJUTable / mock 学生缓存
- OneBot 主动操作 **优先经 AstrBot aiocqhttp adapter**；HTTP 为可选 fallback
- 管理员命令仅 **私聊**；主动通知优先 `context.send_message` + UMO 缓存

## 安装

```bash
cd <AstrBot>/data/plugins
git clone https://github.com/Gu-Heping/astrbot_plugin_nju_qq_audit.git astrbot_plugin_nju_qq_audit
pip install -r astrbot_plugin_nju_qq_audit/requirements.txt
```

重启 AstrBot。

## 配置

在 AstrBot 插件管理面板配置（写入 `data/config/astrbot_plugin_nju_qq_audit_config.json`）。

### 必配项（生产）

| 配置项 | 说明 |
|--------|------|
| `target_group_ids` | 目标群号，逗号分隔。**为空时不处理任何入群申请** |
| `admin_qq_ids` | 管理员 QQ，逗号分隔 |
| `student_source` | `mock` 或 `nju_table` |
| `njutable_api_token` | SeaTable base API Token（`nju_table` 时） |
| `njutable_table_name` | 表名，默认 `考生信息-校对表` |

### 可选项

| 配置项 | 说明 |
|--------|------|
| `onebot_action_backend` | 默认 `astrbot_adapter`；adapter 不可用时改为 `http` |
| `onebot_http_url` | HTTP fallback 地址（通常不需要） |
| `onebot_access_token` | HTTP fallback token（如有） |
| `batch_approve_interval_ms` | 分批放人间隔，默认 3000 |
| `batch_approve_max_count` | 分批放人单次上限，默认 20 |
| `auto_sync_enabled` | 启用 NJUTable 定时同步，默认 false |
| `auto_sync_on_startup` | 启动时同步一次，默认 false |
| `auto_sync_interval_minutes` | 定时同步间隔（分钟，最小 10），默认 360 |
| `auto_sync_notify_admin` | 同步失败通知管理员，默认 false |
| `njutable_col_qq` | NJUTable QQ 列名，默认 `QQ` |

### 配置优先级

| 项 | 优先级 |
|----|--------|
| `mode` | `runtime.json` > 插件配置 > 内置默认 `record-only` |
| `target_group_ids` | **仅插件配置** |

### 安全默认值

- 默认 `mode=record-only`，不自动 reject
- `auto` 仅 strong match 自动 approve
- `flag` 必须来自原始 OneBot 事件
- 管理员回复与通知 **不含** flag/token/敏感字段

## NJUTable / SeaTable

1. API Token → Base Token（内存缓存，不落盘）
2. 分页读取 rows（limit ≤ 1000）
3. 审核只读本地 `students.cache.json`
4. 同步失败 **保留旧缓存**

## 隐私

以下字段 **不进入缓存、日志、管理员回复**：

身份证号、收件人、家庭地址、邮政编码、联系电话、联系手机

请勿提交 `data/plugin_data/astrbot_plugin_nju_qq_audit/` 下真实数据到 GitHub。

## 数据文件

```
data/plugin_data/astrbot_plugin_nju_qq_audit/
├── requests.json
├── audit.jsonl
├── runtime.json
├── admin_sessions.json   # 管理员 UMO 缓存
├── list_cache.json       # 短编号映射（v0.3.0+）
├── students.cache.json
├── sync_state.json
├── probe_events.jsonl
└── probe_state.json
```

## 开发测试

```bash
pip install aiohttp pytest pytest-asyncio
pytest tests/
```

## 要求版本

- AstrBot: `>=4.16,<5`
- Python: 3.10+

## 升级注意（v0.3.3+）

- 升级后请 **完整重启 AstrBot**，勿仅热重载插件（避免 sync task / release lock 状态错乱）
- 新增 `requests.json` 字段（如 `match.qq_match`）向后兼容，旧数据可正常读取
- `sync_state.json` 新增 `next_sync_at`、`last_sync_source`，缺字段时使用默认值

## 仓库

https://github.com/Gu-Heping/astrbot_plugin_nju_qq_audit
