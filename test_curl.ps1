$ErrorActionPreference = 'Stop'
try {
    curl -X 'POST' \
      'http://127.0.0.1:8080/worker/signup' \
      -H 'accept: application/json' \
      -H 'Content-Type: application/json' \
      -d '{
      "full_name": "Sadim Hasan Sourav",
      "email": "w1@yopmail.com",
      "password": "Secure123",
      "phone": "+8812345678998"
    }'
} catch {
    Write-Output "Error: $_"
}
