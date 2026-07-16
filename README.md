# astrbot_plugin_nju_qq_audit

南京大学 26 级新生 QQ 群入群审核 **AstrBot 插件**（非独立 Node 服务）。当前版本 **v0.4.3**。

支持 **本科** 与 **研究生** 两套独立审核通道（群号 / NJUTable / 缓存分离；共用事件接入与管理员命令）。

## 快速开始（管理员）

1. 在 AstrBot 插件面板配置 `target_group_ids`、`admin_qq_ids`、学生数据源（mock 或 NJUTable）
2. **完整重启 AstrBot**（不要只热重载插件）
3. 私聊机器人发送 `/audit`，确认首页状态正常
4. 发送 `/audit probe api`，确认「审批接口可用」
5. 发送 `/audit sync` 同步学生数据
6. 保持 **`record-only`**（默认），用小号申请入群
7. 收到通知后发送 `/audit list`，用 `/audit ok 1` 或 `/audit no 1 理由` 处理
8. 名单刚更新、历史 pending 未匹配时用 **`/audit catchup preview`**，再 **`/audit catchup confirm`** 补放
9. 仅用当前本地缓存分批时用 **`/audit release preview`** / **`/audit release 10 confirm`**
10. 确认流程无误后，可选 `/audit auto confirm` 开启自动强匹配（本科 26 级 strong；若启用研究生则含研究生 strong）

> 短编号来自最近一次 `/audit list` 或入群通知；**30 分钟内有效**。无需复制 `REQ-xxx` 长 ID。

## 日常推荐流程

适合开学季大量入群申请：

1. **`/audit record`** — 保持只记录模式（日常默认，勿用 `/audit off`）
2. **`/audit sync`** — 同步 NJUTable 学生数据（或启用定时同步，见下文）
3. 等待入群申请 → 收到通知
4. **`/audit list`** — 查看待处理（会轻量对账 QQ 侧申请队列）；强匹配会标记为可分批
5. 校对表有更新、或大量 pending 还是 weak/none 时：
   - **`/audit catchup preview`** → **`/audit catchup confirm`**（先 sync 再重算再放人）
6. 名单已足够新、只想放当前 strong：
   - **`/audit release preview`** → **`/audit release 10 confirm`**
7. 弱匹配 / 需人工的仍用 **`/audit ok/no <n>`** 逐条处理
8. 怀疑解析或名单问题时用 **`/audit lookup 张三 261220001`**（不改任何申请）
9. 定期 **`/audit unknown`** / **`/audit report`** 复盘未识别原因

## 日常使用

| 命令 | 说明 |
|------|------|
| `/audit` | 管理员首页（状态、待处理数、可分批数、下一步指引） |
| `/audit list [n]` | 待处理列表（带短编号；列出前自动对账 QQ 侧） |
| `/audit view <n>` | 查看第 n 条详情（含 QQ 匹配状态） |
| `/audit lookup <姓名> <学号> [专业]` | 用当前缓存校对表查询匹配（strong/weak/none），不改 pending |
| `/audit ok <n>` | 同意第 n 条（无需 confirm） |
| `/audit no <n> [理由]` | 向 QQ 发起拒绝，可附理由 |
| `/audit dismiss <n> confirm <原因>` | **本地**关闭无效申请（不调 QQ）；原因必填 |
| `/audit sweep preview` | 预览将本地关闭的**非 strong** pending（先 rematch，保留 strong） |
| `/audit sweep confirm <原因>` | 一键 dismiss 全部非 strong（不调 QQ） |
| `/audit mark-external <n> confirm` | 确认 QQ 侧已处理，标为 external |
| `/audit stale [n]` | 查看 stale 队列（QQ 侧已失效） |
| `/audit restore <n> confirm` | 将 stale 恢复为 pending |
| `/audit release` | 分批放人帮助 + 当前可释放数量 |
| `/audit release preview` | 按**当前缓存**重算后预览可分批 strong |
| `/audit release <N> confirm` | 分批通过最多 N 条 strong 26 级（不改变 mode） |
| `/audit release all confirm` | 分批通过（受 max_count 限制） |
| `/audit catchup` | 同步名单并补放帮助 |
| `/audit catchup preview` | **先 sync** + 重算全部 pending + 预览（不放人） |
| `/audit catchup confirm` | sync + 重算 + 分批通过（上限内） |
| `/audit catchup 10 confirm` | 同上，最多 10 条 |
| `/audit unknown [N]` | 近 7 天未识别汇总 + 样例 |
| `/audit report` | 运营统计（今日/累计、原因分布、同步摘要） |
| `/audit sync` | 手动同步本科 NJUTable / mock |
| `/audit sync grad` / `/audit sync-grad` | 同步研究生名单（独立表/token） |
| `/audit list [n]` | 全部待处理 |
| `/audit list grad` / `undergraduate` | 按 profile 筛选 pending |

