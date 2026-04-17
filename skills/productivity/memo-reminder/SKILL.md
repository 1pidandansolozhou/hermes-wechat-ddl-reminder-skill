---
name: memo-reminder
description: 微信备忘/DDL提醒专用技能。自动提取事件与DDL并按规则创建提醒，优先 Apple Reminders，不可用时回退 Hermes cron。
version: 1.3.0
author: Custom local skill
platforms: [macos]
metadata:
  hermes:
    tags: [reminder, memo, todo, cron, apple-reminders, wechat, ddl]
---

# Memo Reminder

用于“微信备忘提醒 / 通知转发 / DDL管理”的专用技能，支持两种模式：

1. Apple Reminders（同步到 iPhone/iPad）
2. Hermes cron（Agent 本地提醒，免系统权限）

## 触发场景

- 用户说“提醒我”“做个备忘”“记一下这个DDL”
- 用户在微信里转发他人通知，要求记录截止时间
- 需要按 DDL 的提前节点自动提醒
- 需要把提醒发回当前聊天会话（微信 origin）
- 用户问“最近的DDL / 最近截止事项 / 这周有什么DDL”

## 路由规则

1. 如果用户明确要“手机同步提醒”，优先 Apple Reminders。
2. 如果 `remindctl status` 不是 `Authorized`，自动回退 Hermes cron。
3. 没有给出 DDL 时，先追问最少必要信息（日期/时间）。
4. 没给具体时分时，默认按当天 `20:00` 处理并在确认消息中说明。

## Apple Reminders 模式（优先）

检查权限：

```bash
remindctl status
```

创建提醒：

```bash
remindctl add --title "提醒内容" --list Personal --due "2026-04-18 09:00"
```

查看提醒：

```bash
remindctl today
remindctl tomorrow
remindctl all
```

## Hermes cron 回退模式（推荐默认）

统一使用下面的脚本来计算提醒节点并创建 cron（支持智能解析微信原文）：

```bash
python3 ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py \
  --title "xx事件" \
  --ddl "2026-04-20 18:00" \
  --detail "具体内容（可选）" \
  --deliver origin
```

或直接喂微信原文（推荐）：

```bash
python3 ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py \
  --text "老师通知：4月20日18:00前提交统计学作业pdf和代码，逾期扣分" \
  --deliver origin
```

查询最近DDL（按时间顺序）：

```bash
python3 ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py \
  --list-upcoming \
  --limit 20
```

### DDL提醒规则（核心）

1. 当 `DDL - 当前时间 > 48小时`：创建两个提醒  
- `T-24h`（提前一天）
- `T-6h`（当天提前六小时）

2. 当 `6小时 < DDL - 当前时间 <= 48小时`：创建一个提醒  
- `T-6h`

3. 当 `DDL - 当前时间 <= 6小时`：创建一个“临近提醒”  
- 立即（约1分钟后）提醒一次

4. 过期 DDL：不创建提醒，提示用户该事项已过期并建议更新时间。

5. 智能增强（在不违反以上规则前提下）：
- 高优先级事项可额外增加 `T-72h` 与 `T-1h` 提醒。
- 自动去重：同事件同DDL默认不重复创建（可 `--force` 覆盖）。
- 灵活时间窗口：当提醒点落在 `00:00-08:59`，优先前移到前一天 `22:00`（避免清晨打扰），且不会回退到过去时间。

## 分类规则

收到新事项时先分类（用于组织和摘要，不影响格式）：

- 学业 / 课程 / 作业 / 考试
- 工作 / 实习 / 项目 / 面试
- 会议 / 活动 / 报名
- 缴费 / 证件 / 申请
- 生活 / 其他

## 提醒消息格式（强制）

提醒正文必须遵循：

```text
【事件提醒】
xx事件  XX时间
具体内容
```

- 第三行“具体内容”可按情况省略。
- 时间统一使用北京时间（Asia/Shanghai）。

## 执行检查清单

创建前：

1. 提取 `事件名 / DDL / 关键信息`。
2. 判断是否已有同事件同DDL提醒，避免重复创建。
3. 按规则生成提醒节点。

创建后必须回报：

1. 使用了哪种模式（Apple Reminders / Hermes cron）
2. 提醒标题
3. 提醒触发时间（T-24h / T-6h / 临近）
4. 对应 ID（cron job_id 或 Reminders 列表定位信息）

查询“最近DDL”时必须回报：

1. 按 DDL 时间升序
2. 每项包含：事件名 / 时间 / 分类 / 优先级 / 剩余时间
3. 无未过期事项时明确回复“暂无未过期DDL事项”
