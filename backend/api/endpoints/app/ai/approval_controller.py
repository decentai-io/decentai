"""The cards (docs/system/chat-session.md).

A call that parks on a human becomes a record here: the runtime opens
it, the person decides it, and the decision travels back to the runtime
as the door's ``approval_decided`` frame. A question an agent asks
(call.ask) is a card of the same kind, answered in words and carried
back as ``question_answered``; code an agent wants to run (call.propose)
is that card again, answered with allow or deny; one nobody answers in
time the runtime closes with ``expire``. The runtime never decides its
own escalation — ``open`` is runtime-only and ``decide`` is user-only —
and a decision lands once; a second one is refused, never recorded over
the first.
"""

from contracts.chat import (
    ANSWER_MAX_CHARS, CHOICE_MAX_CHARS, CHOICES_MAX, FILE_CANDIDATES_MAX,
    FILES_CHOSEN_MAX, QUESTION_MAX_CHARS, CodeAsk, CredentialAsk,
)
from api.services.chat_session.code_rules import CodeRules
from database.stores import ApprovalStore, AuditStore, OrganizationStore
from server.setup.app_state import get_runtime_clients
from util import new_id, utc_now

from .base import AIController


class ApprovalController(AIController):
    def open(self, data, user):
        chat, refusal = self._runtime_chat(data, user, "open approvals")
        if refusal is not None:
            return refusal

        thread, refusal = self._thread(data)
        if refusal is not None:
            return refusal

        request = self._payload(data).get("request")
        if not isinstance(request, dict):
            return self._fail(
                data, "invalid_request", "request must be an object.")
        function = str(request.get("function") or "")
        if not function:
            return self._fail(
                data, "invalid_request", "request.function is required.")
        kind = "question" if request.get("kind") == "question" else "approval"
        question, choices, expects, candidates, credential = "", [], "", [], None
        code = None
        if kind == "question":
            question = str(request.get("question") or "").strip()
            choices = request.get("choices") or []
            expects = str(request.get("expects") or "").strip().lower()
            if expects not in ("", "text", "file", "files", "credential",
                               "code"):
                return self._fail(
                    data, "invalid_request",
                    "A question expects 'text', 'file', 'files', "
                    "'credential' or 'code'.")
            if expects == "code":
                # The code whole, what it is for, what it needs and what
                # the assistant made of it — the contract's own shape,
                # so the page and the record agree.
                try:
                    code = CodeAsk.model_validate(
                        request.get("code") or {}).model_dump(exclude_none=True)
                except Exception as exc:
                    return self._fail(
                        data, "invalid_request",
                        f"code is not a code card: {exc}")
            if expects == "credential":
                # Which card, for which host, in whose name — the
                # contract's own shape, so the page and the record agree.
                try:
                    credential = CredentialAsk.model_validate(
                        request.get("credential") or {}).model_dump()
                except Exception as exc:
                    return self._fail(
                        data, "invalid_request",
                        f"credential is not a credential card: {exc}")
            if expects == "files":
                # What the assistant found, for the card to propose. The
                # page shows these; the answer is what the person chose,
                # checked against what they may see when it arrives.
                candidates = self._candidates(request.get("candidates"))
                if candidates is None:
                    return self._fail(
                        data, "invalid_request",
                        f"candidates must be at most {FILE_CANDIDATES_MAX} "
                        f"files, each with a resource_ref.")
            if not question or len(question) > QUESTION_MAX_CHARS:
                return self._fail(
                    data, "invalid_request",
                    f"A question needs 1 to {QUESTION_MAX_CHARS} characters.")
            if (not isinstance(choices, list) or len(choices) > CHOICES_MAX
                    or not all(isinstance(c, str) and 0 < len(c) <= CHOICE_MAX_CHARS
                               for c in choices)):
                return self._fail(
                    data, "invalid_request",
                    f"choices must be at most {CHOICES_MAX} texts of "
                    f"{CHOICE_MAX_CHARS} characters or fewer.")

        approval_id = f"apr_{new_id()}"
        document = {
            **self._identity(user, chat["chat_id"]),
            "approval_id": approval_id,
            "kind": kind,
            # Whose card: a child's is answered by the parent's audience
            # and routed back to the child by the runtime.
            "thread": thread or None,
            "request": {
                "agent_id": str(request.get("agent_id") or ""),
                "agent_name": str(request.get("agent_name") or ""),
                "function": function,
                "inputs": request.get("inputs")
                if isinstance(request.get("inputs"), dict) else {},
                "permission_level": request.get("permission_level"),
                "chat_level": request.get("chat_level"),
                # Binds the approved inputs to what may later run — the
                # runtime re-verifies this hash before any resumed call.
                "action_hash": str(request.get("action_hash") or "") or None,
                "job_id": str(request.get("job_id") or "") or None,
                **({"question": question, "choices": list(choices),
                    **({"expects": expects} if expects else {}),
                    **({"candidates": candidates,
                        "query": str(request.get("query") or "")[:200]}
                       if expects == "files" else {}),
                    **({"credential": credential}
                       if expects == "credential" else {}),
                    **({"code": code,
                        # Which call is asking: a correction is known by it.
                        "call_id": str(request.get("call_id") or "")[:64]}
                       if expects == "code" else {})}
                   if kind == "question" else {}),
            },
            "status": ApprovalStore.PENDING,
            "requested_at": utc_now(),
            "resolved_at": None,
            "resolved_by": None,
            "decision": None,
        }
        approvals = ApprovalStore()
        settled = expects == "code" and CodeRules(
            OrganizationStore().safety(str(user.get("org_id") or "")),
            approvals).settles(chat["chat_id"], document["request"])
        if settled:
            # The organization chose not to be asked for code like this
            # (Settings:Safety): the card is on the record, answered by
            # that setting, and nobody is shown it.
            document.update({
                "status": ApprovalStore.ANSWERED, "decision": CodeRules.SETTING,
                "answer": "allow",
                "answer_label": "Allowed by the Safety setting",
                "resolved_at": utc_now(), "resolved_by": CodeRules.SETTING,
            })
        approvals.open(document)
        if not settled:
            # The person may not be looking: tell them, unless they are.
            from server.notifications import Notifier

            Notifier().card_opened(chat, document)
        AuditStore().append(
            "approval.requested", user, chat_id=chat["chat_id"],
            function=function, resource_refs=[approval_id],
            details={"permission_level": request.get("permission_level"),
                     "chat_level": request.get("chat_level"),
                     "kind": kind},
        )
        if settled:
            AuditStore().append(
                "approval.resolved", user, chat_id=chat["chat_id"],
                function=function, resource_refs=[approval_id],
                details={"decision": CodeRules.SETTING, "kind": "question",
                         "code": "allow"},
            )
            return self._respond(data, {"approval_id": approval_id,
                                        "settled": "allow"})
        return self._respond(data, {"approval_id": approval_id})

    @staticmethod
    def _candidates(raw):
        """The files a files question proposes, in the shape the card
        shows — or None when the request is not that."""
        if raw is None:
            return []
        if not isinstance(raw, list) or len(raw) > FILE_CANDIDATES_MAX:
            return None
        cleaned = []
        for item in raw:
            if not isinstance(item, dict) or not str(
                    item.get("resource_ref") or ""):
                return None
            size = item.get("file_size")
            cleaned.append({
                "resource_ref": str(item["resource_ref"]),
                "filename": str(item.get("filename") or "")[:255],
                "file_type": str(item.get("file_type") or "")[:100],
                "file_size": int(size) if isinstance(size, int)
                and not isinstance(size, bool) and size >= 0 else 0,
                "source": str(item.get("source") or "")[:40],
                "created_at": str(item.get("created_at") or "")[:40],
            })
        return cleaned

    def list(self, data, user):
        """The pending cards of a chat — what a late audience must be
        able to find, and what a session opening reads to close the
        questions a dead process left waiting."""
        if user.get("principal_type") == "runtime":
            chat, refusal = self._runtime_chat(data, user, "list cards")
        else:
            chat, refusal = self._chat_or_refusal(data, user)
        if refusal is not None:
            return refusal
        pending = ApprovalStore().pending_in_chat(chat["chat_id"])
        return self._respond(data, {
            "approvals": [self._public(a) for a in pending],
        })

    async def decide(self, data, user):
        """The person's decision: recorded first, then delivered."""
        if user.get("principal_type") == "runtime":
            return self._fail(
                data, "forbidden",
                "A runtime never decides its own escalation.", 403)

        payload = self._payload(data)
        approval_id = str(payload.get("approval_id") or "")
        approvals = ApprovalStore()
        approval = approvals.owned(approval_id, self._owner(user))
        if approval is None:
            return self._fail(data, "not_found", "Approval not found.", 404)
        if approval.get("kind") == "question":
            return await self._answer(data, user, approvals, approval, payload)

        decision = str(payload.get("decision") or "").lower()
        if decision not in ("approve", "deny"):
            return self._fail(
                data, "invalid_decision",
                "decision must be approve or deny.")
        if approval["status"] != approvals.PENDING:
            return self._fail(
                data, "not_pending",
                f"Approval is already {approval['status']}.", 409)

        approved = decision == "approve"
        status = approvals.APPROVED if approved else approvals.DENIED
        if not approvals.decide(approval_id, {
            "status": status,
            "decision": decision,
            "resolved_at": utc_now(),
            "resolved_by": str(user.get("email") or ""),
        }):
            return self._fail(
                data, "not_pending", "Approval is already decided.", 409)

        AuditStore().append(
            "approval.resolved", user, chat_id=approval["chat_id"],
            function=(approval.get("request") or {}).get("function"),
            resource_refs=[approval_id], details={"decision": decision},
        )
        # The door's own frame; the relay carries it verbatim. The hash
        # recorded when the card was opened rides with the decision:
        # the runtime's independent copy of what was approved, which it
        # checks against the inputs it is about to run.
        delivered = await get_runtime_clients().send(
            approval["chat_id"], user, {
                "event": "approval_decided",
                "approval_id": approval_id, "approved": approved,
                "action_hash": (approval.get("request") or {}).get(
                    "action_hash"),
            })
        return self._respond(data, {
            "status": status, "delivered": bool(delivered),
        })

    async def _answer(self, data, user, approvals, approval, payload):
        """A question's answer — a choice the agent offered or the
        person's own words: recorded, then carried to the runtime as the
        door's question_answered frame. A files question is answered
        with the refs the person chose — none is a decline, not an
        error — and the frame carries the files by name and type."""
        if (approval.get("request") or {}).get("expects") == "files":
            return await self._answer_files(
                data, user, approvals, approval, payload)
        if (approval.get("request") or {}).get("expects") == "credential":
            return await self._answer_credential(
                data, user, approvals, approval, payload)
        if (approval.get("request") or {}).get("expects") == "code":
            return await self._answer_code(
                data, user, approvals, approval, payload)
        answer = str(payload.get("answer") or "").strip()
        if not answer or len(answer) > ANSWER_MAX_CHARS:
            return self._fail(
                data, "invalid_answer",
                f"An answer needs 1 to {ANSWER_MAX_CHARS} characters.")
        if approval["status"] != approvals.PENDING:
            return self._fail(
                data, "not_pending",
                f"The question is already {approval['status']}.", 409)
        answer_label = ""
        if (approval.get("request") or {}).get("expects") == "file":
            # The answer is a file the person may see — attached to this
            # chat, or theirs already. Anything else is not an answer.
            from database.stores.data.files import FileStore
            record = FileStore().visible_record(user, answer)
            if record is None:
                return self._fail(
                    data, "invalid_answer",
                    "The answer to this question is a file: attach one.")
            answer_label = str(
                (record.get("values") or {}).get("filename")
                or (record.get("keys") or {}).get("filename") or "a file")
        if not approvals.decide(approval["approval_id"], {
            "status": approvals.ANSWERED,
            "decision": "answer",
            "answer": answer,
            **({"answer_label": answer_label} if answer_label else {}),
            "resolved_at": utc_now(),
            "resolved_by": str(user.get("email") or ""),
        }):
            return self._fail(
                data, "not_pending", "The question is already closed.", 409)

        AuditStore().append(
            "approval.resolved", user, chat_id=approval["chat_id"],
            function=(approval.get("request") or {}).get("function"),
            resource_refs=[approval["approval_id"]],
            details={"decision": "answer", "kind": "question"},
        )
        delivered = await get_runtime_clients().send(
            approval["chat_id"], user, {
                "event": "question_answered",
                "approval_id": approval["approval_id"], "answer": answer,
            })
        return self._respond(data, {
            "status": approvals.ANSWERED, "delivered": bool(delivered),
        })

    async def _answer_files(self, data, user, approvals, approval, payload):
        """The person chose files — or none. Every ref must be a file
        they may see; the answer recorded is the refs, the label their
        names, and the frame carries what the runtime needs to attach
        them: ref, name, type, size."""
        from database.stores.data.files import FileStore

        raw = payload.get("answer")
        if raw is None or raw == "":
            raw = []
        elif isinstance(raw, str):
            raw = [part.strip() for part in raw.split(",") if part.strip()]
        if (not isinstance(raw, list) or len(raw) > FILES_CHOSEN_MAX
                or not all(isinstance(ref, str) and ref for ref in raw)):
            return self._fail(
                data, "invalid_answer",
                f"The answer to this question is up to {FILES_CHOSEN_MAX} "
                f"files you can see, or none.")
        if approval["status"] != approvals.PENDING:
            return self._fail(
                data, "not_pending",
                f"The question is already {approval['status']}.", 409)
        store = FileStore()
        chosen, seen = [], set()
        for ref in raw:
            if ref in seen:
                continue
            seen.add(ref)
            record = store.visible_record(user, ref)
            if record is None:
                return self._fail(
                    data, "invalid_answer",
                    "The answer to this question is files you can see.")
            values = record.get("values") or {}
            chosen.append({
                "resource_ref": ref,
                "filename": str(values.get("filename") or "a file"),
                "file_type": str(values.get("file_type") or ""),
                "file_size": int(values.get("file_size") or 0),
            })
        refs = [item["resource_ref"] for item in chosen]
        label = ", ".join(item["filename"] for item in chosen) or "No files"
        if not approvals.decide(approval["approval_id"], {
            "status": approvals.ANSWERED,
            "decision": "answer",
            "answer": refs,
            "answer_label": label,
            "resolved_at": utc_now(),
            "resolved_by": str(user.get("email") or ""),
        }):
            return self._fail(
                data, "not_pending", "The question is already closed.", 409)

        AuditStore().append(
            "approval.resolved", user, chat_id=approval["chat_id"],
            function=(approval.get("request") or {}).get("function"),
            resource_refs=[approval["approval_id"], *refs],
            details={"decision": "answer", "kind": "question",
                     "files": len(refs)},
        )
        delivered = await get_runtime_clients().send(
            approval["chat_id"], user, {
                "event": "question_answered",
                "approval_id": approval["approval_id"], "answer": chosen,
            })
        return self._respond(data, {
            "status": approvals.ANSWERED, "delivered": bool(delivered),
        })

    async def _answer_code(self, data, user, approvals, approval, payload):
        """Code an agent wants to run, decided: allow or deny and
        nothing else. The record keeps the decision beside the code it
        was made about, and the frame carries the word."""
        answer = str(payload.get("answer") or "").strip().lower()
        if answer not in ("allow", "deny"):
            return self._fail(
                data, "invalid_answer",
                "A code card is answered with allow or deny.")
        if approval["status"] != approvals.PENDING:
            return self._fail(
                data, "not_pending",
                f"The card is already {approval['status']}.", 409)
        if not approvals.decide(approval["approval_id"], {
            "status": approvals.ANSWERED,
            "decision": "answer",
            "answer": answer,
            "answer_label": "Allowed" if answer == "allow" else "Declined",
            "resolved_at": utc_now(),
            "resolved_by": str(user.get("email") or ""),
        }):
            return self._fail(
                data, "not_pending", "The card is already closed.", 409)
        AuditStore().append(
            "approval.resolved", user, chat_id=approval["chat_id"],
            function=(approval.get("request") or {}).get("function"),
            resource_refs=[approval["approval_id"]],
            details={"decision": "answer", "kind": "question",
                     "code": answer},
        )
        delivered = await get_runtime_clients().send(
            approval["chat_id"], user, {
                "event": "question_answered",
                "approval_id": approval["approval_id"], "answer": answer,
            })
        return self._respond(data, {
            "status": approvals.ANSWERED, "delivered": bool(delivered),
        })

    async def _answer_credential(self, data, user, approvals, approval, payload):
        """A credential card answered here rather than at the vault's
        doors: a decline or an update request on a consent card, the
        chosen row on a choose card, or the values of fields that are
        asked every time — which ride the frame to the runtime and are
        recorded nowhere. The entry card's values and the consent card's
        yes go to Secrets:Credential:Save and :Allow, which close the
        card themselves."""
        credential = (approval.get("request") or {}).get("credential") or {}
        mode = str(credential.get("mode") or "")
        answer = payload.get("answer")
        if approval["status"] != approvals.PENDING:
            return self._fail(
                data, "not_pending",
                f"The card is already {approval['status']}.", 409)
        recorded, label, carried = "", "", answer
        if mode == "consent":
            answer = str(answer or "").strip().lower()
            if answer not in ("deny", "update"):
                return self._fail(
                    data, "invalid_answer",
                    "A consent card is answered with deny or update here; "
                    "allow goes to Secrets:Credential:Allow.")
            recorded, carried = answer, answer
            label = "Declined" if answer == "deny" else "Update requested"
        elif mode == "choose":
            answer = str(answer or "").strip()
            offered = {i.get("resource_ref"): i for i in credential.get("instances") or []}
            if answer not in offered:
                return self._fail(
                    data, "invalid_answer",
                    "The answer to this card is one of the logins offered.")
            recorded, carried = answer, answer
            label = str(offered[answer].get("name") or offered[answer].get("account") or "Chosen")
        elif mode == "once":
            if not isinstance(answer, dict):
                return self._fail(
                    data, "invalid_answer",
                    "The answer to this card is the fields it asked for.")
            wanted = [f for f in credential.get("fields") or [] if not f.get("remember", True)]
            values = {f["name"]: str(answer.get(f["name"]) or "") for f in wanted}
            missing = [f.get("label") or f["name"] for f in wanted
                       if f.get("required", True) and not values[f["name"]].strip()]
            if missing:
                return self._fail(
                    data, "invalid_answer", f"Required: {', '.join(missing)}.")
            # Never recorded: the card says it was provided, the frame
            # carries it once, and nothing keeps it.
            recorded, label, carried = "provided", "Provided", values
        else:
            return self._fail(
                data, "invalid_answer",
                "This card is answered at Secrets:Credential:Save.")
        if not approvals.decide(approval["approval_id"], {
            "status": approvals.ANSWERED,
            "decision": "answer",
            "answer": recorded,
            "answer_label": label,
            "resolved_at": utc_now(),
            "resolved_by": str(user.get("email") or ""),
        }):
            return self._fail(
                data, "not_pending", "The card is already closed.", 409)
        AuditStore().append(
            "approval.resolved", user, chat_id=approval["chat_id"],
            function=(approval.get("request") or {}).get("function"),
            resource_refs=[approval["approval_id"]],
            details={"decision": "answer", "kind": "question",
                     "credential": mode},
        )
        delivered = await get_runtime_clients().send(
            approval["chat_id"], user, {
                "event": "question_answered",
                "approval_id": approval["approval_id"], "answer": carried,
            })
        return self._respond(data, {
            "status": approvals.ANSWERED, "delivered": bool(delivered),
        })

    def expire(self, data, user):
        """A question nobody answered in time, closed by the runtime that
        asked it — runtime-only, its own chat's cards, pending ones only,
        so a late answer is refused rather than delivered into nothing."""
        chat, refusal = self._runtime_chat(data, user, "expire questions")
        if refusal is not None:
            return refusal
        approval_id = str(self._payload(data).get("approval_id") or "")
        approvals = ApprovalStore()
        if approvals.in_chat(approval_id, chat["chat_id"]) is None:
            return self._fail(data, "not_found", "Approval not found.", 404)
        expired = approvals.decide(approval_id, {
            "status": approvals.EXPIRED, "resolved_at": utc_now(),
        })
        return self._respond(data, {"expired": bool(expired)})
