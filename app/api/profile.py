import io
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, status
from PIL import Image
from typing import Optional
from app.models.user import UserInDB
from app.dependencies.auth import get_current_user
from app.services.s3_service import S3Service
from app.repositories.user_repo import UserRepository

router = APIRouter(prefix="/profile", tags=["Profile"])

def get_s3_service() -> S3Service:
    return S3Service()
    
def get_user_repo() -> UserRepository:
    return UserRepository()

def process_image(file_bytes: bytes, size: tuple = (1080, 1080)) -> bytes:
    try:
        img = Image.open(io.BytesIO(file_bytes))
        # Convert to RGB if it's RGBA or P to avoid JPEG errors
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img = img.resize(size, Image.Resampling.LANCZOS)
        out_bytes = io.BytesIO()
        img.save(out_bytes, format="JPEG", quality=85)
        return out_bytes.getvalue()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid image format: {str(e)}")

@router.get("/")
async def get_profile(current_user: UserInDB = Depends(get_current_user)):
    return {
        "full_name": current_user.full_name,
        "profile_photo": current_user.profile_photo
    }

@router.post("/")
async def create_profile_details(
    full_name: Optional[str] = Form(None),
    profile_photo: Optional[UploadFile] = File(None),
    current_user: UserInDB = Depends(get_current_user),
    s3_service: S3Service = Depends(get_s3_service),
    user_repo: UserRepository = Depends(get_user_repo)
):
    if full_name:
        current_user.full_name = full_name
        
    if profile_photo:
        content = await profile_photo.read()
        processed_image = process_image(content)
        url = await s3_service.upload_file(processed_image, profile_photo.filename)
        if url:
            if current_user.profile_photo:
                await s3_service.delete_file(current_user.profile_photo)
            current_user.profile_photo = url
            
    await user_repo.update(current_user)
    return {"message": "Profile created/updated successfully", "profile_photo": current_user.profile_photo}

@router.patch("/")
async def update_profile_details(
    full_name: Optional[str] = Form(None),
    profile_photo: Optional[UploadFile] = File(None),
    current_user: UserInDB = Depends(get_current_user),
    s3_service: S3Service = Depends(get_s3_service),
    user_repo: UserRepository = Depends(get_user_repo)
):
    return await create_profile_details(full_name, profile_photo, current_user, s3_service, user_repo)

@router.delete("/")
async def delete_profile_picture(
    current_user: UserInDB = Depends(get_current_user),
    s3_service: S3Service = Depends(get_s3_service),
    user_repo: UserRepository = Depends(get_user_repo)
):
    if current_user.profile_photo:
        await s3_service.delete_file(current_user.profile_photo)
        current_user.profile_photo = None
        await user_repo.update(current_user)
        return {"message": "Profile picture deleted"}
    return {"message": "No profile picture to delete"}
