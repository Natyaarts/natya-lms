import requests
import json

key = 'AIzaSyAn8Hh8FMsv7BPjzfVfTSrgCvEzTG1a3ug'
url = f'https://translation.googleapis.com/language/translate/v2?key={key}'

transcript = """<p>test</p>\n<p>"Hi, my name is Peter Parker."</p>"""

res = requests.post(url, json={'q': transcript, 'source': 'en', 'target': 'ml', 'format': 'text'})
with open('test_html_trans.txt', 'w', encoding='utf-8') as f:
    json.dump(res.json(), f, indent=2, ensure_ascii=False)
