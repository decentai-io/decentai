"""The chat's vocabulary, held to one definition (contracts/chat.py).

The runtime and the backend import the contract; the page's
chat-protocol.ts is written by hand against it. These tests keep the
three from drifting apart again: every event and part the contract
defines is one the page knows, and the page knows nothing the contract
does not account for.
"""

import re
from pathlib import Path

from contracts.chat import (
    EVENT_NAMES,
    PART_TYPES,
    RELAY_EVENTS,
    DELIVERED_EVENTS,
    RETIRED_EVENTS,
    DISPLAY_ROWS_MAX,
    agent_source,
    display_stored,
    event_error,
    part_error,
)

PROTOCOL_TS = (Path(__file__).resolve().parent.parent / "frontend" / "src"
               / "app" / "models" / "chat-protocol.ts")


def page_events():
    source = PROTOCOL_TS.read_text(encoding="utf-8")
    union = source.split("export type ChatEvent =", 1)[1].split(";\n", 1)[0]
    return set(re.findall(r"event: '([a-z_]+)'", union))


def page_part_types():
    source = PROTOCOL_TS.read_text(encoding="utf-8")
    names = set()
    for body in re.findall(r"export interface \w+Part \{([^}]*)\}", source):
        declared = re.search(r"type: ([^;]+);", body)
        if declared:
            names.update(re.findall(r"'([a-z_]+)'", declared.group(1)))
    return names


class TestThePageSpeaksTheContract:
    def test_every_event_is_one_the_page_knows(self):
        expected = (set(EVENT_NAMES) | set(RELAY_EVENTS) | set(DELIVERED_EVENTS)
                    | set(RETIRED_EVENTS))
        assert page_events() == expected

    def test_every_part_is_one_the_page_knows(self):
        assert page_part_types() == set(PART_TYPES)


class TestEvents:
    def test_the_work_as_it_happens_fits(self):
        assert event_error({
            "event": "activity", "kind": "call_finished",
            "text": "Notebook · Save Note", "status": "success",
            "duration_ms": 12, "child": "chat_1/sub_1",
            "source": agent_source("agt_1", "Notebook", "agt_1.note.save",
                                   call_id="c_1"),
        }) is None

    def test_the_retired_word_is_refused(self):
        assert event_error({"event": "progress", "description": "…"})

    def test_what_does_not_fit_says_where(self):
        problem = event_error({"event": "activity", "kind": "daydream",
                               "text": "x"})
        assert problem and "kind" in problem

    def test_a_frame_tells_of_the_tabs_behind_its_picture(self):
        frame = {"event": "screen_frame", "call_id": "c_1", "image_base64": "AA==",
                 "width": 1280, "height": 800, "frame": 7, "taken": True}
        assert event_error(frame) is None                       # none told: as before
        assert event_error({**frame, "tabs": [
            {"index": 1, "title": "Inbox", "address": "https://mail.example/", "active": True},
            {"index": 2, "title": "Terms"}]}) is None
        assert "index" in event_error({**frame, "tabs": [{"index": 0, "title": "x"}]})
        assert "tabs" in event_error({**frame, "tabs": [{"index": n} for n in range(1, 22)]})
        assert event_error({**frame, "tabs": [{"index": 1, "cookie": "x"}]})


class TestParts:
    def test_an_agents_table_fits_with_its_source(self):
        assert part_error({
            "type": "table", "storage_ref": "stg_1", "path": "rows",
            "text": "Open issues", "columns": ["key", "summary"],
            "source": agent_source("agt_1", "Jira", "agt_1.issues.search"),
        }) is None

    def test_a_field_nobody_defined_is_refused(self):
        assert part_error({"type": "markdown", "content": "Hi",
                           "colour": "green"})


class TestAgentSource:
    def test_empty_fields_are_left_out(self):
        assert agent_source("agt_1", "", "agt_1.note.save") == {
            "kind": "agent", "agent": "agt_1", "function": "agt_1.note.save"}


class TestDisplays:
    """What a call may offer to show, kept as the page already draws it."""

    def test_a_table_is_kept_with_its_columns(self):
        stored, problem = display_stored({
            "kind": "table", "title": "Notes",
            "rows": [{"title": "Ship", "priority": 2}, {"title": "Hire"}]})
        assert problem is None
        assert stored == {"rows": [{"title": "Ship", "priority": 2},
                                   {"title": "Hire"}],
                          "columns": ["title", "priority"]}

    def test_a_chart_is_kept_as_the_chart_viewer_reads_it(self):
        stored, problem = display_stored({
            "kind": "chart", "chart_type": "bar", "title": "By status",
            "labels": ["open", "done"],
            "series": [{"name": "Issues", "values": [3, 5]}]})
        assert problem is None
        assert stored == {"chartData": {
            "labels": ["open", "done"],
            "datasets": [{"label": "Issues", "data": [3.0, 5.0]}]},
            "chartType": "bar", "title": "By status"}

    def test_a_chart_needs_one_value_per_label(self):
        _, problem = display_stored({
            "kind": "chart", "chart_type": "line", "labels": ["a", "b"],
            "series": [{"name": "x", "values": [1]}]})
        assert problem and "one value per label" in problem

    def test_a_cell_is_a_value_not_a_structure(self):
        _, problem = display_stored({"kind": "table",
                                     "rows": [{"owner": {"name": "A"}}]})
        assert problem

    def test_a_display_is_read_at_a_glance(self):
        _, problem = display_stored({
            "kind": "table",
            "rows": [{"n": i} for i in range(DISPLAY_ROWS_MAX + 1)]})
        assert problem
