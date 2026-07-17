from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import auth, worker, client, admin, profile
import uvicorn

app = FastAPI(
    title="Cleaning One API",
    description="Production-ready FastAPI Authentication and Authorization System",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(worker.router)
app.include_router(client.router)
app.include_router(admin.router)
app.include_router(profile.router)

@app.get("/")
async def root():
    return {"message": "Welcome to Cleaning One API"}

from app.core.config import settings

if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=True)
