"""C79: functional spend_gate raises WaitingUser without assume_yes."""
from manju.build.graph import WaitingUser, spend_gate


def test_spend_gate_with_ask_before(tmp_project, monkeypatch):
    class C:
        ask_before = ["expensive_generation"]
        budget = tmp_project.load_config().budget

    monkeypatch.setattr(tmp_project, "load_config", lambda: C())
    try:
        spend_gate(tmp_project, 1.0, "CNY", assume_yes=False, hint="test")
        assert False, "expected WaitingUser"
    except WaitingUser as exc:
        assert "waiting_user" in str(exc).lower() or "预估" in str(exc)


def test_spend_gate_assume_yes_skips(tmp_project, monkeypatch):
    class C:
        ask_before = ["expensive_generation"]
        budget = tmp_project.load_config().budget

    monkeypatch.setattr(tmp_project, "load_config", lambda: C())
    spend_gate(tmp_project, 1.0, "CNY", assume_yes=True, hint="test")  # no raise
