---
name: memo-reminder
description: >
  微信备忘/DDL提醒完整工作流。提取事件与DDL，按规则创建微信提醒，同步到 Mac 提醒事项。
  智能判断提醒节点，规范日期/星期校验，统一格式输出。
version: 2.0.0
author: Oceanus
platforms: [macos]
metadata:
  hermes:
    tags: [reminder, memo, todo, cron, apple-reminders, wechat, ddl]
---

# Memo Reminder 完整工作流

## 触发场景

- 用户在微信里发"待办/通知/转发消息"
- 用户说"提醒我"“做个备忘"“记一下DDL"
- 用户转发他人通知，要求记录截止时间
- 用户查询"最近DDL““这周有什么事"

## 执行流程（严格按顺序）

### Step 1 — 提取与分类

从用户消息中提取：
- 事件名
- DDL 日期/时间
- 地点
- 关键要求（正装、打印简历、报告等）

分类：学业、工作、会议/活动、缴费/申请、生活、其他

### Step 2 — 日期与星期校验

**原则**：不凭记忆脑补星期，但也不每次都走 shell。

**方法**：用项目内 `weekday.py` 脚本或 Python 直接计算：

```bash
python3 ~/.hermes/skills/productivity/memo-reminder/scripts/weekday.py YYYY-MM-DD
# 输出：一/二/三/四/五/六/日
```

```python
from datetime import datetime
WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]
dt = datetime(2026, 4, 25)
print(WEEKDAYS[dt.weekday()])  # 六
```

**什么时候走 shell**：
- 查"今天"几月几号 → `date '+%Y-%m-%d %a'` 走一次
- 新会话首次涉及日期 → 验证一次，后续直接用 Python
- 已确认过的日期 → 直接引用，不重复验证

### Step 3 — 智能提醒节点判断

以当前北京时间为基准：

1. `DDL - 现在 > 48小时`：
   - T-24h（提前一天）
   - T-6h（当天提前六小时）

2. `6小时 < DDL - 现在 ≤ 48小时`：
   - T-6h

3. `DDL - 现在 ≤ 6小时`：
   - 临近提醒（约1分钟后）

**智能增强**（高优先级事项，不违反上述规则的前提下）：
- `T-72h`（高优先级且 DDL > 72h）
- `T-1h`（高优先级且 DDL > 1h）

**时间窗口优化**：提醒点落在 `00:00-08:59` 时，前移到前一天 `22:00`，避免清晨打扰。不会回退到过去时间。

### Step 4 — 创建微信提醒

使用 Hermes cronjob 工具创建，默认通道为 weixin/origin。

**微信提醒消息格式**：

```text
【事件提醒】
xx事件  YYYY-MM-DD HH:mm
具体内容（可省略）
```

用户偏好的简洁版：
```text
🔔 事件名
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 时间
📍 地点
📝 关键要求
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰ 提醒节点
```

### Step 5 — 同步 Mac 提醒事项

**触发条件**：仅在**创建新事件**或**更新已有事件**时同步。**查询时不操作 Mac**。

**格式规范**：
- 标题：`🔔 事件名`
- 备注用 emoji 分割：
  - `📅 YYYY-MM-DD(星期X) HH:mm`
  - `📍 地点`
  - `📝 关键要求/注意事项`
- **禁止**在备注中写动态倒计时（如"距DDL还有X小时"），因为 Mac 提醒是静态的，倒计时会随时间变得不准。

**去重复逻辑**：
- 查询 Mac 提醒事项是否已有同名/同事件
- 已有 → 删除旧的，重新创建（复写优化）
- 没有 → 直接新建

**命令示例**：

```bash
# 检查是否已有
remindctl all --json

# 删除旧的
remindctl delete <ID> --force

# 创建新的
remindctl add --title "🔔 事件名" \
  --due "YYYY-MM-DD HH:mm" \
  --notes "📅 日期
📍 地点
📝 要求"
```

### Step 6 — 回报

**必须包含**：
1. 事件名、DDL时间（含正确星期）、分类、优先级
2. 微信提醒节点（T-24h / T-6h / 临近）及 cron job_id
3. Mac 提醒事项同步状态（已同步 / 未同步）

**用户偏好**：
- 反馈极度简洁，不展示中间分析过程
- 使用 emoji 和单行分隔线 `━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`
- 不追加礼貌语

## 查询最近DDL

**触发条件**：用户问"最近DDL““这周有什么事"“还有什么没完成"。

**执行**：
1. 读取 Hermes cron job 列表
2. 按 DDL 时间升序排列
3. 展示事件名 + 日期（含星期）+ 剩余时间

**禁止**：查询时操作 Mac 提醒事项。

## 已知陷阱

- 4月25日 = 周六，4月26日 = 周日
- macOS 不支持 `date -d`，必须用 `date -j -f` 或 Python
- 提醒点在清晨时自动前移到前一天22:00
- Mac 备注不写动态倒计时
- 查询不同步 Mac

## 关键文件

- `scripts/create_ddl_reminders.py` — DDL 解析与 cron 创建
- `scripts/weekday.py` — 日期转星期（省 token）
