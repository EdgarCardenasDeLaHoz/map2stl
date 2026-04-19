#!/usr/bin/env python3
"""Check PEP 8 compliance in terrain_session.py"""

import sys

filepath = 'app/session/terrain_session.py'
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

issues = []

# Check for line length (PEP 8: max 79, practical 99)
for i, line in enumerate(lines, 1):
    line_len = len(line.rstrip('\n'))
    if line_len > 99:
        issues.append((i, 'E501', f'Line too long ({line_len} > 99)'))

# Sort by line number
issues.sort()

if issues:
    for line_no, code, desc in issues:
        print(f'{code} Line {line_no}: {desc}')
    print(f'\nTotal issues found: {len(issues)}')
    sys.exit(1)
else:
    print('✅ No PEP 8 line length issues found!')
    sys.exit(0)
