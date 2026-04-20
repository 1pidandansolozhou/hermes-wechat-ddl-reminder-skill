#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Shanghai")
SKILL_HOME = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = SKILL_HOME / "data"
DEFAULT_TASKS_FILE = DEFAULT_DATA_DIR / "tracked_tasks.json"
FALLBACK_TASKS_FILE = Path.home() / ".hermes" / "skills" / "productivity" / "memo-reminder" / "data" / "tracked_tasks.json"
CRON_JOBS_FILE = Path.home() / ".hermes" / "cron" / "jobs.json"
CHANNEL_FILE = Path.home() / ".hermes" / "channel_directory.json"

# User active hours for more human-friendly reminders.
ONLINE_START_HOUR = 9
EARLY_SHIFT_TO_PREV_DAY_HOUR = 22
CN_NUM = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
CN_TIME_TOKEN = r"(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})"


@dataclass
class ReminderPoint:
    when: datetime
    label: str


@dataclass
class ParsedTask:
    title: str
    ddl: datetime
    detail: str
    category: str
    priority: str
    source_text: str


def _now() -> datetime:
    return datetime.now(TZ)


def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _normalize_title(s: str) -> str:
    t = _normalize_text(s).lower()
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", t)
    return t


def _fingerprint(title: str, ddl: datetime) -> str:
    raw = f"{_normalize_title(title)}|{ddl.strftime('%Y-%m-%d %H:%M')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _cn_to_int(token: str) -> int | None:
    s = (token or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)

    if s == "十":
        return 10
    if "十" in s:
        parts = s.split("十")
        if len(parts) != 2:
            return None
        left, right = parts
        tens = 1 if left == "" else CN_NUM.get(left)
        if tens is None:
            return None
        if right == "":
            ones = 0
        else:
            ones = CN_NUM.get(right)
            if ones is None:
                return None
        return tens * 10 + ones

    if len(s) == 1:
        return CN_NUM.get(s)
    return None


def _parse_clock(daypart: str, hour_raw: str, minute_raw: str, half_raw: str) -> tuple[int, int] | None:
    hour = _cn_to_int(hour_raw)
    if hour is None:
        return None

    minute: int
    if half_raw:
        minute = 30
    elif minute_raw:
        m = _cn_to_int(minute_raw)
        if m is None:
            return None
        minute = m
    else:
        minute = 0

    if not (0 <= minute <= 59):
        return None
    hour = _apply_daypart(daypart or "", hour)
    if not (0 <= hour <= 23):
        return None
    return hour, minute


def _apply_daypart(daypart: str, hour: int) -> int:
    if not daypart:
        return hour
    if daypart in {"下午", "晚上", "傍晚"} and hour < 12:
        return hour + 12
    if daypart == "中午":
        if hour == 0:
            return 12
        if hour < 11:
            return hour + 12
    if daypart in {"凌晨"} and hour == 12:
        return 0
    return hour


