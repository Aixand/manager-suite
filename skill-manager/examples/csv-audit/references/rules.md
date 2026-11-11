# Audit rules

Preserve the original dataset. Treat quoted commas and newlines according to CSV
parsing rules. A row-width problem means its number of fields differs from the
header; include the record number and actual width. Missing values are empty or
whitespace-only cells; `0`, `false`, and `N/A` remain literal data unless the user
supplies another missing-value policy. Report duplicate headers without silently
renaming them. Do not infer validity of business facts from structural checks.
