from fastapi.testclient import TestClient
from main import app
import traceback

client = TestClient(app)

payload = {
  "full_name": "Sadim Hasan Sourav",
  "email": "w1@yopmail.com",
  "password": "Secure123",
  "phone": "+8812345678998"
}

try:
    response = client.post("/worker/signup", json=payload)
    print("Status Code:", response.status_code)
    print("Response JSON:", response.json())
except Exception as e:
    traceback.print_exc()
