"""What of a result the model is shown, and what the trace keeps.

A function can return more than one beat should read: a table of
four hundred rows, a document. The whole of it lives in storage under
its ``storage_ref``; the model sees a preview that fits a beat and a
note saying how much there is and where the rest can be read (the
``read`` action pages it). The trace, saved with the mind every beat,
keeps a cut copy the same way.

Everything here is a function of the value alone: no state, no model,
no chat. The assistant (assistant.py, ``_record``, ``fitted``,
``_read``) decides when to call it; this decides what the result
looks like once it has.
"""

import json
from typing import Any, Dict, Optional

#: How much of a result the model sees whole. Roughly 4,000 tokens —
#: small against any current context, and the difference between
#: one beat and one beat per row: a table the model cannot see
#: whole is one it reads back item by item, a model call each.
OBSERVATION_MAX_CHARS = 16000
#: When a result is previewed instead, lists are shown as many
#: complete items as fit in this, strings clipped at the value cap.
PREVIEW_MAX_CHARS = 12000
PREVIEW_VALUE_CHARS = 1000
#: How much of a result the TRACE keeps. The whole result lives in
#: storage under its storage_ref; the trace is the mind's own state,
#: persisted every beat and capped by the platform, and one large
#: read must not be what makes it unsaveable.
TRACE_RESULT_MAX_CHARS = 4000
#: How a call is said in one watchable line (``spoken``,
#: ``inputs_summary``): the whole line, and each value in it.
SUMMARY_MAX_CHARS = 90
VALUE_MAX_CHARS = 44


def previewed(status: str, result: Any) -> Dict[str, Any]:
    """A result too large to show whole, as the model is shown it:
    its preview, the reference to the whole, and the note that
    says how much there is and where the rest can be read."""
    shown = preview(result)
    return {
        "status": status,
        "storage_ref": result.get("storage_ref")
        if isinstance(result, dict) else None,
        # What the call offered to show survives the preview: it
        # is how the model can put the rows in front of a person.
        "displays": result.get("displays")
        if isinstance(result, dict) else None,
        "result_preview": shown,
        "truncated": True,
        "note": preview_note(
            shown, stored=isinstance(result, dict)
            and isinstance(result.get("storage_ref"), str)),
    }

def preview_note(preview: Any, stored: bool = True) -> str:
    """What to tell the model when a result was too big to show whole.

    A note that says only that the result was previewed leaves the
    model with nothing concrete and a strong urge to explain itself:
    people are told "the query is big", as though they had asked for
    too much. They had not — the FUNCTION returned more than fits.
    So the note counts what was cut, the way the read action's note
    does, and the counts are the thing worth repeating to a
    person."""
    counts = []
    if isinstance(preview, dict):
        for field, value in preview.items():
            if isinstance(value, dict) and "items_total" in value:
                counts.append(f"{field}: {value.get('items_shown')} of "
                              f"{value.get('items_total')}")
    head = ("Preview only — " + "; ".join(counts) + ". ") if counts \
        else "Preview only. "
    # Where the whole can be read is said only where it was kept:
    # a failed call's result, or one the store would not take, has
    # no ref to read it by.
    where = ("The whole result is stored. Use the read action with "
             "this storage_ref and a path to see any part of it. "
             if stored else
             "The whole result was not kept, so this is all of it "
             "that can be seen. ")
    return head + where + (
        "If you tell the "
        "person anything about this, say what you are showing and how "
        "much there is — never that their request was too large, which "
        "it was not.")

def bounded(result: Any) -> Any:
    """The trace's copy of a result: whole when it fits, else its
    shape — each list cut to the items that fit, long strings
    clipped — marked ``truncated`` beside the storage_ref that holds
    the whole. Lists stay lists, so evidence still sees a read with
    rows and the reference to render them from."""
    if not isinstance(result, dict) or len(
            json.dumps(result, default=str)) <= TRACE_RESULT_MAX_CHARS:
        return result
    # What the platform added to the result is not the agent's
    # output to cut: the displays the call offered are what a later
    # show is checked against, every one of them.
    displays = result.get("displays")
    bounded = preview(
        {key: value for key, value in result.items() if key != "displays"},
        TRACE_RESULT_MAX_CHARS)
    for field, value in list(bounded.items()):
        if isinstance(value, dict) and "items_total" in value:
            bounded[field] = value["items"]
    if displays is not None:
        bounded["displays"] = displays
    bounded["truncated"] = True
    return bounded

