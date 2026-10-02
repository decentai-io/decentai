"""The Screen fixture agent (see manifest.yaml): frames out, input in."""

import asyncio
import base64

from decentai_sdk.base import AgentBase, ToolBase

#: A 1×1 JPEG. What it shows does not matter; that it travels does.
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABALDA4MChAODQ4SERATGCgaGBYWGDEjJR0oOjM9PDkzODdASFxOQERXRTc4UG1RV19iZ2hnPk1xeXBkeFxlZ2P/2wBDARESEhgVGC8aGi9jQjhCY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2P/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD0CiiigD//2Q=="
)


class ShowTool(ToolBase):
    id = "show"

    async def run(self, call):
        shown = 0
        # What is open behind the picture, told with it — and one thing
        # that is no tab, which a frame leaves out rather than fails on.
        tabs = [{"index": 1, "title": "Inbox", "address": "https://mail.example/", "active": True},
                {"index": 2, "title": "T" * 500}, "not a tab"]
        for _ in range(3):
            if await call.screen.show(JPEG, width=1, height=1, tabs=tabs):
                shown += 1
            await asyncio.sleep(0.05)
        events = await call.screen.wait_input(
            float(call.inputs.get("wait_seconds") or 1.0))
        taken = call.screen.taken
        said = call.screen.said()
        await call.screen.close()
        return {"frames": shown, "inputs": events, "taken": taken,
                "said": said, "conversation": call.conversation}, "success"

    async def watch(self, call):
        """The screen on request: frames until the person closes the
        panel or a while passes, as a browser agent's watch would."""
        if call.inputs.get("action") == "quit":
            return {"outcome": "quit", "frames": 0}, "success"
        shown = 0
        deadline = asyncio.get_running_loop().time() + 5
        while not call.screen.closed and asyncio.get_running_loop().time() < deadline:
            if await call.screen.show(JPEG, width=1, height=1):
                shown += 1
            await call.screen.wait_input(0.2)
        outcome = "closed" if call.screen.closed else "timeout"
        await call.screen.close()
        return {"outcome": outcome, "frames": shown}, "success"


class ScreenAgent(AgentBase):
    def tools(self):
        return [ShowTool(self)]
