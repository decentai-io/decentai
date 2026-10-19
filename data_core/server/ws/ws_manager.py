import asyncio


class WSManager:
    def __init__(self):
        self.frontends = {}
        self.lock = asyncio.Lock()

    @staticmethod
    def key(chat_id, user):
        return f"{user.get('org_id')}:{user.get('user_id')}:{chat_id}"

    async def connect(self, websocket, chat_id, user):
        key = self.key(chat_id, user)
        async with self.lock:
            previous = self.frontends.get(key)
            self.frontends[key] = websocket
        if previous is not None and previous is not websocket:
            await previous.close(code=4001, reason="Replaced by a newer connection")

    async def disconnect(self, websocket, chat_id, user):
        """Remove this socket only if it is still the registered one, and
        say so — a socket that was replaced (4001) no longer owns the
        chat's connections and must not tear down its successor's."""
        key = self.key(chat_id, user)
        async with self.lock:
            if self.frontends.get(key) is websocket:
                self.frontends.pop(key, None)
                return True
        return False

    async def send_frontend(self, chat_id, user, message):
        websocket = self.frontends.get(self.key(chat_id, user))
        if websocket is None:
            return False
        try:
            await websocket.send_json(message)
            return True
        except Exception:
            await self.disconnect(websocket, chat_id, user)
            return False
