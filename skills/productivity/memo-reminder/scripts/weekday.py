#!/usr/bin/env python3
from datetime import datetime
import sys

WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]

def weekday_cn(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return WEEKDAYS[dt.weekday()]

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python weekday.py YYYY-MM-DD")
        sys.exit(1)
    print(weekday_cn(sys.argv[1]))
