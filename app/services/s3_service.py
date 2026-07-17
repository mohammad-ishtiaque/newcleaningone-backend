import aioboto3
import uuid
import os
from typing import Optional
from app.core.config import settings

class S3Service:
    def __init__(self):
        self.session = aioboto3.Session(
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_REGION
        )
        self.bucket_name = settings.AWS_S3_BUCKET

    async def upload_file(self, file_bytes: bytes, file_name: str, content_type: str = "image/jpeg") -> Optional[str]:
        if not self.bucket_name or not settings.AWS_ACCESS_KEY_ID:
            print(f"Mock S3 Upload: {file_name}")
            return f"https://mock-s3-url.com/{file_name}"
            
        unique_name = f"{uuid.uuid4()}_{file_name}"
        
        try:
            async with self.session.client("s3") as s3:
                await s3.put_object(
                    Bucket=self.bucket_name,
                    Key=unique_name,
                    Body=file_bytes,
                    ContentType=content_type,
                    ACL="public-read"
                )
                
            region = settings.AWS_REGION or "us-east-1"
            return f"https://{self.bucket_name}.s3.{region}.amazonaws.com/{unique_name}"
        except Exception as e:
            print(f"Failed to upload to S3: {e}")
            return None

    async def delete_file(self, file_url: str):
        if not self.bucket_name or not file_url.startswith("https://"):
            return
            
        # Extract object key from URL
        try:
            object_key = file_url.split(".amazonaws.com/")[-1]
            async with self.session.client("s3") as s3:
                await s3.delete_object(Bucket=self.bucket_name, Key=object_key)
        except Exception as e:
            print(f"Failed to delete from S3: {e}")
