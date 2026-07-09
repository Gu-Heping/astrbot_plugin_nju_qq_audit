# astrbot_plugin_nju_qq_audit

南京大学 26 级新生 QQ 群入群审核 **AstrBot 插件**（非独立 Node 服务）。

## 功能概览

- 通过 AstrBot aiocqhttp 适配器接收 `request.group.add/invite`（`event.message_obj.raw_message`）
- 解析入群验证 comment，匹配 NJUTable / mock 学生缓存
- 模式：`record-only` / `manual` / `auto` / `off`
- 默认 **record-only**，**不自动 reject**
- `auto` 模式仅 **strong match**（姓名+学号 / 姓名+通知书编号）自动 approve
- OneBot 主动操作 **优先经 AstrBot aiocqhttp adapter**；HTTP 为可选 fallback
- 管理员命令回复使用 AstrBot `event.plain_result(...)`；主动通知优先 `context.send_message`
- 保留 OneBot 探针（`/audit probe *`）

## 安装

```bash
cd <AstrBot>/data/plugins
git clone https://github.com/Gu-Heping/astrbot_plugin_nju_qq_audit.git astrbot_plugin_nju_qq_audit
pip install -r astrbot_plugin_nju_qq_audit/requirements.txt
```

重启 AstrBot 或在 WebUI 重载插件。

## 配置

在 AstrBot 插件管理面板配置（写入 `data/config/astrbot_plugin_nju_qq_audit_config.json`）。

**不再使用 `.env` 作为正式配置入口。**

### 必配项（生产）

| 配置项 | 说明 |
|--------|------|
| `target_group_ids` | 目标群号，逗号分隔。**为空时不处理任何入群申请** |
| `admin_qq_ids` | 管理员 QQ，逗号分隔 |
| `student_source` | `mock` 或 `nju_table` |
| `njutable_api_token` | SeaTable base API Token（`nju_table` 时） |
| `njutable_table_name` | 表名，默认 `考生信息-校对表` |

### 可选项（通常无需配置）

| 配置项 | 说明 |
|--------|------|
| `onebot_action_backend` | 默认 `astrbot_adapter`；仅 adapter 无法调用 API 时改为 `http` |
| `onebot_http_url` | HTTP fallback 地址。**通常不需要配置** |
| `onebot_access_token` | HTTP fallback token（如有） |

> 通常不需要配置 `onebot_http_url`；SnowLuma 只需通过 OneBot v11 连接 AstrBot。仅当 `/audit probe api` 显示 adapter 不可用、需启用 HTTP fallback 时，设置 `onebot_action_backend=http` 并填写 URL。

### 配置优先级

| 项 | 优先级 |
|----|--------|
| `mode` | `runtime.json` > 插件配置 > 内置默认 `record-only` |
| `target_group_ids` | **仅插件配置**（不提供 QQ 指令修改） |

### 安全默认值

- 默认 `mode=record-only`
- 默认 `onebot_action_backend=astrbot_adapter`
- 不自动 reject
- 专业/书院 weak match 不能自动通过
- `flag` 必须来自原始 OneBot 事件，不可自造
- 管理员只能通过 **request id** 审批，不能传 flag

## 命令（仅私聊）

| 命令 | 说明 |
|------|------|
| `/audit help` | 帮助 |
| `/audit status` | 运行状态 |
| `/audit mode` | 查看/切换 runtime mode |
| `/audit mode auto confirm` | 切换 auto（需 confirm） |
| `/audit mode reset confirm` | 恢复插件配置 mode |
| `/audit sync` | 同步学生数据 |
| `/audit pending [n]` | pending 列表 |
| `/audit request <id>` | 请求详情（不含 flag） |
| `/audit approve <id> confirm` | 人工同意 |
| `/audit reject <id> confirm` | 人工拒绝 |
| `/audit process strong confirm` | 批量处理 strong pending |
| `/audit stats` | 统计 |
| `/audit probe status\|last\|recent\|raw\|api` | 探针 |

兼容旧命令：`/audit_probe status|last|recent`

**不提供** `/audit group *` 命令。修改目标群请编辑插件配置 `target_group_ids` 后重载。

## 真实环境测试流程

1. SnowLuma 通过 OneBot v11 连接 AstrBot（无需额外配置 HTTP 端口）
2. 在 AstrBot 插件面板配置 `target_group_ids`、`admin_qq_ids`、NJUTable
3. `/audit status` 确认 mode、backend 与群号
4. `/audit probe api` 确认 adapter 可调用 OneBot API
5. `/audit sync` 同步学生缓存
6. 保持 `record-only`，用小号申请入群
7. `/audit pending` → `/audit request <id>`
8. 确认无误后 `/audit mode auto confirm`
9. `/audit probe last` 确认插件层收到 `request.group.add`

## NJUTable / SeaTable

1. API Token → Base Token（内存缓存，不落盘）
2. Base Token → 分页读取 rows（limit ≤ 1000）
3. 审核只读本地 `students.cache.json`
4. 同步失败 **保留旧缓存**

默认仅同步 `njutable_allowed_statuses` 中的状态（默认 `对外公布`）。`有问题` 硬排除。

## 隐私

以下字段 **不进入缓存、日志、管理员回复**：

身份证号、收件人、家庭地址、邮政编码、联系电话、联系手机

请勿提交 `data/plugin_data/astrbot_plugin_nju_qq_audit/` 下真实数据到 GitHub。

## 数据文件

```
data/plugin_data/astrbot_plugin_nju_qq_audit/
├── requests.json
├── audit.jsonl
├── runtime.json          # 仅 mode override
├── admin_sessions.json   # 管理员 UMO 缓存
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
