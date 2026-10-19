from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from server.custom_logging import CustomLoggerFactory
from server.setup.app_state import get_api_router, get_access_controller

router = APIRouter()
logger = CustomLoggerFactory.get_logger(__name__)


def _ascii_fallback(filename: str) -> str:
    """A name safe to sit inside a quoted header parameter.

    Anything outside latin-1 becomes '_', and quotes and backslashes go
    with it — a quote would close the parameter early and let the rest of
    the name pose as further header parameters.
    """
    cleaned = "".join(
        character if 32 <= ord(character) < 127
        and character not in '"\\' else "_"
        for character in (filename or "")
    )
    return cleaned.strip() or "download"


@router.get("/download/{resource_ref}")
async def download_file(resource_ref: str, request: Request):
    try:
        user = get_access_controller().verify_request_auth(request)
        if not user:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        data, status_code = await get_api_router().route(
            {
                "endpoint": "Files:File:Download",
                "data": {
                    "resource_ref": resource_ref,
                },
            },
            user,
        )

        if status_code != 200:
            return JSONResponse(data, status_code=status_code)

        file_data = data.get("file_data")
        if file_data is None:
            return JSONResponse({"error": "File stream is missing"}, status_code=400)

        filename = data.get("filename") or resource_ref
        file_type = data.get("file_type") or "application/octet-stream"
        file_size = data.get("file_size")

        headers = data.get("headers") or {}

        # Built here, never taken from the connector. A header is bytes on
        # the wire and Starlette encodes them latin-1, so a name like
        # 報告.pdf raises rather than sends — the file uploads fine and is
        # then permanently undownloadable. RFC 5987's filename* carries any
        # name at all, percent-encoded, and the plain filename beside it is
        # what older clients read. Overwriting rather than defaulting is
        # the point: a connector that supplied a raw name would win a
        # setdefault, which is exactly how this went unnoticed.
        headers["Content-Disposition"] = (
            f"attachment; filename=\"{_ascii_fallback(filename)}\"; "
            f"filename*=UTF-8''{quote(filename)}"
        )

        if file_size:
            headers.setdefault("Content-Length", str(file_size))

        headers.setdefault("Content-Type", file_type)

        return StreamingResponse(
            file_data,
            media_type=file_type,
            headers=headers,
            background=BackgroundTask(file_data.close),
        )

    except Exception as e:
        logger.error(f"Error processing /download request: {e}", exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)
