import urllib.request
import json

url = "http://127.0.0.1:8081/worker/signup"
payload = {
  "full_name": "Sadim Hasan Sourav",
  "email": "w6@yopmail.com",
  "password": "Secure123",
  "phone": "+8812345678998"
}
data = json.dumps(payload).encode('utf-8')
req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', 'Accept': 'application/json'})

try:
    with urllib.request.urlopen(req) as response:
        print("Status:", response.status)
        print("Body:", response.read().decode())
except Exception as e:
    print("Error:", e)
