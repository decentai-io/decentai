"""The live screen (docs/system/live-screen.md): the browser a chat's
agent drives, shown to whoever watches the chat.

A call shows frames through its own ``screen`` door while it runs
(execution/call.py); what is here is the chat's side: opening a
browser before anything is asked of it, through the roster's agent
that declares a ``watch`` function, called with no model and no turn;
the person's hand on the screen, passed to the call showing it, here
or in a helper; and closing the browser the chat kept when the chat
is stopped.
"""

from __future__ import annotations

import asyncio


class Screen:
    #: how long closing the chat's browser may take before the kill
    #: goes on without it
    QUIT_BROWSER_SECONDS = 20.0

    async def _quit_browser(self) -> None:
        """The browser this chat kept, closed through the roster's watch
        function — bounded, and skipped when no agent can show one."""
        for agent in self.roster.values():
            manifest = getattr(agent, "manifest", None)
            if manifest is None:
                continue
            for name, _tool, function in manifest.functions():
                if function.get("watch") is True:
                    try:
                        await asyncio.wait_for(
                            self._watch(agent, agent.granted(name), "quit"),
                            self.QUIT_BROWSER_SECONDS)
                    except (asyncio.TimeoutError, Exception) as exc:
                        self.logger.warning(f"Browser of {self.chat_id} not closed: {exc}")
                    return

    async def open_screen(self, action: str = "open") -> bool:
        """The person asked to see a browser before asking anything of
        it. The roster's agent that declares a watch function is called
        directly — no model, no turn, level 0 by contract — and streams
        its screen until the person closes the panel or a run takes the
        browser. One at a time; asked again while one shows, nothing
        changes. Without such an agent the page is told."""
        if action == "open" and self.watching is not None and not self.watching.done():
            return True
        for agent in self.roster.values():
            manifest = getattr(agent, "manifest", None)
            if manifest is None:
                continue
            for name, _tool, function in manifest.functions():
                if function.get("watch") is True:
                    canonical = agent.granted(name)
                    task = asyncio.get_running_loop().create_task(
                        self._watch(agent, canonical, action))
                    if action == "open":
                        self.watching = task
                    return True
        await self._relay({
            "event": "screen_unavailable",
            "detail": "No agent in this chat can show a browser."})
        return False

    async def _watch(self, agent, canonical: str, action: str = "open") -> None:
        try:
            result, status = await self.assistant.executor.invoke(
                agent, canonical, {"action": action} if action != "open" else {},
                chat_level=self.chat_level)
            self.logger.info(f"Watch {canonical} ended: {status} "
                             f"{str(result)[:120]}")
        except Exception as exc:
            self.logger.warning(f"Watch {canonical} failed: {exc}")

    async def deliver_screen_input(self, call_id: str, events: list) -> bool:
        """The person acting on a screen — mouse, keys, wheel, taking or
        releasing control — to the call showing it, here or in a live
        child."""
        if call_id in self.cards.screens:
            return await self.assistant.executor.screen_input(call_id, events)
        for child in self.helpers():
            if await child.deliver_screen_input(call_id, events):
                return True
        return False
