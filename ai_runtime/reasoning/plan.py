"""The work in hand — items the user sees, alive across beats, and
what the trace can prove about each.

A plan used to be an announcement that nothing referred to again; then
it was state with a status per step, marked by the model's word alone.
Now each item carries what proves it: while an item is active, every
successful invocation's storage ref and every finished job's id land on
it, and a model may name evidence itself — only refs the trace holds.
An item marked done with nothing behind it stays visible as unverified,
never refused: a step like "answer the question" has no trace. What
IS refused is a finish that claims completion while items are owed,
evidence the trace never produced, a block with no reason, and a start
on an item whose dependencies are not done. The rules live here, in
code; the prompt only describes them.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from contracts.chat import PLAN_MAX_STEPS, PLAN_STEP_MAX_CHARS

PENDING = "pending"
ACTIVE = "active"
DONE = "done"
BLOCKED = "blocked"

STATUSES = {PENDING, ACTIVE, DONE, BLOCKED}


class WorkItem:
    """One obligation: its words, where it stands, and what proves it."""

    def __init__(self, item_id: str, text: str, status: str = PENDING,
                 evidence: Optional[Iterable[str]] = None, blocker: str = "",
                 depends_on: Optional[Iterable[str]] = None):
        self.id = item_id
        self.text = text
        self.status = status if status in STATUSES else PENDING
        self.evidence: List[str] = list(dict.fromkeys(
            str(ref) for ref in (evidence or []) if str(ref)))
        self.blocker = str(blocker or "")
        self.depends_on: List[str] = list(dict.fromkeys(
            str(ref) for ref in (depends_on or []) if str(ref)))

    @property
    def verified(self) -> bool:
        return bool(self.evidence)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "text": self.text, "status": self.status,
            "evidence": list(self.evidence), "blocker": self.blocker,
            "depends_on": list(self.depends_on), "verified": self.verified,
        }


class Plan:
    """The items of the work in hand, and the rules they answer to."""

    #: The chat's own bounds (contracts/chat.py): the backend keeps no
    #: plan longer, so the runtime holds none longer either.
    MAX_STEPS = PLAN_MAX_STEPS
    STEP_MAX_CHARS = PLAN_STEP_MAX_CHARS

    def __init__(self, steps: Optional[List[Dict[str, Any]]] = None):
        self.items: List[WorkItem] = []
        self._next = 1
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            text = str(step.get("text") or "").strip()[: self.STEP_MAX_CHARS]
            if not text or len(self.items) >= self.MAX_STEPS:
                continue
            self.items.append(WorkItem(
                self._id(step.get("id")), text, str(step.get("status") or ""),
                step.get("evidence"), str(step.get("blocker") or ""),
                step.get("depends_on"),
            ))

    def _id(self, wanted: Any = None) -> str:
        """The item's id: the one it was saved with, else the next free
        one. Ids are the runtime's — a plan saved before items had ids
        gets them on load, in order."""
        wanted = str(wanted or "")
        taken = {item.id for item in self.items}
        if wanted and wanted not in taken:
            if wanted[0] == "w" and wanted[1:].isdigit():
                self._next = max(self._next, int(wanted[1:]) + 1)
            return wanted
        while f"w{self._next}" in taken:
            self._next += 1
        self._next += 1
        return f"w{self._next - 1}"

    # ------------------------------------------------------------------
    def __bool__(self) -> bool:
        return bool(self.items)

    def to_steps(self) -> List[Dict[str, Any]]:
        return [item.to_dict() for item in self.items]

    def get(self, ref: Any) -> Optional[WorkItem]:
        """By id (``w3``) or by 1-based position, as the model names
        them."""
        if isinstance(ref, int) and not isinstance(ref, bool):
            return self.items[ref - 1] if 1 <= ref <= len(self.items) else None
        ref = str(ref or "")
        return next((item for item in self.items if item.id == ref), None)

    # ------------------------------------------------------------------
    def replace(self, texts: List[Any]) -> None:
        """A new plan, keeping what is already true.

        A revision is usually the same work seen more clearly, so an
        item whose wording is unchanged keeps its id, its status, its
        evidence and its blocker. Rewriting an item makes it new, which
        is the honest reading: if the words changed, the thing done
        under the old words is not obviously this."""
        # By their words, each in turn: two items that say the same
        # thing are two items, and both keep what they had.
        kept: Dict[str, List[WorkItem]] = {}
        for item in self.items:
            kept.setdefault(item.text, []).append(item)
        self.items = []
        for text in (str(t).strip()[: self.STEP_MAX_CHARS] for t in texts):
            if not text or len(self.items) >= self.MAX_STEPS:
                continue
            previous = kept[text].pop(0) if kept.get(text) else None
            if previous is not None:
                self.items.append(previous)
            else:
                self.items.append(WorkItem(self._id(), text))
        ids = {item.id for item in self.items}
        for item in self.items:
            item.depends_on = [d for d in item.depends_on if d in ids]

    def update(self, ref: Any, status: str = "", evidence: Any = None,
               blocker: str = "", depends_on: Any = None,
               proven: Optional[Set[str]] = None) -> Tuple[bool, str]:
        """Mark one item. Returns (ok, why not).

        ``proven`` is what the trace can vouch for — storage refs of
        successful invocations, ids of finished jobs. Evidence the
        model names must be in it; evidence it never names is not
        required, only shown as missing."""
        item = self.get(ref)
        if item is None:
            return False, (f"No item '{ref}'. Items are "
                           f"{', '.join(i.id for i in self.items) or 'none'}.")
        if status and status not in STATUSES:
            return False, ("status must be one of pending, active, done, "
                           "blocked.")

        named = [str(r) for r in (evidence or []) if str(r)] \
            if isinstance(evidence, list) else []
        unknown = [r for r in named if r not in (proven or set())]
        if unknown:
            return False, (f"Evidence {', '.join(unknown)} is not in this "
                           f"chat's trace. Name a storage_ref of a "
                           f"successful call or a finished job's id, or "
                           f"mark the item done without evidence.")

        # Worked out here and written at the end: an update that is
        # refused below has changed nothing.
        depends = list(item.depends_on)
        if depends_on is not None:
            wanted = [str(d) for d in (depends_on or []) if str(d)] \
                if isinstance(depends_on, list) else []
            missing = [d for d in wanted if self.get(d) is None or d == item.id]
            if missing:
                return False, f"No such item to depend on: {', '.join(missing)}."
            depends = list(dict.fromkeys(wanted))

        if status == BLOCKED and not (blocker or item.blocker):
            return False, "A blocked item needs a blocker: say what stops it."
        if status == ACTIVE:
            waiting = [d for d in depends
                       if (self.get(d) or WorkItem(d, d)).status != DONE]
            if waiting:
                return False, (f"{item.id} waits on {', '.join(waiting)}, "
                               f"not done yet.")
        item.depends_on = depends

        for ref_ in named:
            if ref_ not in item.evidence:
                item.evidence.append(ref_)
        if blocker:
            item.blocker = str(blocker)
        if status:
            item.status = status
            if status != BLOCKED:
                item.blocker = ""
        return True, ""

    def attach(self, refs: Iterable[str]) -> None:
        """What the trace just proved lands on every active item."""
        refs = [str(r) for r in refs if str(r)]
        for item in self.items:
            if item.status == ACTIVE:
                for ref in refs:
                    if ref not in item.evidence:
                        item.evidence.append(ref)

    # ------------------------------------------------------------------
    def outstanding(self) -> List[WorkItem]:
        return [i for i in self.items if i.status in (PENDING, ACTIVE)]

    def blocked(self) -> List[WorkItem]:
        return [i for i in self.items if i.status == BLOCKED]

    def finished(self) -> bool:
        return bool(self.items) and not self.outstanding()

    def all_done(self) -> bool:
        """Every item done — nothing owed, nothing blocked. A blocked
        item is not finished work: it is usually what the person is
        about to answer."""
        return bool(self.items) and all(i.status == DONE for i in self.items)

    # ------------------------------------------------------------------
    def render(self, instruction: bool = True) -> str:
        """The block the model reads every beat: each item by id, where
        it stands, what proves it, what blocks it, what it waits on."""
        if not self.items:
            return "(no plan yet)"

        lines = []
        for item in self.items:
            if item.status == DONE:
                label = (f"done, {len(item.evidence)} evidence"
                         if item.evidence else "done, UNVERIFIED")
            elif item.status == BLOCKED:
                label = f"BLOCKED: {item.blocker}" if item.blocker else "BLOCKED"
            elif item.status == ACTIVE:
                label = "in progress"
                if item.evidence:
                    label += f", {len(item.evidence)} evidence"
            else:
                label = "still to do"
            line = f"{item.id} [{label}] {item.text}"
            if item.depends_on:
                line += f" (after {', '.join(item.depends_on)})"
            lines.append(line)

        owed = len(self.outstanding())
        if owed and instruction:
            lines.append(
                f"\n{owed} item(s) still to do. Continue the plan — a "
                f"finish claiming completion will be refused while items "
                f"are owed. If an item cannot be done, mark it blocked and "
                f"say why."
            )
        return "\n".join(lines)
