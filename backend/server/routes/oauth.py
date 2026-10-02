"""GET /oauth/callback — where every provider sends the browser back.

The provider sends nothing we can trust but ``state``, which names who
started the flow and for what, and is spent as it is read. It must also
come back to the browser that started it: the session cookie the popup
carries (sent on a top-level return from the provider) has to be the
session that chose Connect. Otherwise somebody could start a flow, send
the consent link to a colleague, and keep the colleague's account as
their own credential. What comes back is a small page that tells the
window that opened the popup how it went, and closes.
"""

from __future__ import annotations

import html
import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from server.custom_logging import CustomLoggerFactory
from server.setup.app_state import get_access_controller, get_settings

router = APIRouter()
logger = CustomLoggerFactory.get_logger(__name__)


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font: 15px system-ui, sans-serif; color: #222; background: #fafafa;
         display: grid; place-items: center; height: 100vh; margin: 0; }}
  main {{ max-width: 420px; padding: 28px; text-align: center; }}
  p {{ color: #555; }}
</style>
<main>
  <h2>{title}</h2>
  <p>{detail}</p>
</main>
<script>
  (function () {{
    var result = {payload};
    try {{
      if (window.opener) {{
        window.opener.postMessage(result, {origin});
        window.close();
      }}
    }} catch (e) {{}}
  }})();
</script>
"""


def _script_json(value) -> str:
    """JSON that cannot end the script it sits in. The error text comes
    from the query string, and json.dumps leaves ``</script>`` as it is
    (it already escapes every non-ASCII character, U+2028 included)."""
    return (json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


def _page(ok: bool, title: str, detail: str, resource_ref: str = "") -> HTMLResponse:
    origin = str(get_settings().public_app_url or "").rstrip("/") or "*"
    payload = {"type": "decentai:oauth", "ok": ok,
               "resource_ref": resource_ref, "error": "" if ok else detail}
    return HTMLResponse(PAGE.format(
        title=html.escape(title), detail=html.escape(detail),
        payload=_script_json(payload), origin=_script_json(origin),
    ), status_code=200 if ok else 400)


def _same_browser(request: Request, state) -> bool:
    """The callback came back to the person, and the browser session,
    that started the flow."""
    user = get_access_controller().verify_request_auth(request)
    if not user or user.get("principal_type") != "user":
        return False
    if str(user.get("user_id") or "") != str(state.get("user_id") or ""):
        return False
    started_in = str(state.get("session_id") or "")
    return not started_in or str(user.get("session_id") or "") == started_in


@router.get("/oauth/callback")
async def oauth_callback(request: Request):
    from api.services.oauth import OauthError, OauthFlow
    from database.stores.settings.oauth import OauthStateStore

    query = request.query_params
    state = OauthStateStore().spend(str(query.get("state") or ""))
    if state is None:
        return _page(False, "This link has expired",
                     "Go back to DecentAI and choose Connect again.")
    if not _same_browser(request, state):
        return _page(False, "Not connected",
                     "Finish connecting in the DecentAI window where you "
                     "chose Connect. Go back to it and choose Connect again.")

    if query.get("error"):
        detail = str(query.get("error_description") or query.get("error"))
        return _page(False, "Not connected", f"The provider said: {detail}")

    code = str(query.get("code") or "")
    if not code:
        return _page(False, "Not connected", "The provider sent no authorization code.")

    try:
        secret = await OauthFlow().complete(state, code)
    except OauthError as exc:
        return _page(False, "Not connected", str(exc))
    except Exception as exc:  # noqa: BLE001 — the page must still answer
        logger.error(f"oauth callback failed: {exc}", exc_info=True)
        return _page(False, "Not connected", "Something went wrong on our side. Try again.")

    account = str((secret.get("keys") or {}).get("account") or "")
    return _page(True, "Connected",
                 f"{account or 'The account'} is now saved as a credential. "
                 f"You can close this window.",
                 resource_ref=str(secret.get("resource_ref") or ""))
