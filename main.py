from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api import auth, worker, client, admin, profile
import uvicorn
import os

app = FastAPI(
    title="Cleaning One API",
    description="Production-ready FastAPI Authentication and Authorization System",
    version="1.0.0"
)

# CORS middleware
# Using allow_origins=["*"] with allow_credentials=True is not allowed by CORS standard.
# We explicitly list allowed origins here.
origins = [
    "http://localhost",
    "http://localhost:8000",
    "http://localhost:8080",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(worker.router)
app.include_router(client.router)
app.include_router(admin.router)
app.include_router(profile.router)

# Ensure uploads directory exists
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

@app.get("/")
async def root():
    return {"message": "Welcome to Cleaning One API"}

from app.core.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "main:app", 
        host=settings.APP_HOST, 
        port=settings.APP_PORT, 
        reload=True,
        access_log=True,
        log_level="info"
    )
