# astrbot_plugin_nju_qq_audit

南京大学新生 QQ 群入群审核插件 — **当前仅为 Phase 0 探针**，不执行任何自动审核、approve/reject 操作。

## 当前阶段说明

本插件 **不是正式审核插件**。它只做一件事：在 AstrBot 插件层监听 OneBot 原始事件，确认能否通过 `event.message_obj.raw_message` 拿到入群申请、群邀请、退群等 `request` / `notice` 事件。

- 不调用 `set_group_add_request`
- 不 approve / reject 任何入群请求
- 不 `stop_event`，不影响其他插件

## 安装

1. 克隆或下载本仓库
2. 将整个目录放到 AstrBot 插件目录：

```text
<AstrBot 根目录>/data/plugins/astrbot_plugin_nju_qq_audit/
```

目录结构示例：

```text
data/plugins/astrbot_plugin_nju_qq_audit/
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── requirements.txt
├── probe/
│   ├── __init__.py
│   ├── sanitizer.py
│   ├── event_store.py
│   └── formatter.py
└── README.md
```

3. 重启 AstrBot，在 WebUI 或配置文件中确认插件已加载

## 配置

插件配置保存在 `data/config/astrbot_plugin_nju_qq_audit_config.json`，也可在 AstrBot 管理面板中编辑。

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `probe_enabled` | bool | `true` | 是否启用事件探针 |
| `log_raw_event` | bool | `false` | 是否保存脱敏后的 raw event |
| `admin_qq_ids` | string | `""` | 管理员 QQ，逗号分隔 |
| `target_group_ids` | string | `""` | 目标群号，逗号分隔；留空记录所有群 |
| `max_recent_events` | int | `20` | 内存中保留的最近事件数 |

### 配置示例

```json
{
  "probe_enabled": true,
  "log_raw_event": false,
  "admin_qq_ids": "123456789",
  "target_group_ids": "1093442531",
  "max_recent_events": 20
}
```

### 权限说明

- 所有 `/audit_probe` 命令 **仅私聊可用**
- `admin_qq_ids` 已配置时，仅管理员可执行全部命令
- `admin_qq_ids` 为空时（首次调试）：
  - 允许：`/audit_probe status`、`/audit_probe last`
  - 拒绝：`/audit_probe recent`、`/audit_probe raw`、`/audit_probe clear`（并提示未配置管理员）

## 命令

| 命令 | 说明 |
|------|------|
| `/audit_probe` | 显示命令树 / 帮助 |
| `/audit_probe status` | 探针状态、最近事件数、数据目录等 |
| `/audit_probe last` | 最近一条事件摘要 |
| `/audit_probe recent` | 最近 10 条事件摘要 |
| `/audit_probe raw` | 查看脱敏 raw（需 `log_raw_event=true`，仅管理员） |
| `/audit_probe clear confirm` | 清空记录（仅管理员） |

## 测试步骤

1. 启动 **AstrBot** + **SnowLuma**（OneBot v11 反向 WS）
2. 确认插件加载，必要时配置 `admin_qq_ids`
3. 触发以下事件：
   - 让机器人 **退群** → 期望 `notice_type=group_decrease`
   - **邀请机器人入群** → 期望 `post_type=request`, `request_type=group`, `sub_type=invite`
   - 用小号 **申请入群** → 期望 `post_type=request`, `request_type=group`, `sub_type=add`
4. 私聊机器人发送：
   - `/audit_probe last`
   - `/audit_probe recent`
5. 查看数据文件（可选）：

```text
<AstrBot>/data/plugin_data/astrbot_plugin_nju_qq_audit/
├── probe_events.jsonl
└── probe_state.json
```

## 如何判断结果

### 成功（插件层可直接处理）

`/audit_probe last` 显示：

- `raw_message_present: yes`
- `post_type=request`, `request_type=group`, `sub_type=add`（入群申请）

说明 AstrBot 插件层能直接拿到 OneBot 入群申请，后续正式审核可在 AstrBot 插件内实现。

### 失败（需 Phase 1 备选方案）

- `message_str` 为空
- `raw_message_present: no` 或 `raw_message_missing: true`

说明插件层拿不到原始 payload，正式插件可能需要 **插件内直连 SnowLuma WebSocket 收事件 + HTTP action 发操作**。

## 安全提示

- 本探针 **不会** approve/reject 入群请求
- **不会** 保存完整 `flag` / `token` / `access_token`
- 默认 `log_raw_event=false`，不保存完整 raw event
- `probe_events.jsonl` 含群事件摘要，**不要提交到 GitHub**
- 生产环境请配置 `admin_qq_ids`

## 数据文件

| 文件 | 说明 |
|------|------|
| `probe_events.jsonl` | 脱敏事件摘要（JSONL，append-only） |
| `probe_state.json` | 探针状态（最近 request.group 时间、累计条数） |

## 依赖

仅使用 Python 标准库，无额外 pip 依赖。

## 要求版本

- AstrBot: `>=4.16,<5`

## 仓库

https://github.com/Gu-Heping/astrbot_plugin_nju_qq_audit
