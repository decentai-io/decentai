"""What removing a person touches: what they shared passes to a successor, their private data and files too; their
private secrets, chats, schedules, memories and ways of being reached go; the trail keeps their lines
(api-server.md §4)."""

from __future__ import annotations

from ..chats.endpoints import ChatEndpoints
from ..chats.store import Chats, Remembered, Schedules
from ..records.store import Records
from ..settings.assistant import McpServers, Memories, Skills
from ..settings.notify import NotifyTo
from ..settings.webhooks import Webhooks


class Leaving:
    def count(self, org_id: str, user_id: str) -> dict[str, int]:
        mine = {"org_id": org_id, "created_by": user_id}
        return {"chats": Chats().col.count_documents({"user_id": user_id}),
                "schedules": Schedules().col.count_documents({"user_id": user_id}),
                "records_handed_on": Records().col.count_documents({**mine, "$or": [
                    {"kind": {"$ne": "secret"}}, {"owner.groups.0": {"$exists": True}}, {"owner.users.1": {"$exists": True}}]}),
                "secrets_deleted": Records().col.count_documents({**mine, "kind": "secret", "owner.groups": [],
                                                                  "owner.users": [user_id]}),
                "skills": Skills().col.count_documents(mine), "mcp_servers": McpServers().col.count_documents(mine),
                "webhooks": Webhooks().col.count_documents({"user_id": user_id})}

    def hand_on(self, org_id: str, user_id: str, successor: str) -> None:
        mine = {"org_id": org_id, "created_by": user_id}
        private_secret = {**mine, "kind": "secret", "owner.groups": [], "owner.users": [user_id]}
        Records().col.delete_many(private_secret)
        for store in (Records(), Skills(), McpServers()):
            for doc in store.col.find(mine):
                users = list(dict.fromkeys([u for u in doc["owner"]["users"] if u != user_id] + [successor]))
                store.col.update_one({"_id": doc["_id"]}, {"$set": {"created_by": successor, "handing": None,
                                                                    "owner": {**doc["owner"], "users": users}}})
        for chat in list(Chats().col.find({"user_id": user_id})):
            ChatEndpoints.erase(chat)
        for store, query in ((Webhooks(), {"user_id": user_id}), (Memories(), {"user_id": user_id}),
                             (NotifyTo(), {"user_id": user_id}), (Remembered(), {"user_id": user_id})):
            store.col.delete_many(query)
