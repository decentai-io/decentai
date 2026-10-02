from json import JSONDecodeError

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from server.setup.app_state import get_access_controller, get_api_router
from server.custom_logging import CustomLoggerFactory

router = APIRouter()
logger = CustomLoggerFactory.get_logger(__name__)


@router.post("/app")
async def application(request: Request):
    try:
        user = get_access_controller().verify_request_auth(request)
        if not user:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            data = await request.json()
        except JSONDecodeError:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        msg, status_code = await get_api_router().route(data, user)

        return JSONResponse(
            content=jsonable_encoder(msg),
            status_code=status_code,
        )

    except Exception as e:
        logger.error(f"Error processing /app request: {e}", exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)
