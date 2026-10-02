import json
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from server.custom_logging import CustomLoggerFactory
from server.setup.app_state import (
    get_access_controller, get_api_router, get_settings,
)

router = APIRouter()
logger = CustomLoggerFactory.get_logger(__name__)


@router.post("/upload")
async def upload(request: Request):
    try:
        # Authenticate BEFORE touching the body: parsing multipart buffers the
        # whole upload, and that work is not owed to anonymous callers.
        user = get_access_controller().verify_request_auth(request)
        if not user:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        form = await request.form()

        uploaded_file_value = form.get("file")

        if not isinstance(uploaded_file_value, UploadFile):
            return JSONResponse({"error": "file is required"}, status_code=400)

        uploaded_file: UploadFile = uploaded_file_value

        additional_data_raw = form.get("additionalData", "{}")

        if not isinstance(additional_data_raw, str):
            return JSONResponse(
                {"error": "additionalData must be a JSON string"},
                status_code=400,
            )

        try:
            additional_data: dict[str, Any] = json.loads(additional_data_raw)
        except Exception as e:
            logger.error(f"Error parsing additional data: {e}", exc_info=True)
            return JSONResponse({"error": "invalid json"}, status_code=400)

        endpoint = additional_data.get("endpoint")

        if not endpoint:
            return JSONResponse({"error": "endpoint is required"}, status_code=400)

        # Read back to the cap plus one byte: enough to know it was
        # exceeded without holding an oversized file in memory. (The
        # form parser has already spooled the body to disk; what the
        # cap protects is the process's memory.)
        limit = get_settings().max_upload_mb * 1024 * 1024
        file_bytes = await uploaded_file.read(limit + 1)
        if len(file_bytes) > limit:
            return JSONResponse(
                {"error": f"Files must be "
                          f"{get_settings().max_upload_mb} MB or smaller."},
                status_code=413,
            )

        route_data = {
            "endpoint": endpoint,
            "data": {
                "filename": uploaded_file.filename,
                "content_type": uploaded_file.content_type,
                "file_bytes": BytesIO(file_bytes),
                "additional_data": additional_data,
            },
        }

        msg, status_code = await get_api_router().route(route_data, user)

        return JSONResponse(
            content=jsonable_encoder(msg),
            status_code=status_code,
        )

    except Exception as e:
        logger.error(f"Error processing /upload request: {e}", exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)
