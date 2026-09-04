import uuid
import base64
import re
from typing import List, Optional, Union
from fastapi import UploadFile
from app.services.s3_service import S3Service
from app.schemas.escalation import WorkerEscalationCreate

WORKER_ESCALATION_OPENAPI_EXTRA = {
    "requestBody": {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Short summary of the incident or problem",
                            "example": "Broken Keycard / Inaccessible Room"
                        },
                        "description": {
                            "type": "string",
                            "description": "Comprehensive explanation of the issue encountered",
                            "example": "Door handle is detached and room cannot be entered."
                        },
                        "category": {
                            "type": "string",
                            "description": "Category of incident. Supported: 'maintenance', 'safety', 'access', 'cleaning', 'equipment', 'client_dispute', 'other'",
                            "enum": ["maintenance", "safety", "access", "cleaning", "equipment", "client_dispute", "other"],
                            "default": "maintenance",
                            "example": "maintenance"
                        },
                        "severity": {
                            "type": "string",
                            "description": "Severity level. Supported: 'high', 'medium', 'low'",
                            "enum": ["high", "medium", "low"],
                            "default": "high",
                            "example": "high"
                        },
                        "shift_id": {
                            "type": "string",
                            "description": "Shift execution or plan ID if tied to a shift",
                            "example": "exec_plan_6141aedb01_2026-09-01"
                        },
                        "room_id": {
                            "type": "string",
                            "description": "Room ID if tied to a specific room",
                            "example": "room_01"
                        },
                        "location_name": {
                            "type": "string",
                            "description": "Facility or building name",
                            "example": "Grand Hotel Central"
                        },
                        "room_name": {
                            "type": "string",
                            "description": "Room name or number",
                            "example": "Room 107"
                        },
                        "photos": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": "One or more photo evidence files to upload to Amazon S3"
                        },
                        "photo": {
                            "type": "string",
                            "format": "binary",
                            "description": "Single photo evidence file to upload to Amazon S3"
                        },
                        "photo_urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Direct image URLs or base64 image strings (will be uploaded to Amazon S3)",
                            "example": ["https://cleanones-bucket.s3.eu-central-1.amazonaws.com/uploads/door.jpg"]
                        }
                    },
                    "required": ["title", "description"]
                }
            },
            "application/json": {
                "schema": WorkerEscalationCreate.model_json_schema()
            }
        },
        "required": True
    }
}

WORKER_SHIFT_ESCALATION_OPENAPI_EXTRA = {
    "requestBody": {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Short summary of the incident or problem",
                            "example": "Broken Keycard / Inaccessible Room"
                        },
                        "description": {
                            "type": "string",
                            "description": "Comprehensive explanation of the issue encountered",
                            "example": "Door handle is detached and room cannot be entered."
                        },
                        "category": {
                            "type": "string",
                            "description": "Category of incident. Supported: 'maintenance', 'safety', 'access', 'cleaning', 'equipment', 'client_dispute', 'other'",
                            "enum": ["maintenance", "safety", "access", "cleaning", "equipment", "client_dispute", "other"],
                            "default": "maintenance",
                            "example": "maintenance"
                        },
                        "severity": {
                            "type": "string",
                            "description": "Severity level. Supported: 'high', 'medium', 'low'",
                            "enum": ["high", "medium", "low"],
                            "default": "high",
                            "example": "high"
                        },
                        "room_id": {
                            "type": "string",
                            "description": "Room ID if tied to a specific room",
                            "example": "room_01"
                        },
                        "location_name": {
                            "type": "string",
                            "description": "Facility name (optional, inherited from shift)",
                            "example": "Grand Hotel Central"
                        },
                        "room_name": {
                            "type": "string",
                            "description": "Room name or number (optional, inherited from shift)",
                            "example": "Room 107"
                        },
                        "photos": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": "One or more photo evidence files to upload to Amazon S3"
                        },
                        "photo": {
                            "type": "string",
                            "format": "binary",
                            "description": "Single photo evidence file to upload to Amazon S3"
                        },
                        "photo_urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Direct image URLs or base64 image strings (will be uploaded to Amazon S3)",
                            "example": ["https://cleanones-bucket.s3.eu-central-1.amazonaws.com/uploads/door.jpg"]
                        }
                    },
                    "required": ["title", "description"]
                }
            },
            "application/json": {
                "schema": WorkerEscalationCreate.model_json_schema()
            }
        },
        "required": True
    }
}


async def process_photos_to_s3(
    files: Optional[List[UploadFile]] = None,
    raw_strings: Optional[List[str]] = None,
    s3_service: Optional[S3Service] = None
) -> List[str]:
    """
    Takes uploaded binary image files and/or string entries (base64 data URIs or existing URLs),
    resizes and uploads them to Amazon S3, and returns a deduplicated list of permanent S3 URLs.
    """
    if s3_service is None:
        s3_service = S3Service()

    uploaded_urls: List[str] = []
    seen = set()

    # 1. Process binary uploaded files
    if files:
        for f in files:
            if not f or not hasattr(f, "filename") or not f.filename:
                continue
            try:
                file_bytes = await f.read()
                if not file_bytes:
                    continue
                content_type = getattr(f, "content_type", None) or "image/jpeg"
                filename = f.filename
                s3_url = await s3_service.upload_file(file_bytes, filename, content_type)
                if s3_url and s3_url not in seen:
                    seen.add(s3_url)
                    uploaded_urls.append(s3_url)
            except Exception as e:
                print(f"[Escalation File Upload Error] {getattr(f, 'filename', 'unknown')}: {e}")

    # 2. Process string URLs / base64 payloads
    if raw_strings:
        for item in raw_strings:
            if not item or not isinstance(item, str):
                continue
            s_val = item.strip()
            if not s_val or s_val in seen:
                continue

            # If it's already an HTTP / HTTPS URL
            if s_val.startswith("http://") or s_val.startswith("https://"):
                seen.add(s_val)
                uploaded_urls.append(s_val)
                continue

            # Check if it's a base64 encoded image
            mime_type = "image/jpeg"
            ext = "jpg"
            b64_content = s_val

            data_uri_match = re.match(r"^data:(image\/[a-zA-Z0-9.+_-]+);base64,(.*)$", s_val, re.DOTALL)
            if data_uri_match:
                mime_type = data_uri_match.group(1).lower()
                b64_content = data_uri_match.group(2).strip()
                if "png" in mime_type:
                    ext = "png"
                elif "webp" in mime_type:
                    ext = "webp"
                elif "gif" in mime_type:
                    ext = "gif"

            try:
                file_bytes = base64.b64decode(b64_content)
                if file_bytes and len(file_bytes) > 0:
                    fname = f"escalation_{uuid.uuid4().hex[:8]}.{ext}"
                    s3_url = await s3_service.upload_file(file_bytes, fname, mime_type)
                    if s3_url and s3_url not in seen:
                        seen.add(s3_url)
                        uploaded_urls.append(s3_url)
            except Exception as b_err:
                print(f"[Escalation Base64 Decode Error] {b_err}")

    return uploaded_urls