def _extract_ddl_from_text(text: str, now: datetime) -> tuple[datetime | None, tuple[int, int] | None]:
    s = text

    # 1) YYYY-MM-DD [time]
    pat_full = re.compile(
        r"(20\d{2})[年/\-](\d{1,2})[月/\-](\d{1,2})[日号]?"
        rf"(?:\s*([上下午晚上傍晚中午凌晨早上]*)\s*({CN_TIME_TOKEN})(?:[:：点时]({CN_TIME_TOKEN})?)?(半)?)?"
    )
    m = pat_full.search(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        parsed = _parse_clock(m.group(4) or "", m.group(5) or "", m.group(6) or "", m.group(7) or "")
        if parsed:
            hh, mm = parsed
        else:
            hh, mm = 20, 0
        return datetime(y, mo, d, hh, mm, tzinfo=TZ), m.span()

    # 2) MM-DD [time]
    pat_md = re.compile(
        r"(?<!\d)(\d{1,2})[月/\-](\d{1,2})[日号]?"
        rf"(?:\s*([上下午晚上傍晚中午凌晨早上]*)\s*({CN_TIME_TOKEN})(?:[:：点时]({CN_TIME_TOKEN})?)?(半)?)?"
    )
    m = pat_md.search(s)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        parsed = _parse_clock(m.group(3) or "", m.group(4) or "", m.group(5) or "", m.group(6) or "")
        if parsed:
            hh, mm = parsed
        else:
            hh, mm = 20, 0
        year = now.year
        dt = datetime(year, mo, d, hh, mm, tzinfo=TZ)
        if dt < now - timedelta(days=1):
            dt = dt.replace(year=year + 1)
        return dt, m.span()

    # 3) 今天/明天/后天 [time]
    pat_rel = re.compile(
        r"(今天|明天|后天)"
        rf"(?:\s*([上下午晚上傍晚中午凌晨早上]*)\s*({CN_TIME_TOKEN})(?:[:：点时]({CN_TIME_TOKEN})?)?(半)?)?"
    )
    m = pat_rel.search(s)
    if m:
        offset = {"今天": 0, "明天": 1, "后天": 2}[m.group(1)]
        day = now.date() + timedelta(days=offset)
        parsed = _parse_clock(m.group(2) or "", m.group(3) or "", m.group(4) or "", m.group(5) or "")
        if parsed:
            hh, mm = parsed
        else:
            hh, mm = 20, 0
        return datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ), m.span()

    # 4) 单独时间（如：下午三点半 / 3点半）默认指向最近未来时间
    pat_time_only = re.compile(
        rf"([上下午晚上傍晚中午凌晨早上]*)\s*({CN_TIME_TOKEN})点(?:({CN_TIME_TOKEN})?分?)?(半)?"
    )
    m = pat_time_only.search(s)
    if m:
        parsed = _parse_clock(m.group(1) or "", m.group(2) or "", m.group(3) or "", m.group(4) or "")
        if parsed:
            hh, mm = parsed
            dt = datetime(now.year, now.month, now.day, hh, mm, tzinfo=TZ)
            if dt <= now:
                dt += timedelta(days=1)
            return dt, m.span()

    # 5) fallback explicit formats
    raw = _normalize_text(s).replace("年", "-").replace("月", "-").replace("日", "")
    raw = raw.replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%H:%M"):
        try:
            p = datetime.strptime(raw, fmt)
            if fmt == "%Y-%m-%d":
                return datetime(p.year, p.month, p.day, 20, 0, tzinfo=TZ), None
            if fmt == "%H:%M":
                dt = datetime(now.year, now.month, now.day, p.hour, p.minute, tzinfo=TZ)
                if dt <= now:
                    dt += timedelta(days=1)
                return dt, None
            return p.replace(tzinfo=TZ), None
        except ValueError:
            continue

    return None, None


def _extract_title(text: str, time_span: tuple[int, int] | None) -> str:
    s = text
    if time_span:
        s = (s[: time_span[0]] + " " + s[time_span[1] :]).strip()
    s = _normalize_text(s)
    s = re.sub(r"^(老师转发|转发|通知|通知如下|面试通知|公告|帮我|请你|麻烦你|提醒我|请提醒我|记一下|记得)[:：,\s]*", "", s)
    s = re.sub(r"(截止|ddl|due|到期|之前).*$", "", s, flags=re.IGNORECASE)
    s = s.strip("，。；;:： ")
    if not s:
        return "未命名事件"

    # 常见学业/会议片段优先
    m = re.search(r"([\u4e00-\u9fffA-Za-z0-9]{0,12}(作业|面试|会议|汇报|考试|申请|缴费))", s)
    if m:
        title = m.group(1).strip()
        title = re.sub(r"^(前|先|再)", "", title)
        return title[:30]

    # 再抽动作+宾语，例如：提交统计学作业、参加技术面试
    m = re.search(r"(提交|完成|缴费|报名|参加|准备|面试|开会|汇报|考试|答辩|申请)([^，。；;]{2,24})", s)
    if m:
        title = (m.group(1) + m.group(2)).strip("，。；;:： ")
        title = re.sub(r"^(前|先|再)", "", title)
        return title[:30]

    # 截取第一句
    m = re.split(r"[，。；;]", s, maxsplit=1)
    s = m[0].strip() if m else s
    if not s:
        return "未命名事件"
    s = re.sub(r"^(前|先|再)", "", s)
    return s[:30]


