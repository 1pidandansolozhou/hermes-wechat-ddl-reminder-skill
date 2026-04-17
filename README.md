# Hermes WeChat DDL Reminder Skill

[简体中文](./README.zh-CN.md)

A production-ready Hermes skill for WeChat-based task capture, DDL extraction, intelligent categorization, and automatic reminder scheduling.

This project turns free-form notification messages (including forwarded messages) into structured reminders with deterministic timing rules and deduplication.

## Why this skill

Most reminder setups fail because users must manually structure every item.
This skill is designed for real chat behavior:

- You send a natural-language message in WeChat.
- Hermes extracts event + deadline.
- It classifies urgency and category.
- It schedules reminder jobs automatically.
- It sends reminders using a fixed, readable format.

## Core capabilities

- Natural-language time parsing (Chinese-friendly):
  - `2026-04-20 18:00`
  - `4月20日18:00`
  - `明天14:00`
  - `今天23:00`
- Event extraction from raw text / forwarded notices
- Category classification:
  - Study
  - Work
  - Meeting/Event
  - Payment/Application
  - Life
  - Other
- Priority scoring (`high` / `medium` / `normal`)
- Dedup by `(normalized_title + ddl)` fingerprint
- Reminder scheduling via Hermes cron
- Optional Apple Reminders routing when authorized
- Upcoming DDL query in chronological order

## Reminder policy

Mandatory reminder rules:

1. If `DDL - now > 48h`: create `T-24h` and `T-6h`
2. If `6h < DDL - now <= 48h`: create `T-6h`
3. If `DDL - now <= 6h`: create one immediate near-term reminder

Smart add-ons (without violating mandatory policy):

- High-priority items may add:
  - `T-72h`
  - `T-1h`
- Flexible reminder window:
  - if a reminder point falls in `00:00-08:59`, it is moved to previous day `22:00`
  - never moves reminders into past time

## Reminder format

All reminders follow:

```text
【事件提醒】
xx事件  XX时间
具体内容
```

The third line is optional when content is minimal.

## Project structure

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

## Installation

### 1) Copy skill into Hermes

```bash
mkdir -p ~/.hermes/skills/productivity/memo-reminder
cp -R skills/productivity/memo-reminder/* ~/.hermes/skills/productivity/memo-reminder/
```

### 2) Ensure script executable

```bash
chmod +x ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py
```

### 3) (Optional) Install Apple Reminders CLI

```bash
# if remindctl is available in your environment
remindctl --version
remindctl authorize
```

If authorization is not available, the skill still works using Hermes cron fallback.

## Usage

### A) Best mode: pass raw WeChat text

```bash
python3 ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py \
  --text "老师通知：4月20日18:00前提交统计学作业pdf和代码，逾期扣分" \
  --deliver origin
```

### B) Structured mode

```bash
python3 ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py \
  --title "提交统计学作业" \
  --ddl "2026-04-20 18:00" \
  --detail "pdf + code" \
  --deliver origin
```

### C) Dry run (no creation)

```bash
python3 ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py \
  --text "明天14:00技术面试，提前准备项目介绍" \
  --deliver local \
  --dry-run
```

### D) Force recreate duplicate reminders

```bash
python3 ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py \
  --text "4月21日20:00交数据库作业" \
  --deliver origin \
  --force
```

### E) List upcoming DDLs (time-ordered)

```bash
python3 ~/.hermes/skills/productivity/memo-reminder/scripts/create_ddl_reminders.py \
  --list-upcoming \
  --limit 20
```

## Runtime data

Tracked tasks are persisted to:

```text
~/.hermes/skills/productivity/memo-reminder/data/tracked_tasks.json
```

This file is intentionally ignored by git.

## WeChat workflow recommendation

1. In WeChat, send or forward a notice to Hermes.
2. Hermes extracts DDL and event intent.
3. Hermes creates reminder jobs.
4. Hermes sends reminder in fixed format.

For stable behavior, pair this with a `SOUL.md` policy that instructs Hermes to always route reminder requests through this skill.

## Troubleshooting

- `DDL parsing failed`
  - Add explicit date/time in message (`YYYY-MM-DD HH:MM` or `4月20日18:00`)
- Duplicate not created
  - This is expected (dedup). Use `--force` when needed.
- Apple Reminders denied
  - Use Hermes cron fallback, or authorize Terminal/remindctl in macOS privacy settings.

## License

MIT
