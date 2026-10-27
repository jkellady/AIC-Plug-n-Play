---
description: Create an empty month folder in the drop folder, e.g. /new-month 2026-11
argument-hint: <YYYY-MM>
---
Create the empty month folder `01_DROP_FOLDER\$ARGUMENTS`.

1. `$ARGUMENTS` must look like `2026-11`. If not, stop and say so.
2. If the folder already exists, say so and change nothing.
3. Otherwise create it with `mkdir` only. Do not touch any existing file or folder.
4. Tell the user: the folder is empty on purpose. `RUN_NOW.bat` runs every month folder, so while this one is empty it would produce a partial report and replace the one in `LATEST`. Use `RUN_MONTH.bat` and type the month instead, until files are in.
5. Remind the user which three things go in the folder: the month's clock exports (`OUTLET-All_Report_<date>.xls`), the management schedule workbook and the HR record workbook.