| `/audit record` | 只记录，不自动放人（**日常推荐**） |
| `/audit manual` | 人工审核模式 |
| `/audit auto confirm` | 自动通过强匹配（需 confirm） |
| `/audit off confirm` | 完全停用（不记录 pending，**慎用**） |

**别名：** `/audit batch strong N confirm`、`/audit temp N confirm` 等价于 `/audit release N confirm`。  
**兼容：** `/audit process strong confirm` 仍可用，建议改用 `/audit release`。

**语义区分：**

| 操作 | 效果 |
|------|------|
| `/audit no` | 向 QQ 发起拒绝 |
| `/audit mark-external` | QQ 侧已处理，本地记 external |
| `/audit dismiss` | 本地认定无效并关闭单条，**不调用 QQ** |
| `/audit sweep` | 本地批量关闭**非 strong** pending，**不调用 QQ**（保留 strong） |

**不提供** `/audit group *`。修改目标群请编辑插件配置 `target_group_ids` 后重启。

## catchup vs release

| | `catchup` | `release` |
|--|-----------|-----------|
| 是否先 `/audit sync` | 是 | 否 |
| pending 重算 | 是（相对最新校对表） | 是（相对**当前本地缓存**） |
| 适用 | 校对表刚更新、历史 pending 未匹配 | 名单已新，只想批量放 strong |

两者都只通过 strong 26 级 pending，**都不改变** runtime mode。

## 模式说明

| 模式 | 行为 | 适用场景 |
|------|------|----------|
| `record-only` | 记录申请、通知管理员，不自动 approve/reject；可用 release/catchup 分批 | **日常运营（推荐）** |
| `manual` | 每条申请需人工处理 | 严格人工把关 |
| `auto` | 仅 **强匹配** 26 级自动 approve | 流量稳定、规则已验证 |
| `off` | 不处理、不记录新申请 | 维护/放假，**非日常** |

- **`/audit off`（无 confirm）** 会提示：完全停用且不记录；日常请用 **`/audit record`**
- 默认 **不自动 reject**
- 弱匹配、专业模糊、QQ 辅助、非 26 级只会通知或需人工，不会自动拒绝
- pending 上修改验证信息、或拒绝后改答案再申请：若变为 strong 且 mode=`auto`，仍会自动通过（见下文「再申请」）

## 分批放人

仅以下申请可进入 release/catchup 批次（全部满足）：

- `pending` 且未处理、`decision=approve`、`match_strength=strong`
- 目标群、`sub_type=add`（非 invite）、有有效 flag
- 学号 26 级、comment 无「学长/家长」等关键词

**不会批量通过：** manual_review、weak、auxiliary、非 26 级、invite、已处理。

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `batch_approve_interval_ms` | 3000 | 每条间隔（毫秒），防 QQ 频控 |
| `batch_approve_max_count` | 20 | `release all` / catchup 单次上限 |

## 再申请与 debounce

- **`processed`(approve/reject)**、**`external`**：同 flag 允许新建 attempt（`reapply_of` / `attempt_no`），旧记录保留
- **`stale` / `ignored` / `dismissed`**：同 flag 永久忽略，不自动复活
- **`/audit sweep`**：清理 QQ 侧已拒但未上报留下的非 strong 僵尸 pending（本地 dismiss）
- **15 秒 burst**（`reapply_debounce_seconds`，默认 15）：仅拦**同一答案**的平台连发/重放
- **comment 已变**（如拒绝后改正文重申）：立即新建 attempt；`auto` + strong 会自动通过
- 退群（`group_decrease`）后带 `reapply_eligible` 时，可立即再申请（绕过 debounce）

## 未识别复盘

| 命令 | 用途 |
|------|------|
| `/audit unknown [N]` | 近 7 天 manual_review / 解析失败汇总 + 最近 N 条样例（默认 5，最大 30） |
| `/audit report` | 今日/累计 pending、auto/admin/reject、top 原因、sync 摘要、可分批 strong 数 |
| `/audit lookup …` | 不改状态，直接查当前缓存能否 strong/weak 命中 |

根据 `unknown` 中的原因分类（信息不足、仅姓名、专业弱匹配、非 26 级等）调整 parser 标签、aliases，或更新群公告引导填写格式。名单过旧请先 `/audit sync`。

## 推荐学生填写格式

**强匹配（可 auto / 可 release / catchup）：**

```
张三 261220001 计算机类
李四 通知书编号：20260002 电子信息类
```

也支持自然语言，如：`我是张三，学号261220001，专业是计算机科学与技术`。

**弱匹配（需人工）：**

```
王五 电子
赵六 123456789
```

- 姓名 + 学号 或 姓名 + 通知书编号 → strong（需 26 级）
- 专业可选；专业别名支持模糊（电子→电子信息类等），但仍为 weak，不 auto approve
- QQ 仅作辅助匹配，单独 QQ 或 QQ+专业 **不会** 自动通过

## 研究生审核（v0.4.0）

