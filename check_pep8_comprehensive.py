#!/usr/bin/env python3
"""Comprehensive PEP 8 checker for terrain_session.py"""

import sys
import re

filepath = 'app/session/terrain_session.py'
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

issues = []

# Check for line length
for i, line in enumerate(lines, 1):
    line_len = len(line.rstrip('\n'))
    if line_len > 99:
        issues.append((i, 'E501', f'Line too long ({line_len} > 99)'))

# Check for trailing whitespace
for i, line in enumerate(lines, 1):
    if line.rstrip('\n') != line.rstrip():
        issues.append((i, 'W291', 'Trailing whitespace'))

# Check for tabs
for i, line in enumerate(lines, 1):
    if '\t' in line:
        issues.append((i, 'W191', 'Indentation contains tabs'))

# Check for multiple blank lines
blank_count = 0
for i, line in enumerate(lines, 1):
    if line.strip() == '':
        blank_count += 1
        if blank_count > 2:
            issues.append((i, 'E303', 'Too many blank lines'))
    else:
        blank_count = 0

# Check for missing whitespace around operators (simplified)
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    # Skip comments and strings
    if stripped.startswith('#'):
        continue
    # Look for common patterns
    if re.search(r'\w\+\w|\w\-\w|\w\*\w', line):
        issues.append((i, 'E225', 'Missing whitespace around operator'))

# Sort and deduplicate
issues = list(set(issues))
issues.sort()

if issues:
    for line_no, code, desc in issues[:50]:
        print(f'{code} Line {line_no}: {desc}')
    if len(issues) > 50:
        print(f'\n... and {len(issues) - 50} more issues')
    print(f'\nTotal issues found: {len(issues)}')
    sys.exit(1)
else:
    print('✅ No PEP 8 style issues found!')
    sys.exit(0)
