from __future__ import annotations

from pathlib import Path

import pytest

from ai_trader.exceptions import KillSwitchEngagedError
from ai_trader.kill_switch import KillSwitch


def test_starts_engaged(tmp_path: Path) -> None:
    switch = KillSwitch(tmp_path / "KILL_SWITCH", initially_engaged=True)
    assert switch.is_engaged() is True
    with pytest.raises(KillSwitchEngagedError):
        switch.assert_clear()


def test_disengage_and_engage(tmp_path: Path) -> None:
    switch = KillSwitch(tmp_path / "KILL_SWITCH", initially_engaged=True)
    switch.disengage("test")
    assert switch.is_engaged() is False
    switch.assert_clear()
    switch.engage("halt")
    assert switch.is_engaged() is True
    snap = switch.snapshot()
    assert snap["reason"] == "halt"
