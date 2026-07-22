import aioboto3
import uuid
import os
import io
from typing import Optional
from PIL import Image, ImageOps
from app.core.config import settings

def _resize_image_to_1080(file_bytes: bytes, target_width: int = 1080) -> tuple[bytes, str]:
    """
    Resizes image to 1080px width while scaling height to match exact picture aspect ratio.
    Automatically handles mobile photo EXIF orientations.
    Returns (resized_file_bytes, content_type).
    """
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img = ImageOps.exif_transpose(img)

        width, height = img.size
        if width > 0 and height > 0:
            aspect_ratio = height / width
            new_width = target_width
            new_height = max(1, int(new_width * aspect_ratio))

            img_resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            buf = io.BytesIO()
            fmt = img.format if img.format else "JPEG"
            
            # Convert RGBA/P to RGB if saving as JPEG
            if fmt.upper() in ["JPEG", "JPG"] and img_resized.mode in ["RGBA", "LA", "P"]:
                img_resized = img_resized.convert("RGB")

            img_resized.save(buf, format=fmt, quality=90)
            mime_type = f"image/{fmt.lower()}" if fmt.lower() != "jpg" else "image/jpeg"
            return buf.getvalue(), mime_type
    except Exception as e:
        print(f"Non-image or Pillow resize error, skipping resize: {e}")
        
    return file_bytes, "image/jpeg"

class S3Service:
    def __init__(self):
        self.session = aioboto3.Session(
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_REGION
        )
        self.bucket_name = settings.AWS_S3_BUCKET_NAME

    async def upload_file(self, file_bytes: bytes, file_name: str, content_type: str = "image/jpeg") -> Optional[str]:
        # Automatically resize images to 1080px width preserving picture aspect ratio
        is_image = content_type.startswith("image/") or any(
            file_name.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"]
        )
        
        if is_image:
            file_bytes, content_type = _resize_image_to_1080(file_bytes, target_width=1080)

        # Clean the file name and make it unique
        unique_name = f"{uuid.uuid4()}_{file_name.replace(' ', '_')}"
        
        if not self.bucket_name or not settings.AWS_ACCESS_KEY_ID:
            os.makedirs("uploads", exist_ok=True)
            file_path = os.path.join("uploads", unique_name)
            with open(file_path, "wb") as f:
                f.write(file_bytes)
            print(f"Saved locally as S3 is not configured: {file_path}")
            return f"http://127.0.0.1:8080/uploads/{unique_name}"
        
        try:
            async with self.session.client("s3") as s3:
                await s3.put_object(
                    Bucket=self.bucket_name,
                    Key=unique_name,
                    Body=file_bytes,
                    ContentType=content_type
                )
                
            region = settings.AWS_REGION or "us-east-1"
            return f"https://{self.bucket_name}.s3.{region}.amazonaws.com/{unique_name}"
        except Exception as e:
            print(f"Failed to upload to S3: {e}")
            return None

    async def delete_file(self, file_url: str):
        if not self.bucket_name or not file_url.startswith("https://"):
            return
            
        try:
            object_key = file_url.split(".amazonaws.com/")[-1]
            async with self.session.client("s3") as s3:
                await s3.delete_object(Bucket=self.bucket_name, Key=object_key)
        except Exception as e:
            print(f"Failed to delete from S3: {e}")
