import re

srt_text = """1
00:00:01,000 --> 00:00:02,500
Hello

2
00:00:05,000 --> 00:00:06,000
World
"""
pattern = re.compile(r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n((?:.*(?:\n|$))+?)(?=\n\d+\n|\Z)', re.MULTILINE)
for match in pattern.finditer(srt_text):
    print(match.group(2))
