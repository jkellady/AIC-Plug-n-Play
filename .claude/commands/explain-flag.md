---
description: Explain a flag in plain English, e.g. /explain-flag Late in (over 15 min)
argument-hint: <flag name, or staff ID and date>
---
Explain `$ARGUMENTS` to a non-technical reader from HR or Admin. Do not decide anything.

1. Find the flag in `03_MASTER_DATA\flag_catalogue.csv`. If given a staff ID and date, find the matching row in the Review page or the latest workbook in `06_REPORTS\LATEST`, using the staff ID only.
2. Find the rule behind it in `03_MASTER_DATA\rules.toml` and say whether it is CONFIRMED, ASSUMED or OPEN.
3. Answer in this shape, in plain English, at most 8 lines:
   - What the flag means
   - Why it was raised (the rule, in one sentence)
   - Colour: red = possible data error, amber = depends on a rule not yet confirmed, green = a confirmed rule was applied
   - Who decides: HR
   - What a person could check next
4. Never calculate, estimate or re-derive a number. Quote counts from the report and name the file.
5. If the flag or rule cannot be found, say so and offer to log it with `/log-feedback`.
