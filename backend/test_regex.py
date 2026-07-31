import re

transcripts = [
    'test\n00:02 - "Hi, my name is Peter Parker."',
    'test\n0:02 - "Hi, my name is Peter Parker."',
    'test\n[00:02] "Hi, my name is Peter Parker."',
    'test\n00:02 "Hi, my name is Peter Parker."'
]

for t in transcripts:
    clean = re.sub(r'\[?\d{1,2}:\d{2}(:\d{2})?\]?\s*[-:]?\s*', '', t)
    print("CLEAN:", repr(clean))