def fit_list(items: list, budget: int) -> Dict[str, Any]:
    """As many complete items as fit in ``budget`` characters, and
    how many there were: whole rows, never a clipped middle."""
    kept: list = []
    used = 2
    for item in items:
        piece = len(json.dumps(item, default=str)) + 2
        if used + piece > budget:
            if not kept:
                # The first item is itself more than the budget: it
                # is shown as its own preview, and never whole —
                # one row can be a document.
                kept.append(preview(item, max(budget - 2, 200)))
            break
        kept.append(item)
        used += piece
    return {"items_total": len(items), "items_shown": len(kept),
            "items": kept}

def preview(value: Any, budget: Optional[int] = None) -> Any:
    """A result too large to show whole, by its shape: every field,
    each list as the first N complete items that fit, each long
    string clipped. The whole preview fits ``budget``, which is
    PREVIEW_MAX_CHARS unless another is given."""
    budget = budget or PREVIEW_MAX_CHARS

    def clip(text: str, cap: int) -> str:
        return text if len(text) <= cap else text[: cap - 1] + "…"

    if isinstance(value, list):
        return fit_list(value, budget)
    if isinstance(value, str):
        return clip(value, budget)
    if not isinstance(value, dict):
        return value

    # What is not a list first, each value clipped — to less than
    # the usual length where there are many, so that together they
    # take at most half of the budget. Then the lists share what is
    # left, measured, and not a guess that is halved until it fits:
    # that would leave a result of many short rows with a handful
    # shown.
    lists = [f for f, v in value.items() if isinstance(v, list)]
    others = [f for f in value if f not in lists]
    cap = PREVIEW_VALUE_CHARS
    if others:
        cap = min(cap, max(24, (budget // 2) // len(others) - 24))
    preview: Dict[str, Any] = {}
    for field in others:
        item = value[field]
        if isinstance(item, dict):
            preview[field] = clip(json.dumps(item, default=str), cap)
        elif isinstance(item, str):
            preview[field] = clip(item, cap)
        else:
            preview[field] = item
    taken = len(json.dumps(preview, default=str))
    # Each list's own wrapping (its name, its two counts) is about
    # eighty characters.
    left = budget - taken - 80 * len(lists)
    share = max(left // max(1, len(lists)), 200)
    fitted = {field: fit_list(value[field], share) for field in lists}
    # In the order the result had them.
    preview = {field: fitted[field] if field in fitted else preview[field]
               for field in value}
    if len(json.dumps(preview, default=str)) > budget:
        # A shape nothing above bounds (a great many fields): said
        # as text, cut to the budget.
        # (to two thirds of it: said as text inside JSON, every
        # quote in it is written as two characters).
        return {"clipped": clip(json.dumps(preview, default=str),
                                max((budget * 2) // 3, 16))}
    return preview

def spoken(agent, function: str) -> str:
    """A call as a person would say it: the agent's name and the
    function's label, never the minted ref the platform routes by.
    'Outlook · Find Messages', with the canonical name kept only
    when the manifest gives no label."""
    manifest = getattr(agent, "manifest", None)
    agent_name = str(getattr(manifest, "name", "") or getattr(agent, "agent_id", "") or "")
    label = ""
    try:
        found = manifest.function(function) if manifest is not None else None
        if found:
            label = str((found[1] or {}).get("name") or "")
    except Exception:  # noqa: BLE001 — narration must never break a call
        label = ""
    if not label:
        parts = function.split(".")
        label = ".".join(parts[1:]) if len(parts) == 3 else function
    return f"{agent_name} · {label}" if agent_name else label

def inputs_summary(inputs: Dict[str, Any]) -> str:
    """What this call was actually asked to do, in one watchable
    line."""
    if not isinstance(inputs, dict) or not inputs:
        return ""
    parts = []
    for key, value in inputs.items():
        parts.append(f"{key}={value_summary(value)}")
        if sum(len(part) for part in parts) > SUMMARY_MAX_CHARS:
            break
    summary = ", ".join(parts)
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return f" · {summary}"

def value_summary(value: Any) -> str:
    if isinstance(value, dict):
        if isinstance(value.get("storage_ref"), str):
            return "«stored result»"
        return "{…}"
    if isinstance(value, list):
        return f"[{len(value)}]"
    if isinstance(value, bool) or value is None:
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if len(text) > VALUE_MAX_CHARS:
        text = text[: VALUE_MAX_CHARS - 1].rstrip() + "…"
    return f'"{text}"' if " " in text else text
