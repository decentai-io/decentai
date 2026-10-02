import hashlib
import json

from decentai_sdk.base import ToolBase


class SyncTool(ToolBase):
    id = "sync"

    async def status(self, call):
        # The remote is simulated; the CONNECTION is real — the bound
        # secret's decrypted values arrive through the mediated access.
        try:
            secret = await call.resources.use_secret("connection")
        except Exception:
            return {"connected": False, "remote": "unbound"}, "success"
        return {
            "connected": bool(secret.get("api_token")),
            "remote": "simulated://notebook",
        }, "success"

    async def push(self, call):
        notebook = str(call.inputs["notebook"]).lower()

        try:
            secret = await call.resources.use_secret("connection")
        except Exception:
            return {"error": "No sync connection is bound."}, "error"

        records = await call.resources.list_data("note", {"notebook": notebook})
        await call.progress(f"Pushing {len(records)} note(s) from '{notebook}'")

        canonical = json.dumps(
            [record["keys"] for record in records], sort_keys=True, default=str,
        )
        digest = hashlib.sha256(
            (canonical + str(secret.get("api_token") or "")).encode("utf-8")
        ).hexdigest()
        return {"pushed": len(records), "digest": digest}, "success"
