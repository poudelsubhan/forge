from forge.tui import ViewModel, _tickets_panel, _toolbox_panel


def test_view_model_tracks_ticket_and_synthesis_states() -> None:
    vm = ViewModel()
    vm.ingest(
        {
            "type": "toolbox_snapshot",
            "tools": [
                {
                    "name": "existing_tool",
                    "status": "promoted",
                    "uses": 4,
                    "revisions": 1,
                    "signature": "existing_tool() -> dict",
                }
            ],
        }
    )
    vm.ingest(
        {
            "type": "ticket_queue_polled",
            "open_count": 2,
            "tickets": [
                {"id": 42, "subject": "Refund", "status": "open"},
                {"id": 43, "subject": "Hours", "status": "open"},
            ],
        }
    )
    vm.ingest(
        {
            "type": "ticket_started",
            "ticket_id": 42,
            "subject": "Refund",
            "status": "open",
        }
    )
    vm.ingest({"type": "synthesis_requested", "name": "lookup_order"})
    assert vm.tickets[42]["status"] == "open"
    assert vm.tickets[43]["subject"] == "Hours"
    assert vm.tools["existing_tool"]["status"] == "promoted"
    assert vm.tools["lookup_order"]["status"] == "synthesizing"

    vm.ingest({"type": "verification_failed", "name": "lookup_order", "stderr": "boom"})
    assert vm.tools["lookup_order"]["status"] == "test failed"
    vm.ingest({"type": "revision_requested", "name": "lookup_order"})
    assert vm.tools["lookup_order"]["status"] == "revising"
    vm.ingest({"type": "tool_promoted", "name": "lookup_order", "revisions": 1})
    vm.ingest({"type": "ticket_solved", "ticket_id": 42, "subject": "Refund"})
    assert vm.tools["lookup_order"]["status"] == "promoted"
    assert vm.tickets[42]["status"] == "solved"

    # Rendering is part of the replay contract: new states must be accepted by
    # both panels without requiring live external state.
    assert _toolbox_panel(vm) is not None
    assert _tickets_panel(vm) is not None
