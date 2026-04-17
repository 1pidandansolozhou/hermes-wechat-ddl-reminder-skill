# Hermes 微信 DDL 提醒 Skill

[English](./README.md)

这是一个面向真实微信使用场景的 Hermes 提醒 Skill：
支持把你在微信里发的自然语言通知（包括转发消息）自动解析为事件与 DDL，并按规则创建提醒。

## 这个技能解决什么问题

普通提醒工具要求手工结构化输入，实际使用中非常麻烦。
本技能针对“聊天式输入”优化：

- 你在微信发一段原文
- Hermes 自动提取事件与截止时间
- 自动分类、判断优先级
- 自动创建提醒任务
- 最终按固定格式提醒你

## 核心能力

- 中文时间解析（自然语言友好）
  - `2026-04-20 18:00`
  - `4月20日18:00`
  - `明天14:00`
  - `今天23:00`
- 从转发通知中提取 DDL 与事件
- 自动分类：
  - 学业
  - 工作
  - 会议/活动
  - 缴费/申请
  - 生活
  - 其他
- 自动优先级（`high` / `medium` / `normal`）
- 同事件同DDL自动去重
- 通过 Hermes cron 自动排提醒
- Apple Reminders 可用时可联动，不可用自动回退 cron
- 支持“最近DDL”按时间顺序查询

## 提醒规则（硬约束）

1. `DDL - 当前时间 > 48小时`：创建 `T-24h` + `T-6h`
2. `6小时 < DDL - 当前时间 <= 48小时`：创建 `T-6h`
3. `DDL - 当前时间 <= 6小时`：创建一次临近提醒

智能增强（不破坏硬约束）：

- 高优先级事项可加：
  - `T-72h`
  - `T-1h`
- 灵活提醒时间：
  - 若提醒点落在 `00:00-08:59`，优先前移到前一天 `22:00`
  - 且不会把提醒时间挪到过去

## 提醒格式（固定）

```text
【事件提醒】
xx事件  XX时间
具体内容
```

第三行可按情况省略。

## 目录结构

```text
hermes-wechat-ddl-reminder-skill/
├── README.md
├── README.zh-CN.md
├── LICENSE
└── skills/
    └── productivity/
        └── memo-reminder/
            ├── SKILL.md
            ├── scripts/
            │   └── create_ddl_reminders.py
            └── data/
                └── tracked_tasks.example.json
```

## 安装方式

### 1) 复制 skill 到 Hermes

```bash
mkdir -p ~/.hermes/skills/productivity/memo-reminder
cp -R skills/productivity/memo-reminder/* ~/.hermes/skills/productivity/memo-reminder/
```

### 2) 设置可执行权限

```bash
chmod +x ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py
```

### 3) （可选）接入 Apple Reminders

```bash
remindctl --version
remindctl authorize
```

如果系统权限未通过，本技能会自动走 Hermes cron，不影响使用。

## 使用方式

### A）推荐：直接喂微信原文

```bash
python3 ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py \
  --text "老师通知：4月20日18:00前提交统计学作业pdf和代码，逾期扣分" \
  --deliver origin
```

### B）结构化输入

```bash
python3 ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py \
  --title "提交统计学作业" \
  --ddl "2026-04-20 18:00" \
  --detail "pdf + 代码" \
  --deliver origin
```

### C）演练模式（不创建任务）

```bash
python3 ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py \
  --text "明天14:00技术面试，提前准备项目介绍" \
  --deliver local \
  --dry-run
```

### D）强制重复创建（覆盖去重）

```bash
python3 ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py \
  --text "4月21日20:00交数据库作业" \
  --deliver origin \
  --force
```

### E）查询最近 DDL（按时间顺序）

```bash
python3 ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py \
  --list-upcoming \
  --limit 20
```

## 运行数据

任务跟踪文件：

```text
~/.hermes/skills/productivity/memo-reminder/data/tracked_tasks.json
```

该文件已在 `.gitignore` 中忽略，不会上传你的私有任务数据。

## 微信实际工作流建议

1. 你在微信里直接发通知文本或转发消息给 Hermes
2. Hermes 提取事件与 DDL
3. Hermes 自动创建提醒任务
4. 到点按固定格式提醒你

建议在 `~/.hermes/SOUL.md` 中写死策略，让 Hermes 默认走本技能。

## 常见问题

- 提示“无法识别 DDL”
  - 在原文里补充明确时间（如 `YYYY-MM-DD HH:MM` / `4月20日18:00`）
- 为什么没重复创建
  - 默认开启去重；如需重建请加 `--force`
- Apple Reminders 无权限
  - 直接使用 cron 回退即可，或者在 macOS 隐私设置中授权

## License

MIT
