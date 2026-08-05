import re

srt_text = "1\r\n00:00:01,000 --> 00:00:02,500\r\nHello\r\n\r\n2\r\n00:00:05,000 --> 00:00:06,000\r\nWorld\r\n"
pattern = re.compile(r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n((?:.*(?:\n|$))+?)(?=\n\d+\n|\Z)', re.MULTILINE)

matches = list(pattern.finditer(srt_text))
print("Original Regex matches:", len(matches))

srt_text_norm = srt_text.replace('\r\n', '\n')
matches_norm = list(pattern.finditer(srt_text_norm))
print("Normalized Regex matches:", len(matches_norm))
