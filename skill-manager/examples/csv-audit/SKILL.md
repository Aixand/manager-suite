---
name: csv-audit
description: Audit a CSV dataset for inconsistent row widths, duplicate headers, and missing values. Use when reviewing a dataset before import or analysis.
license: MIT
compatibility: Requires Python 3.11 or newer to run the optional audit script.
metadata:
  author: aixand
  version: "1.0"
allowed-tools: Read
---

# CSV audit

Read [the audit rules](references/rules.md), then inspect the supplied CSV without
changing it. Report header names, total rows, row-width problems, and missing-value
counts. Distinguish an empty cell from the literal value `0`.

If the user explicitly requests a script-based check, run
`python scripts/audit.py PATH_TO_CSV` and explain its JSON output. The script uses
only Python's standard library and does not write the dataset. Confirm the CSV
encoding and delimiter if the default UTF-8 and comma do not fit the input.

The sample CSV is [assets/sample.csv](assets/sample.csv). The PNG badge is an
optional binary asset, preserved by package releases.
