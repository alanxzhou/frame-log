"""Pairing scans with log cards by frame number (framelog_tagger.pairing)."""

from framelog_tagger.pairing import by_frame, scan_number


def cards(*frames):
    """(card id, frame) pairs; ids are 100 + position so they can't be mistaken for frame numbers."""
    return [(100 + i, f) for i, f in enumerate(frames)]


def renamed(n, start=1, name="2026-10 Lisbon"):
    return [f"{name}-{i:02d}.jpg" for i in range(start, start + n)]


def everything_once(result, scans, entries):
    got_scans = [s for s, _ in result["slots"] if s is not None]
    got_cards = [e for _, e in result["slots"] if e is not None]
    assert sorted(got_scans) == sorted(scans) and sorted(got_cards) == sorted(e for e, _ in entries)


def test_scan_number_reads_the_trailing_digits():
    assert scan_number("2026-10 Lisbon-05.jpg") == 5
    assert scan_number("004123450005.jpg") == 4123450005
    assert scan_number("IMG_0012.JPG") == 12
    assert scan_number("beach.jpg") is None
    assert scan_number("05 beach.jpg") is None


def test_a_frame_never_logged_leaves_its_scan_without_a_card():
    scans, entries = renamed(12), cards(1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12)
    r = by_frame(scans, entries)
    assert r["offset"] == 0 and r["matched"] == 11
    assert r["slots"][4] == ["2026-10 Lisbon-05.jpg", None]
    assert r["slots"][5] == ["2026-10 Lisbon-06.jpg", 104]   # frame 6, not frame 7
    assert len(r["slots"]) == 12
    everything_once(r, scans, entries)


def test_lab_names_pair_at_a_large_offset():
    scans = [f"0041234500{i:02d}.jpg" for i in range(1, 13)]
    r = by_frame(scans, cards(1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12))
    assert r["offset"] == -4123450000 and r["slots"][4] == ["004123450005.jpg", None]


def test_numbering_off_by_one_is_detected():
    scans = [f"0041234500{i:02d}.jpg" for i in range(0, 12)]   # the lab started at 00
    r = by_frame(scans, cards(1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12))
    assert r["offset"] == -4123449999
    assert r["slots"][0] == ["004123450000.jpg", 100] and r["slots"][4] == ["004123450004.jpg", None]
    r = by_frame(renamed(12, start=0), cards(*range(1, 13)))
    assert r["offset"] == 1 and r["matched"] == 12


def test_a_tie_goes_to_no_offset():
    # 12 scans, frames 1-11: offsets 0 and +1 both match 11
    assert by_frame(renamed(12), cards(*range(1, 12)))["offset"] == 0
    assert by_frame(renamed(12), cards(*range(2, 13)))["offset"] == 0


def test_a_card_with_no_scan_keeps_its_place():
    scans = [s for s in renamed(6) if not s.endswith("-03.jpg")]   # a blank, deleted
    entries = cards(1, 2, 3, 4, 5, 6)
    r = by_frame(scans, entries)
    assert r["slots"][2] == [None, 102]
    everything_once(r, scans, entries)


def test_a_card_beyond_the_scans_goes_last():
    r = by_frame(renamed(3), cards(1, 2, 3, 9))
    assert r["slots"] == [["2026-10 Lisbon-01.jpg", 100], ["2026-10 Lisbon-02.jpg", 101],
                          ["2026-10 Lisbon-03.jpg", 102], [None, 103]]


def test_a_second_card_with_the_same_frame_goes_at_the_end():
    entries = cards(1, 2, 2, 3)   # a pasted copy of #2
    r = by_frame(renamed(3), entries)
    assert r["slots"][1] == ["2026-10 Lisbon-02.jpg", 101] and r["slots"][-1] == [None, 102]
    everything_once(r, renamed(3), entries)


def test_a_sparse_log_still_pairs_by_number():
    entries = cards(3, 9, 14, 20, 27, 33)   # a few frames logged out of 36
    r = by_frame(renamed(36), entries)
    assert r["offset"] == 0 and r["matched"] == 6 and len(r["slots"]) == 36
    assert r["slots"][13] == ["2026-10 Lisbon-14.jpg", 102]


def test_falls_back_when_the_numbers_dont_fit():
    assert by_frame(["a.jpg", "b-02.jpg"], cards(1, 2)) is None            # not every name ends in a number
    assert by_frame(["x-1.jpg", "y-01.jpg"], cards(1, 2)) is None          # two scans with the same number
    assert by_frame(renamed(3), []) is None                                # nothing to pair with
    # numbers that match no constant offset: a reversed roll, say
    assert by_frame(renamed(10), cards(*[3 * i for i in range(10)])) is None


def test_session_offers_frame_pairing(tmp_path, monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from PIL import Image
    from framelog_tagger.server import AppState, create_app

    monkeypatch.setenv("FRAMELOG_TAGGER_CONFIG", str(tmp_path / "config.json"))
    scans = tmp_path / "Roll"
    scans.mkdir()
    for i in (1, 2, 3):
        Image.new("RGB", (8, 8)).save(scans / f"Roll-{i:02d}.jpg")
    log = tmp_path / "roll.json"
    log.write_text(json.dumps({"app": "framelog", "schema": 2, "roll": "Roll", "entries": [
        {"frame": 1, "iso": "2026-08-30T14:05:12Z"}, {"frame": 3, "iso": "2026-08-30T14:07:12Z"}]}))
    c = TestClient(create_app(AppState(exiftool=None, default_tz="UTC")))
    j = c.post("/api/load", json={"scans_dir": str(scans), "log_path": str(log)}).json()
    ids = [e["id"] for e in j["entries"]]
    assert j["frame_pairing"]["slots"] == [["Roll-01.jpg", ids[0]], ["Roll-02.jpg", None], ["Roll-03.jpg", ids[1]]]


def test_a_roll_numbered_last_frame_first_pairs_in_reverse_when_asked():
    scans = renamed(12)                                  # Lisbon-01 is the last frame, Lisbon-12 the first
    entries = cards(1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12)  # frame 5 never logged
    assert not by_frame(scans, entries)["reversed"]      # both ways match 11: the numbers can't tell, up wins
    r = by_frame(scans, entries, -1)
    assert r["reversed"] and r["offset"] == 13 and r["matched"] == 11
    pairs = {s: e for s, e in r["slots"]}
    assert pairs["2026-10 Lisbon-12.jpg"] == 100 and pairs["2026-10 Lisbon-01.jpg"] == 110   # frames 1 and 12
    assert pairs["2026-10 Lisbon-08.jpg"] is None                                          # 13 - 8 = frame 5
    everything_once(r, scans, entries)


def test_counting_down_is_found_when_it_matches_more():
    scans = [f"x-{i:02d}.jpg" for i in range(1, 13) if i != 8]   # the lab dropped scan 08, a blank (frame 5 reversed)
    entries = cards(1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12)          # and frame 5 was never logged
    r = by_frame(scans, entries)
    assert r["reversed"] and r["offset"] == 13 and r["matched"] == 11
    everything_once(r, scans, entries)


def test_counting_up_wins_a_tie_with_counting_down():
    r = by_frame(renamed(1), cards(1))
    assert not r["reversed"] and r["offset"] == 0