- 配置：`grad_enabled`、`grad_target_group_ids`、独立 `grad_njutable_*`（勿与本科 token/表/群重叠）
- 缓存：`grad_students.cache.json`、`grad_sync_state.json`
- 强匹配：姓名 + 硕士/博士 + 专业名称（模糊）或专业代码，且唯一
- **不读取**「证件号码末三位」；不自动 reject
- 命令：`/audit sync-grad`、`/audit list grad`

## 定时同步

在 `student_source=nju_table` 时可启用后台定时同步：

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `auto_sync_enabled` | false | 启用定时同步 |
| `auto_sync_on_startup` | false | 启动时同步一次 |
| `auto_sync_interval_minutes` | 360 | 间隔（最小 10 分钟） |
| `auto_sync_notify_admin` | false | 失败时通知管理员 |

- **`/audit sync status`** 查看最近同步、来源（manual/auto）、下次计划时间
- 手动 `/audit sync` 与定时任务互斥（同一 lock）；忙时不覆盖上次成功状态
- 同步失败 **保留旧缓存**；失败会记录 `failed:异常类型` 便于排查

## `/audit list` 对账（QQ 侧）

列出待处理前会轻量拉取 `get_group_system_msg`，与本地 pending 对齐：

- 匹配优先级：完整 flag → `slreq` 解析 → `group_id+requester_uin` → comment 辅助
- 外部拒绝推断较保守，需多次空快照等条件
- **SnowLuma 一次最多约 20 条**：达到上限时标记 `snapshot_saturated`，**禁止**「队列里看不到就当已拒绝」；请 `/audit dismiss` 或人工处理

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `audit_list_reconcile_timeout_ms` | 4000 | 对账超时 |
| `audit_list_reject_confirm_snapshots` | 2 | 推断外部拒绝所需空快照次数 |
| `audit_list_reject_wait_seconds` | 30 | 推断外部拒绝等待窗口 |

## 排查与高级命令

| 命令 | 说明 |
|------|------|
| `/audit help` | 分层帮助 |
| `/audit debug` | 技术状态（版本、duplicate/reconcile policy、backend、probe 等） |
| `/audit probe api` | 测试 AstrBot adapter 能否调用 OneBot API |
| `/audit probe last` | 最近原始入群事件 |
| `/audit cleanup failed` | 列出疑似失败/可重试项 |
| `/audit stats` | 统计 |

### 旧命令（仍可用）

`/audit status`、`/audit pending`、`/audit request <id>`、`/audit approve <id> confirm`、`/audit reject <id> confirm`、`/audit mode ...`

## 功能概览

- 通过 AstrBot aiocqhttp 适配器接收 `request.group.add/invite`、`notice.group_decrease` 等
- 解析入群验证 comment，匹配 NJUTable / mock 学生缓存
- OneBot 主动操作 **优先经 AstrBot aiocqhttp adapter**；HTTP 为可选 fallback
- 管理员命令仅 **私聊**；主动通知优先 adapter / UMO 缓存

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

### 常用可选项

| 配置项 | 说明 |
|--------|------|
| `onebot_action_backend` | 默认 `astrbot_adapter`；adapter 不可用时改为 `http` |
| `onebot_http_url` / `onebot_access_token` | HTTP fallback（通常不需要） |
| `batch_approve_interval_ms` / `batch_approve_max_count` | 分批放人间隔与上限 |
| `auto_sync_*` | 定时同步（见上文） |
| `reapply_debounce_seconds` | 终态同答案 burst 窗口，默认 15；**改答案重申不受限** |
| `audit_list_reconcile_*` | `/audit list` 对账超时与外部拒绝推断 |
| `njutable_col_qq` | NJUTable QQ 列名，默认 `QQ` |

完整列名映射见 `_conf_schema.json`。

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
├── requests.json          # by_id / by_flag / seen_fingerprints / membership
├── audit.jsonl
├── runtime.json
├── admin_sessions.json    # 管理员 UMO 缓存
├── list_cache.json        # 短编号映射
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

## 升级注意

- 升级后请 **完整重启 AstrBot**，勿仅热重载插件
- **v0.4.3**：通知 QQ 行展示真实昵称（非申请姓名）；群名支持 get_group_info 回退
- **v0.4.2**：管理员通知统一中文字段，并支持群名缓存展示
- **v0.4.1**：自动通过成功/失败通知改为可读申请人摘要
- **v0.4.0**：新增研究生通道；`grad_*` 配置与本科分离；重叠群不处理
- **v0.3.23**：新增 `/audit sweep` 批量本地关闭非 strong pending
- **v0.3.22**：拒绝后改答案重申的 debounce 行为变更；`duplicate_policy_version=v8-reject-comment-change-bypass-burst`
- **v0.3.20+**：`/audit list` 依赖 `get_group_system_msg`；SnowLuma 20 条上限时不会把缺失当成外部拒绝
- `requests.json` / `sync_state.json` 新增字段向后兼容

## 仓库

https://github.com/Gu-Heping/astrbot_plugin_nju_qq_audit
