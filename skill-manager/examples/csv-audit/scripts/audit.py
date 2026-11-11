#!/usr/bin/env python3
"""Optional explicit CSV audit. Skill Manager never executes this script."""
import argparse
import csv
from collections import Counter
import json


def main():
    parser = argparse.ArgumentParser(description="Read a CSV and report structural issues")
    parser.add_argument("path")
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--delimiter", default=",")
    args = parser.parse_args()
    with open(args.path, newline="", encoding=args.encoding) as stream:
        rows = csv.reader(stream, delimiter=args.delimiter)
        headers = next(rows, [])
        missing = [0] * len(headers)
        width_errors = []
        count = 0
        for count, row in enumerate(rows, 1):
            if len(row) != len(headers):
                width_errors.append({"record": count + 1, "fields": len(row)})
            for column, cell in enumerate(row[:len(headers)]):
                if not cell.strip():
                    missing[column] += 1
    print(json.dumps({"headers": headers, "rows": count, "width_errors": width_errors,
                      "duplicate_headers": sorted(k for k, v in Counter(headers).items() if v > 1),
                      "missing_by_column": missing}, ensure_ascii=False))


if __name__ == "__main__":
    main()
