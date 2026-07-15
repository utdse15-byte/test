from manju.gui.jobs import CANCELABLE_RUNNING_KINDS

def test_series_new_episode_not_running_cancelable():
    assert "series_new_episode" not in CANCELABLE_RUNNING_KINDS
