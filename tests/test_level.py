"""The recording meter: level curve and the scrolling history the overlay draws."""
import vox_core as core


def test_level_is_zero_in_a_quiet_room_and_rises_with_the_voice():
    assert core.level_from_rms(0) == 0
    assert core.level_from_rms(core.LEVEL_FLOOR) == 0
    values = [core.level_from_rms(r) for r in (0.005, 0.01, 0.02, 0.05, 0.1, 0.3, 1.0)]
    assert values == sorted(values) and 0 < values[0] < values[-1] <= 1
    assert core.level_from_rms(0.05) > 0.9   # normal speech nearly fills the meter


def test_level_never_leaves_zero_to_one():
    for r in (-1, 0, 0.5, 1, 5):
        assert 0 <= core.level_from_rms(r) <= 1


def test_history_scrolls_newest_last_and_clamps():
    h = core.LevelHistory(3)
    for v in (0.2, 0.5, 2.0, -1.0):
        h.push(v)
    assert h.values == [0.5, 1.0, 0.0]
    h.reset()
    assert h.values == [0.0, 0.0, 0.0]