def _classify(text: str) -> str:
    t = text.lower()
    mapping = [
        ("学业", ["作业", "课程", "考试", "论文", "答辩", "提交", "老师", "课堂"]),
        ("工作", ["实习", "项目", "汇报", "客户", "周报", "对接", "排期", "面试"]),
        ("会议/活动", ["会议", "活动", "讲座", "分享", "路演", "报名", "签到"]),
        ("缴费/申请", ["缴费", "付款", "申请", "材料", "证件", "签证", "报销"]),
        ("生活", ["看病", "体检", "搬家", "快递", "取件", "缴水电", "家务"]),
    ]
    for label, kws in mapping:
        if any(k in t for k in kws):
            return label
    return "其他"


def _priority(text: str, ddl: datetime, now: datetime) -> str:
    t = text.lower()
    if any(k in t for k in ["考试", "面试", "截止", "ddl", "到期", "申请", "缴费", "答辩"]):
        return "high"
    if ddl - now <= timedelta(hours=24):
        return "high"
    if any(k in t for k in ["会议", "汇报", "提交", "报名"]):
        return "medium"
    return "normal"


def _optimize_point_time(when: datetime, now: datetime) -> datetime:
    """Make reminders align with user's active window (09:00-24:00)."""
    if 0 <= when.hour < ONLINE_START_HOUR:
        shifted = (when - timedelta(days=1)).replace(
            hour=EARLY_SHIFT_TO_PREV_DAY_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
        if shifted > now:
            return shifted
    return when


def _build_points(ddl: datetime, now: datetime, priority: str) -> list[ReminderPoint]:
    delta = ddl - now
    points: list[ReminderPoint] = []
    if delta <= timedelta(0):
        return points

    # Mandatory rule from user
    if delta > timedelta(hours=48):
        points.extend(
            [
                ReminderPoint(when=ddl - timedelta(hours=24), label="T-24h"),
                ReminderPoint(when=ddl - timedelta(hours=6), label="T-6h"),
            ]
        )
    elif delta > timedelta(hours=6):
        points.append(ReminderPoint(when=ddl - timedelta(hours=6), label="T-6h"))
    else:
        points.append(ReminderPoint(when=now + timedelta(minutes=1), label="临近提醒"))

    # Smart add-ons
    if priority == "high" and delta > timedelta(hours=72):
        points.append(ReminderPoint(when=ddl - timedelta(hours=72), label="T-72h"))
    if priority == "high" and delta > timedelta(hours=1):
        points.append(ReminderPoint(when=ddl - timedelta(hours=1), label="T-1h"))

    dedup: dict[tuple[int, int, int, int, int], ReminderPoint] = {}
    for p in points:
        adjusted = ReminderPoint(when=_optimize_point_time(p.when, now), label=p.label)
        if adjusted.when <= now:
            continue
        key = (adjusted.when.year, adjusted.when.month, adjusted.when.day, adjusted.when.hour, adjusted.when.minute)
        dedup[key] = adjusted
    return sorted(dedup.values(), key=lambda x: x.when)


def _cron_expr(dt: datetime) -> str:
    return f"{dt.minute} {dt.hour} {dt.day} {dt.month} *"


def _build_message(title: str, ddl: datetime, detail: str, category: str, priority: str) -> str:
    lines = ["【事件提醒】", f"{title}  {ddl.strftime('%Y-%m-%d %H:%M')}"]
    if detail.strip():
        lines.append(detail.strip())
    elif category != "其他" or priority == "high":
        lines.append(f"分类：{category}；优先级：{priority}")
    return "\n".join(lines)


def _find_hermes() -> str:
    cmd = shutil.which("hermes")
    if cmd:
        return cmd
    candidate = str(Path.home() / ".local" / "bin" / "hermes")
    if Path(candidate).exists():
        return candidate
    raise FileNotFoundError("找不到 hermes 命令")


def _run(cmd: Iterable[str]) -> str:
    res = subprocess.run(list(cmd), capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout).strip() or "unknown error")
    return res.stdout.strip()


