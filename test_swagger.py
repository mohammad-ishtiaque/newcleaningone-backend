from fastapi import FastAPI
from app.api.auth import router as auth_router
import json

app = FastAPI()
app.include_router(auth_router)

print(json.dumps(app.openapi(), indent=2))
