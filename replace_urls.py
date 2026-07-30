import os
import re

frontend_dir = r"c:\Users\91811\OneDrive\Desktop\NEW-LMS\frontend\src"

def replace_in_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content

    # Replace already backtick'd strings
    content = re.sub(
        r"`http://(?:localhost|127\.0\.0\.1):8000",
        r"`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}",
        content
    )

    # Replace double quoted strings
    content = re.sub(
        r'"http://(?:localhost|127\.0\.0\.1):8000(.*?)"',
        r"`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}\1`",
        content
    )

    # Replace single quoted strings
    content = re.sub(
        r"'http://(?:localhost|127\.0\.0\.1):8000(.*?)'",
        r"`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}\1`",
        content
    )

    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Updated {filepath}")

for root, dirs, files in os.walk(frontend_dir):
    for file in files:
        if file.endswith('.tsx') or file.endswith('.ts'):
            replace_in_file(os.path.join(root, file))

print("Done replacing URLs.")
