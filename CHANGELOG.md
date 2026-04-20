# Changelog

## v1.4.0

- Improved Chinese time parsing for colloquial formats like `明天下午三点半` / `下午3点半`
- Added `--health-check` mode to inspect tracked-task and cron-job consistency
- Updated bilingual docs and skill instructions for health-check workflow

## v1.3.0

- Added `--list-upcoming` mode to query upcoming DDL items in chronological order
- Added flexible reminder-time optimization for low-activity hours (00:00-08:59 → previous day 22:00 when feasible)
- Added deliver-target hardening for `origin` outside gateway sessions (fallback to primary WeChat channel)
- Expanded bilingual docs for upcoming-DDL query and flexible scheduling behavior

## v1.2.0

- Added smart natural-language parsing for WeChat forwarded notifications
- Added category + priority inference
- Added dedup via task fingerprint
- Added optional high-priority extra reminders (`T-72h`, `T-1h`)
- Added bilingual project documentation

## v1.1.0

- Added fixed reminder policy (`T-24h`, `T-6h`, near-term)
- Added required message format for reminders

## v1.0.0

- Initial reminder skill release
