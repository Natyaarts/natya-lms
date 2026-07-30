import urllib.request
import urllib.error
import json

req = urllib.request.Request(
    'https://academy-api.natyaarts.com/api/auth/login/',
    method='POST',
    headers={
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    },
    data=json.dumps({'username': 'natya', 'password': 'Admin@123!'}).encode('utf-8')
)

try:
    response = urllib.request.urlopen(req)
    print(response.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode('utf-8'))
