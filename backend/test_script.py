import requests
import json

key = 'AIzaSyAn8Hh8FMsv7BPjzfVfTSrgCvEzTG1a3ug'
url = f'https://translation.googleapis.com/language/translate/v2?key={key}'

transcript = """test
"Hi, my name is Peter Parker."
"I have something to tell you that's going to sound crazy."
"But it's the truth."
"You're Spider-Man."
"Can't tell anybody about this. Gotta keep it a secret."
"""

res = requests.post(url, json={'q': transcript, 'source': 'en', 'target': 'ml', 'format': 'text'})
with open('test_trans2.txt', 'w', encoding='utf-8') as f:
    json.dump(res.json(), f, indent=2, ensure_ascii=False)
