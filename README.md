# astrbot_plugin_nju_qq_audit

南京大学 26 级新生 QQ 群入群审核 **AstrBot 插件**（非独立 Node 服务）。

## 快速开始（管理员）

1. 在 AstrBot 插件面板配置 `target_group_ids`、`admin_qq_ids`、学生数据源（mock 或 NJUTable）
2. **完整重启 AstrBot**（不要只热重载插件）
3. 私聊机器人发送 `/audit`，确认首页状态正常
4. 发送 `/audit probe api`，确认「审批接口可用」
5. 发送 `/audit sync` 同步学生数据
6. 保持 `record-only`，用小号申请入群
7. 收到通知后发送 `/audit list`，用 `/audit ok 1` 或 `/audit no 1 理由` 处理
8. 确认流程无误后，发送 `/audit auto confirm` 开启自动强匹配

> 短编号来自最近一次 `/audit list` 或入群通知；**30 分钟内有效**。无需复制 `REQ-xxx` 长 ID。

## 日常使用

| 命令 | 说明 |
|------|------|
| `/audit` | 管理员首页（状态、待处理数、下一步指引） |
| `/audit list [n]` | 待处理列表（带短编号 1、2、3…） |
| `/audit view <n>` | 查看第 n 条详情 |
| `/audit ok <n>` | 同意第 n 条（无需 confirm） |
| `/audit no <n> [理由]` | 拒绝第 n 条，可附理由 |
| `/audit sync` | 同步 NJUTable / mock 学生数据 |
| `/audit record` | 只记录，不自动放人（默认） |
| `/audit manual` | 人工审核模式 |
| `/audit auto confirm` | 自动通过强匹配（需 confirm） |
| `/audit off confirm` | 暂停处理（需 confirm） |

**不提供** `/audit group *`。修改目标群请编辑插件配置 `target_group_ids` 后重启。

## 模式说明

| 模式 | 行为 |
|------|------|
| `record-only` | 只记录申请，通知管理员，不自动 approve/reject |
| `manual` | 每条申请需人工处理 |
| `auto` | 仅 **强匹配**（姓名+学号 / 姓名+通知书编号）自动 approve |
| `off` | 不处理新申请 |

- 默认 **不自动 reject**
- 弱匹配、信息不足只会通知，不会自动拒绝

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

## 仓库

https://github.com/Gu-Heping/astrbot_plugin_nju_qq_audit
