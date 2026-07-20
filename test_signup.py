import urllib.request
import json
import urllib.error

url = "http://127.0.0.1:8080/worker/signup"
payload = {
  "full_name": "Sadim Hasan Sourav",
  "email": "w5@yopmail.com",
  "password": "Secure123",
  "phone": "+8812345678998"
}
data = json.dumps(payload).encode('utf-8')

req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
try:
    with urllib.request.urlopen(req) as response:
        print("Status:", response.status)
        print("Body:", response.read().decode())
except urllib.error.HTTPError as e:
    print("Status:", e.code)
    print("Body:", e.read().decode())
except Exception as e:
    print("Error:", e)