def _tasks_file() -> Path:
    if DEFAULT_TASKS_FILE.exists():
        return DEFAULT_TASKS_FILE
    # Running from a dev repo without runtime data: transparently inspect live Hermes data.
    if FALLBACK_TASKS_FILE.exists():
        return FALLBACK_TASKS_FILE
    return DEFAULT_TASKS_FILE


def _load_tasks() -> dict:
    path = _tasks_file()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"tasks": []}


def _save_tasks(data: dict) -> None:
    path = _tasks_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_jobs() -> list[dict]:
    if not CRON_JOBS_FILE.exists():
        return []
    try:
        obj = json.loads(CRON_JOBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    jobs = obj.get("jobs", [])
    if not isinstance(jobs, list):
        return []
    return [j for j in jobs if isinstance(j, dict)]


def _existing_cron_ids(prefix: str) -> list[str]:
    ids: list[str] = []
    for j in _load_jobs():
        name = j.get("name", "")
        if isinstance(name, str) and name.startswith(prefix):
            jid = j.get("id")
            if isinstance(jid, str):
                ids.append(jid)
    return ids


def _resolve_deliver(created_job_ids: list[str], fallback: str) -> str:
    """Resolve concrete deliver target from jobs.json when possible."""
    by_id = {str(j.get("id", "")): str(j.get("deliver", "")) for j in _load_jobs()}
    for jid in created_job_ids:
        deliver = by_id.get(jid, "")
        if deliver:
            return deliver
    return fallback


def _default_deliver() -> str:
    """Resolve the primary WeChat target for DDL reminders."""
    try:
        data = json.loads(CHANNEL_FILE.read_text(encoding="utf-8"))
    except Exception:
        raise RuntimeError("未找到可用的微信通道，请先完成 Hermes 微信连接。")
    platforms = data.get("platforms") if isinstance(data, dict) else None
    if isinstance(platforms, dict):
        weixin = platforms.get("weixin")
        if isinstance(weixin, list):
            for item in weixin:
                if isinstance(item, dict) and item.get("id"):
                    return f"weixin:{item['id']}"
    raise RuntimeError("未找到可用的微信通道，请先完成 Hermes 微信连接。")


def _normalize_deliver_target(requested: str) -> str:
    """Enforce WeChat-only delivery for DDL reminders."""
    raw = (requested or "").strip() or "weixin"
    lowered = raw.lower()
    session_platform = (os.getenv("HERMES_SESSION_PLATFORM") or "").strip().lower()

    if lowered.startswith("weixin:"):
        return raw
    if lowered == "weixin":
        return _default_deliver()
    if lowered == "origin" and session_platform == "weixin":
        return "origin"

    fallback = _default_deliver()
    if lowered == "origin":
        print(f"[memo-reminder] 当前不在微信会话，已将 deliver=origin 改为 {fallback}", file=sys.stderr)
    else:
        print(f"[memo-reminder] DDL 提醒仅支持微信，已将 deliver={raw} 改为 {fallback}", file=sys.stderr)
    return fallback


def _to_dt(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def _format_remaining(delta: timedelta) -> str:
    if delta <= timedelta(0):
        return "已过期"
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    if days > 0:
        return f"剩余{days}天{hours}小时"
    if hours > 0:
        return f"剩余{hours}小时{minutes}分钟"
    return f"剩余{minutes}分钟"


def _list_upcoming(tasks: dict, now: datetime, limit: int, include_expired: bool, as_json: bool) -> int:
    rows: list[dict] = []
    for t in tasks.get("tasks", []):
        if not isinstance(t, dict):
            continue
        ddl = _to_dt(t.get("ddl"))
        if ddl is None:
            continue
        if not include_expired and ddl <= now:
            continue
        rows.append(
            {
                "title": str(t.get("title") or "未命名事件"),
                "ddl": ddl,
                "category": str(t.get("category") or "其他"),
                "priority": str(t.get("priority") or "normal"),
                "detail": str(t.get("detail") or ""),
                "fingerprint": str(t.get("fingerprint") or ""),
            }
        )

    rows.sort(key=lambda x: x["ddl"])
    rows = rows[: max(1, limit)]

    if as_json:
        payload = {
            "now": now.isoformat(),
            "count": len(rows),
            "items": [
                {
                    "title": r["title"],
                    "ddl": r["ddl"].isoformat(),
                    "category": r["category"],
                    "priority": r["priority"],
                    "detail": r["detail"],
                    "fingerprint": r["fingerprint"],
                    "remaining": _format_remaining(r["ddl"] - now),
                }
                for r in rows
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not rows:
        print("暂无未过期DDL事项。")
        return 0

    print("最近DDL（按时间顺序）:")
    for idx, r in enumerate(rows, start=1):
        ddl_text = r["ddl"].strftime("%Y-%m-%d %H:%M")
        remain = _format_remaining(r["ddl"] - now)
        print(f"{idx}. {ddl_text} | {r['title']} | {r['category']}/{r['priority']} | {remain}")
        if r["detail"]:
            print(f"   说明: {r['detail'][:100]}")
    return 0


def _parse_fixed_schedule(expr: str, now: datetime) -> datetime | None:
    parts = (expr or "").strip().split()
    if len(parts) != 5:
        return None
    minute, hour, day, month, _weekday = parts
    for p in (minute, hour, day, month):
        if not p.isdigit():
            return None
    try:
        return datetime(now.year, int(month), int(day), int(hour), int(minute), tzinfo=TZ)
    except ValueError:
        return None


def _health_check(tasks: dict, jobs: list[dict], now: datetime) -> dict:
    rows = [t for t in tasks.get("tasks", []) if isinstance(t, dict)]
    job_by_id = {str(j.get("id") or ""): j for j in jobs if isinstance(j, dict)}
    tracked_fp = {str(t.get("fingerprint") or "") for t in rows}

    expired_tasks: list[dict] = []
    orphan_task_job_refs: list[dict] = []
    for t in rows:
        ddl = _to_dt(t.get("ddl"))
        if ddl and ddl <= now:
            expired_tasks.append(
                {
                    "fingerprint": str(t.get("fingerprint") or ""),
                    "title": str(t.get("title") or "未命名事件"),
                    "ddl": ddl.isoformat(),
                }
            )

        for jid in t.get("job_ids", []) or []:
            jid_s = str(jid)
            if jid_s not in job_by_id:
                orphan_task_job_refs.append(
                    {
                        "fingerprint": str(t.get("fingerprint") or ""),
                        "title": str(t.get("title") or "未命名事件"),
                        "missing_job_id": jid_s,
                    }
                )

    orphan_ddl_jobs: list[dict] = []
    stale_one_shot_jobs: list[dict] = []
    ddl_non_weixin_jobs: list[dict] = []
    for j in jobs:
        name = str(j.get("name") or "")
        jid = str(j.get("id") or "")
        deliver = str(j.get("deliver") or "")
        repeat = j.get("repeat") if isinstance(j.get("repeat"), dict) else {}
        repeat_times = repeat.get("times")
        repeat_completed = int(repeat.get("completed") or 0)
        expr = str((j.get("schedule") or {}).get("expr") or "")

        if name.startswith("ddl-"):
            parts = name.split("-")
            fp = parts[1] if len(parts) > 1 else ""
            if fp not in tracked_fp:
                orphan_ddl_jobs.append({"job_id": jid, "name": name, "deliver": deliver})
            if not deliver.startswith("weixin:"):
                ddl_non_weixin_jobs.append({"job_id": jid, "name": name, "deliver": deliver})

        run_at = _parse_fixed_schedule(expr, now)
        if repeat_times == 1 and repeat_completed == 0 and run_at and run_at < now:
            stale_one_shot_jobs.append(
                {
                    "job_id": jid,
                    "name": name,
                    "schedule": expr,
                    "run_at": run_at.isoformat(),
                    "deliver": deliver,
                }
            )

    return {
        "now": now.isoformat(),
        "tracked_task_count": len(rows),
        "cron_job_count": len(jobs),
        "expired_tasks": expired_tasks,
        "orphan_task_job_refs": orphan_task_job_refs,
        "orphan_ddl_jobs": orphan_ddl_jobs,
        "stale_one_shot_jobs": stale_one_shot_jobs,
        "ddl_non_weixin_jobs": ddl_non_weixin_jobs,
    }


def _print_health_report(report: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("任务体检结果：")
    print(f"- 当前时间: {report.get('now')}")
    print(f"- tracked tasks: {report.get('tracked_task_count')}")
    print(f"- cron jobs: {report.get('cron_job_count')}")

    checks = [
        ("过期 tracked tasks", "expired_tasks"),
        ("task->job 孤儿引用", "orphan_task_job_refs"),
        ("无归属 DDL cron 任务", "orphan_ddl_jobs"),
        ("疑似未执行的一次性过期任务", "stale_one_shot_jobs"),
        ("DDL 非微信投递任务", "ddl_non_weixin_jobs"),
    ]
    for label, key in checks:
        rows = report.get(key, []) or []
        print(f"- {label}: {len(rows)}")
        for row in rows[:5]:
            print(f"  - {json.dumps(row, ensure_ascii=False)}")
        if len(rows) > 5:
            print(f"  - ... 共 {len(rows)} 项")
    return 0


def _parse_task(args: argparse.Namespace, now: datetime) -> ParsedTask:
    source_text = _normalize_text(args.text or "")

    if args.ddl:
        ddl, span = _extract_ddl_from_text(args.ddl, now)
        if ddl is None:
            raise ValueError(f"无法解析DDL时间: {args.ddl}")
    elif source_text:
        ddl, span = _extract_ddl_from_text(source_text, now)
        if ddl is None:
            raise ValueError("未能从文本中识别到DDL，请补充日期时间。")
    else:
        raise ValueError("必须提供 --ddl 或 --text。")

    title = _normalize_text(args.title or "")
    if not title and source_text:
        title = _extract_title(source_text, span)
    if not title:
        title = "未命名事件"

    detail = _normalize_text(args.detail or "")
    if not detail and source_text:
        detail = source_text[:120]

    category = args.category or _classify(" ".join([title, detail, source_text]))
    priority = args.priority or _priority(" ".join([title, detail, source_text]), ddl, now)
    return ParsedTask(
        title=title,
        ddl=ddl,
        detail=detail,
        category=category,
        priority=priority,
        source_text=source_text,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Smart DDL reminder builder for Hermes/WeChat")
    ap.add_argument("--title", default="", help="事件名（可选）")
    ap.add_argument("--ddl", default="", help="DDL时间（可选，支持中文时间）")
    ap.add_argument("--detail", default="", help="具体内容（可选）")
    ap.add_argument("--text", default="", help="微信原始通知文本（推荐）")
    ap.add_argument("--category", default="", help="强制分类")
    ap.add_argument("--priority", default="", choices=["high", "medium", "normal"], help="强制优先级")
    ap.add_argument("--deliver", default="weixin", help="投递目标，默认 weixin（DDL提醒仅走微信）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不真正创建")
    ap.add_argument("--force", action="store_true", help="忽略重复检测，强制创建")
    ap.add_argument("--list-upcoming", action="store_true", help="列出最近DDL（按时间顺序）")
    ap.add_argument("--limit", type=int, default=20, help="--list-upcoming 时最多返回条数")
    ap.add_argument("--include-expired", action="store_true", help="--list-upcoming 时包含已过期事项")
    ap.add_argument("--health-check", action="store_true", help="输出 tracked tasks 与 cron jobs 的一致性体检结果")
    ap.add_argument("--json", action="store_true", help="与 --list-upcoming / --health-check 搭配时以 JSON 输出")
    args = ap.parse_args()

    now = _now()

    if args.list_upcoming:
        tasks = _load_tasks()
        return _list_upcoming(tasks, now, limit=args.limit, include_expired=args.include_expired, as_json=args.json)
    if args.health_check:
        tasks = _load_tasks()
        jobs = _load_jobs()
        report = _health_check(tasks, jobs, now)
        return _print_health_report(report, as_json=args.json)

    try:
        task = _parse_task(args, now)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if task.ddl <= now:
        print("DDL 已过期，未创建提醒。", file=sys.stderr)
        return 3

    points = _build_points(task.ddl, now, task.priority)
    if not points:
        print("无可创建提醒节点。", file=sys.stderr)
        return 4

    fp = _fingerprint(task.title, task.ddl)
    name_prefix = f"ddl-{fp}-"
    tasks = _load_tasks()
    existing_task = next((t for t in tasks.get("tasks", []) if t.get("fingerprint") == fp), None)
    existing_jobs = _existing_cron_ids(name_prefix)

    if (existing_task or existing_jobs) and not args.force:
        print("检测到重复任务，已跳过创建。")
        print(f"fingerprint={fp}")
        if existing_jobs:
            print("existing_job_ids=" + ",".join(existing_jobs))
        return 0

    message = _build_message(task.title, task.ddl, task.detail, task.category, task.priority)
    hermes = _find_hermes()
    created: list[tuple[str, datetime, str]] = []

    try:
        deliver_target = _normalize_deliver_target(args.deliver)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 5

    for p in points:
        expr = _cron_expr(p.when)
        name = f"{name_prefix}{p.label}"
        cmd = [
            hermes,
            "cron",
            "create",
            expr,
            message,
            "--name",
            name,
            "--deliver",
            deliver_target,
            "--repeat",
            "1",
        ]
        if args.dry_run:
            print(
                f"[DRY-RUN] {p.label} @ {p.when.strftime('%Y-%m-%d %H:%M')} ({task.category}/{task.priority}) -> "
                + " ".join(repr(x) for x in cmd)
            )
            continue
        out = _run(cmd)
        m = re.search(r"([a-f0-9]{12})", out)
        job_id = m.group(1) if m else "unknown"
        created.append((p.label, p.when, job_id))

    if args.dry_run:
        return 0

    row = {
        "fingerprint": fp,
        "title": task.title,
        "ddl": task.ddl.isoformat(),
        "category": task.category,
        "priority": task.priority,
        "detail": task.detail,
        "created_at": now.isoformat(),
        "job_ids": [x[2] for x in created],
        "deliver": _resolve_deliver([x[2] for x in created], deliver_target),
        "planned_labels": [x[0] for x in created],
        "source_text": task.source_text,
    }
    tasks.setdefault("tasks", [])
    tasks["tasks"] = [t for t in tasks["tasks"] if t.get("fingerprint") != fp] + [row]
    _save_tasks(tasks)

    print("创建完成：")
    print(f"- 事件: {task.title}")
    print(f"- DDL: {task.ddl.strftime('%Y-%m-%d %H:%M')}")
    print(f"- 分类/优先级: {task.category}/{task.priority}")
    print(f"- fingerprint: {fp}")
    print(f"- 投递: {_resolve_deliver([x[2] for x in created], deliver_target)}")
    for label, when, job_id in created:
        print(f"- {label} {when.strftime('%Y-%m-%d %H:%M')} id={job_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
