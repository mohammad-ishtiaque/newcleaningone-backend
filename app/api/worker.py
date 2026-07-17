from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File, Form
from typing import List, Optional
from app.schemas.user import WorkerSignup, UserResponse, WorkerOnboardingStep1, WorkerProfileResponse
from app.services.user_service import UserService
from app.repositories.user_repo import UserRepository
from app.dependencies.auth import get_current_user
from app.models.user import UserInDB, RoleEnum
from app.services.s3_service import S3Service
from app.api.profile import process_image

router = APIRouter(prefix="/worker", tags=["Worker"])

def get_user_service(user_repo: UserRepository = Depends(UserRepository)) -> UserService:
    return UserService(user_repo)

def get_s3_service() -> S3Service:
    return S3Service()

def require_worker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != RoleEnum.worker:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return current_user

@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup_worker(
    user_in: WorkerSignup,
    user_service: UserService = Depends(get_user_service)
):
    # Pass WorkerSignup attributes properly
    return await user_service.signup_worker(user_in)

@router.post("/onboarding/step1", response_model=WorkerProfileResponse)
async def onboarding_step1(
    step1_data: WorkerOnboardingStep1,
    current_user: UserInDB = Depends(require_worker),
    user_repo: UserRepository = Depends(UserRepository)
):
    current_user.dob = step1_data.dob
    current_user.address = step1_data.address
    current_user.nationality = step1_data.nationality
    current_user.worker_type = step1_data.worker_type
    current_user.onboarding_complete1 = True
    await user_repo.update(current_user)
    
    # We construct the response
    return await _build_worker_response(current_user)

@router.post("/onboarding/step2", response_model=WorkerProfileResponse)
async def onboarding_step2(
    id_card_front: UploadFile = File(...),
    id_card_back: UploadFile = File(...),
    profile_photo: Optional[UploadFile] = File(None),
    certificates: List[UploadFile] = File(default=[]),
    current_user: UserInDB = Depends(require_worker),
    user_repo: UserRepository = Depends(UserRepository),
    s3_service: S3Service = Depends(get_s3_service)
):
    if not current_user.onboarding_complete1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Please complete Step 1 first")
        
    if len(certificates) > 5:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Maximum 5 certificates allowed")

    # Upload ID Front
    front_url = await s3_service.upload_file(await id_card_front.read(), id_card_front.filename, id_card_front.content_type)
    current_user.id_card_front = front_url
    
    # Upload ID Back
    back_url = await s3_service.upload_file(await id_card_back.read(), id_card_back.filename, id_card_back.content_type)
    current_user.id_card_back = back_url
    
    # Upload Certificates
    cert_urls = []
    for cert in certificates:
        url = await s3_service.upload_file(await cert.read(), cert.filename, cert.content_type)
        if url:
            cert_urls.append(url)
    current_user.certificates = cert_urls
    
    # Upload Profile Photo with resize
    if profile_photo:
        processed_photo = process_image(await profile_photo.read())
        photo_url = await s3_service.upload_file(processed_photo, profile_photo.filename)
        current_user.profile_photo = photo_url

    await user_repo.update(current_user)
    return await _build_worker_response(current_user)

@router.post("/onboarding/confirm", response_model=WorkerProfileResponse)
async def confirm_onboarding(
    current_user: UserInDB = Depends(require_worker),
    user_repo: UserRepository = Depends(UserRepository)
):
    if not current_user.onboarding_complete1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Step 1 incomplete")
    if not current_user.id_card_front or not current_user.id_card_back:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Step 2 incomplete (missing ID cards)")
        
    current_user.is_profile_completed = True
    await user_repo.update(current_user)
    return await _build_worker_response(current_user)

@router.get("/profile", response_model=WorkerProfileResponse)
async def get_worker_profile(current_user: UserInDB = Depends(require_worker)):
    return await _build_worker_response(current_user)

async def _build_worker_response(user: UserInDB) -> WorkerProfileResponse:
    user_data = user.model_dump(by_alias=True)
    user_data["id_uploaded"] = bool(user.id_card_front and user.id_card_back)
    user_data["id_card_front_link"] = user.id_card_front
    user_data["id_card_back_link"] = user.id_card_back
    user_data["certificate_uploaded"] = len(user.certificates) > 0
    user_data["certificate_count"] = len(user.certificates)
    user_data["certificate_links"] = user.certificates
    user_data["profile_photo_uploaded"] = bool(user.profile_photo)
    user_data["profile_photo_link"] = user.profile_photo
    return WorkerProfileResponse(**user_data)
