#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
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
DATA_DIR = SKILL_HOME / "data"
TASKS_FILE = DATA_DIR / "tracked_tasks.json"
CRON_JOBS_FILE = Path.home() / ".hermes" / "cron" / "jobs.json"


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
        r"(?:\s*([上下午晚上傍晚中午凌晨早上]*)\s*(\d{1,2})(?:[:：点时](\d{1,2}))?)?"
    )
    m = pat_full.search(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if m.group(5):
            hh = _apply_daypart(m.group(4) or "", int(m.group(5)))
            mm = int(m.group(6) or 0)
        else:
            hh, mm = 20, 0
        return datetime(y, mo, d, hh, mm, tzinfo=TZ), m.span()

    # 2) MM-DD [time]
    pat_md = re.compile(
        r"(?<!\d)(\d{1,2})[月/\-](\d{1,2})[日号]?"
        r"(?:\s*([上下午晚上傍晚中午凌晨早上]*)\s*(\d{1,2})(?:[:：点时](\d{1,2}))?)?"
    )
    m = pat_md.search(s)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        if m.group(4):
            hh = _apply_daypart(m.group(3) or "", int(m.group(4)))
            mm = int(m.group(5) or 0)
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
        r"(?:\s*([上下午晚上傍晚中午凌晨早上]*)\s*(\d{1,2})(?:[:：点时](\d{1,2}))?)?"
    )
    m = pat_rel.search(s)
    if m:
        offset = {"今天": 0, "明天": 1, "后天": 2}[m.group(1)]
        day = now.date() + timedelta(days=offset)
        if m.group(3):
            hh = _apply_daypart(m.group(2) or "", int(m.group(3)))
            mm = int(m.group(4) or 0)
        else:
            hh, mm = 20, 0
        return datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ), m.span()

    # 4) fallback explicit formats
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
        if p.when <= now:
            continue
        key = (p.when.year, p.when.month, p.when.day, p.when.hour, p.when.minute)
        dedup[key] = p
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


def _load_tasks() -> dict:
    if TASKS_FILE.exists():
        try:
            return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"tasks": []}


def _save_tasks(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _existing_cron_ids(prefix: str) -> list[str]:
    if not CRON_JOBS_FILE.exists():
        return []
    try:
        obj = json.loads(CRON_JOBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    ids: list[str] = []
    for j in obj.get("jobs", []):
        name = j.get("name", "")
        if isinstance(name, str) and name.startswith(prefix):
            jid = j.get("id")
            if isinstance(jid, str):
                ids.append(jid)
    return ids


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
    ap.add_argument("--deliver", default="origin", help="投递目标，默认 origin")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不真正创建")
    ap.add_argument("--force", action="store_true", help="忽略重复检测，强制创建")
    args = ap.parse_args()

    now = _now()
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
            args.deliver,
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
    for label, when, job_id in created:
        print(f"- {label} {when.strftime('%Y-%m-%d %H:%M')} id={job_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
