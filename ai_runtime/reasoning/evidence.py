"""Evidence: what the trace can prove about a message.

The assistant reasons; this module vouches — quietly. Model prose is
not evidence, the executor trace is, and this module decides what may
appear AS DATA beside the model's words. The words themselves travel
as written; the runtime never speaks over the model, and never twice.

Three rules, each learned from a conversation that read badly:

- **A table is shown when the model shows it, and only when the trace
  proves it.** It is either a display a call offered (``call.show`` —
  a table or a chart the agent built, kept with the call's result) or ``say`` names what to show — a stored result of a call
  made in this conversation, the field holding its rows, a title, the
  columns worth seeing — and the runtime attaches it only when that
  result is a successful call the trace holds and the field is a
  declared list with rows. Nothing is attached on a rule of its own: a
  read is usually the means to a sentence, and a table under every
  reply read as noise. What is refused is said back to the model,
  never to the user.
- **A write is recorded, not announced.** Every successful write rides
  the message as a ``success`` part — machine state the page keeps but
  does not render — so an audit can see what was verified without the
  chat gaining a second voice.
- **The runtime speaks only when the model said nothing.** One honest
  line for a silent model, never a chorus for a talking one.

Everything derives from the manifest and successful executor results,
never from prose. Trace entries carry ``agent`` (the agent's id); the
manifest lookup goes through the agents map, since one trace may span
several agents.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Tuple

from contracts.chat import agent_source


class Evidence:

    #: Tables one message may carry; a reply is not a report.
    SHOW_MAX = 3
    #: Columns a shown table may name.
    COLUMNS_MAX = 24

    # ------------------------------------------------------------------
    # Derivations from the trace
    # ------------------------------------------------------------------

    @staticmethod
    def _successes(
        agents: Dict[str, Any], trace: List[Dict[str, Any]],
    ) -> Iterator[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]]:
        """(result, declared function, source) for every successful entry
        whose function the manifest knows — the only entries that can
        prove anything. The source is the agent that produced it, and it
        rides every part built from the entry."""
        for entry in trace:
            result = entry.get("result")
            if entry.get("status") != "success" or not isinstance(result, dict):
                continue
            agent = agents.get(str(entry.get("agent") or ""))
            if agent is None:
                continue
            declared = agent.manifest.function(
                agent.declared(str(entry.get("function") or "")))
            if declared:
                yield result, declared[1], agent_source(
                    agent.agent_id, agent.manifest.name,
                    str(entry.get("function") or ""))

    @staticmethod
    def _outputs(function: Dict[str, Any]) -> Dict[str, Any]:
        return (function.get("outputs") or {}).get("properties") or {}

    @classmethod
    def _lists(cls, function: Dict[str, Any]) -> List[str]:
        """The declared output fields that hold rows."""
        return [field for field, schema in cls._outputs(function).items()
                if isinstance(schema, dict) and schema.get("type") == "array"]

    @classmethod
    def writes(cls, agents: Dict[str, Any],
               trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Successful writes (permission level above zero), each one a
        ``success`` part for the message."""
        parts = []
        for result, function, source in cls._successes(agents, trace):
            if function.get("permission_level") == 0:
                continue
            part = {"type": "success",
                    "text": f"Verified: {function.get('name') or 'action'}",
                    "source": source}
            if isinstance(result.get("storage_ref"), str):
                part["storage_ref"] = result["storage_ref"]
            parts.append(part)
        return parts

    @classmethod
    def reads(cls, agents: Dict[str, Any],
              trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Successful reads whose declared outputs are lists: one entry
        per list field, in trace order, each with its row count. What
        the runtime knows when a silent model leaves it to speak."""
        found = []
        for entry in trace:
            # A large result is kept cut; its counts were taken from
            # the whole one when it was recorded.
            counts = entry.get("counts") if isinstance(
                entry.get("counts"), dict) else {}
            for result, function, _ in cls._successes(agents, [entry]):
                if function.get("permission_level") != 0:
                    continue
                storage_ref = result.get("storage_ref")
                if not isinstance(storage_ref, str):
                    continue
                for field in cls._lists(function):
                    rows = result.get(field)
                    if isinstance(rows, list):
                        found.append({
                            "storage_ref": storage_ref, "path": field,
                            "count": int(counts.get(field, len(rows)))})
        return found

    @classmethod
    def files(cls, agents: Dict[str, Any],
              trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Files the work created, by the manifest's x-resource
        declarations, once each.

        The name travels with the ref. Every file-producing function in
        the catalog returns ``filename`` beside its file field, and
        without it the page had nothing to call these but "File" — six
        downloads in a row all read "File · FILE", which is no more
        useful than six blank rows."""
        parts: List[Dict[str, Any]] = []
        seen = set()
        for entry in trace:
            kept = entry.get("files")
            if isinstance(kept, list) and entry.get("status") == "success":
                # A large result is kept cut; its files were read off
                # the whole one when it was recorded.
                for part in kept:
                    ref = part.get("resource_ref") if isinstance(part, dict) else None
                    if isinstance(ref, str) and ref not in seen:
                        seen.add(ref)
                        parts.append(dict(part))
                continue
            for result, function, source in cls._successes(agents, [entry]):
                for row, properties in cls._file_rows(function, result):
                    for field, schema in properties.items():
                        resource = schema.get("x-resource") if isinstance(schema, dict) else None
                        ref = row.get(field)
                        if not resource or resource.get("type") != "file" \
                                or not isinstance(ref, str) or ref in seen:
                            continue
                        seen.add(ref)
                        part = {"type": "file", "resource_ref": ref,
                                "text": str(function.get("name") or field),
                                "source": source}
                        filename = row.get("filename")
                        if isinstance(filename, str) and filename.strip():
                            part["filename"] = filename.strip()
                        parts.append(part)
        return parts

    @classmethod
    def _file_rows(cls, function: Dict[str, Any],
                   result: Dict[str, Any]) -> Iterator[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """(a row, its declared fields) for everywhere a result may
        hold a file: the result itself, and each row of a declared list
        of objects — a function that makes several files returns them
        as one, each with its own ``filename``."""
        outputs = cls._outputs(function)
        yield result, outputs
        for field, schema in outputs.items():
            items = schema.get("items") if isinstance(schema, dict) else None
            properties = items.get("properties") if isinstance(items, dict) else None
            rows = result.get(field)
            if isinstance(properties, dict) and isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        yield row, properties

    # ------------------------------------------------------------------
    # What the model shows, and what the trace can vouch for
    # ------------------------------------------------------------------

    @classmethod
    def shown(cls, agents: Dict[str, Any], trace: List[Dict[str, Any]],
              show: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
        """The table parts a ``show`` asks for, each proved against the
        trace, and the reasons for each one that was not. A shown
        result must be a successful call this conversation made; its
        path must be a declared list field with rows. Title and
        columns are the model's — how the rows are presented is a
        judgement, what they are is not."""
        parts: List[Dict[str, Any]] = []
        refused: List[str] = []
        if show is None:
            return parts, refused
        if not isinstance(show, list):
            return parts, ["show must be a list of {display, title?} or "
                           "{storage_ref, path?, title?, columns?}."]

        by_ref: Dict[str, Tuple[Dict[str, Any], Dict[str, Any],
                                Dict[str, Any]]] = {}
        for result, function, source in cls._successes(agents, trace):
            ref = result.get("storage_ref")
            if isinstance(ref, str):
                by_ref[ref] = (result, function, source)

        by_display: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]] = {}
        for result, _, source in cls._successes(agents, trace):
            for display in result.get("displays") or []:
                if isinstance(display, dict) and isinstance(
                        display.get("display_id"), str):
                    by_display[display["display_id"]] = (display, source)

        for item in show[:cls.SHOW_MAX]:
            if not isinstance(item, dict):
                refused.append("Each show entry is an object with a display "
                               "or a storage_ref.")
                continue
            if item.get("display"):
                part, why = cls._display_part(item, by_display)
                if part is None:
                    refused.append(why)
                else:
                    parts.append(part)
                continue
            ref = str(item.get("storage_ref") or "")
            found = by_ref.get(ref)
            if found is None:
                refused.append(f"'{ref}' is not a stored result of a successful "
                               "call in this conversation.")
                continue
            result, function, source = found
            name = str(function.get("name") or "the call")
            lists = cls._lists(function)
            path = str(item.get("path") or "")
            if not path:
                if len(lists) == 1:
                    path = lists[0]
                elif not lists:
                    refused.append(f"'{ref}' ({name}) holds no list to show.")
                    continue
                else:
                    refused.append(f"'{ref}' ({name}) holds several lists — "
                                   f"name the path: {', '.join(lists)}.")
                    continue
            elif path not in lists:
                refused.append(f"'{path}' is not a list field of {name}"
                               + (f"; its lists: {', '.join(lists)}." if lists
                                  else "; it holds no list."))
                continue
            rows = result.get(path)
            if not isinstance(rows, list) or not rows:
                refused.append(f"'{ref}' has no rows in '{path}' — nothing to show.")
                continue
            part = {"type": "table",
                    "text": str(item.get("title") or name)[:120],
                    "storage_ref": ref, "path": path, "source": source}
            columns = item.get("columns")
            if isinstance(columns, list):
                cleaned = list(dict.fromkeys(
                    str(c).strip() for c in columns if str(c).strip()))
                if cleaned:
                    part["columns"] = cleaned[:cls.COLUMNS_MAX]
            parts.append(part)
        if len(show) > cls.SHOW_MAX:
            refused.append(f"At most {cls.SHOW_MAX} tables in one message; "
                           "the rest were left out.")
        return parts, refused

    @classmethod
    def _display_part(cls, item: Dict[str, Any],
                      by_display: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]]
                      ) -> Tuple[Optional[Dict[str, Any]], str]:
        """A display a successful call offered, as the model presents
        it: the title (and a table's columns) are the model's to choose;
        the rows and the chart are the agent's, as the trace kept them."""
        display_id = str(item.get("display") or "")
        found = by_display.get(display_id)
        if found is None:
            return None, (f"'{display_id}' is not a display offered by a "
                          "successful call in this conversation.")
        display, source = found
        chart = display.get("kind") == "chart"
        part: Dict[str, Any] = {
            "type": "graph" if chart else "table",
            "text": str(item.get("title") or display.get("title")
                        or ("Chart" if chart else "Table"))[:120],
            "storage_ref": display_id, "source": source,
        }
        columns = item.get("columns")
        if not chart and isinstance(columns, list):
            cleaned = list(dict.fromkeys(
                str(c).strip() for c in columns if str(c).strip()))
            if cleaned:
                part["columns"] = cleaned[:cls.COLUMNS_MAX]
        return part, ""

    # ------------------------------------------------------------------
    # The message
    # ------------------------------------------------------------------

    @classmethod
    def compose(cls, text: str, agents: Dict[str, Any],
                trace: List[Dict[str, Any]], show: Any = None,
                history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """One message whose data-bearing parts are executor-grounded.

        ``trace`` is the work this message accounts for — the slice
        since the previous say: its writes and files ride the message.
        ``history`` is the whole conversation's trace, what a ``show``
        is checked against: the model may show a result it read before
        its last say. The prose is the model's and travels as written;
        ``refused`` names each show the trace could not vouch for, for
        the model to hear, not the user."""
        text = str(text or "").strip()
        writes = cls.writes(agents, trace)
        tables, refused = cls.shown(
            agents, trace if history is None else history, show)
        parts: List[Dict[str, Any]] = list(tables)
        parts.extend(writes)
        parts.extend(cls.files(agents, trace))
        return {"text": text or cls._silent(writes, cls.reads(agents, trace), trace),
                "parts": parts, "refused": refused}

    @staticmethod
    def _silent(writes: List[Dict[str, Any]], reads: List[Dict[str, Any]],
                trace: List[Dict[str, Any]]) -> str:
        """The one line the runtime speaks for a model that said
        nothing: what was verified, or an honest account of why not."""
        if writes:
            done = ", ".join(p["text"][len("Verified: "):] for p in writes)
            return f"Verified: {done}."
        if reads:
            count = sum(r["count"] for r in reads)
            if count == 0:
                return "The search ran and came back empty — nothing matches yet."
            return f"Found {count} result{'s' if count != 1 else ''}."
        if not trace:
            return ""
        error = next((str((e.get("result") or {}).get("error") or "").strip()
                      for e in trace if e.get("status") == "error"), "")
        return "No verified result came back" + \
            (f": {error.rstrip('.')}." if error else ".")
