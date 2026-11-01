"""The pet detector's plumbing: letterboxing, decoding the model's output, overlap suppression, head crops."""

import numpy as np
import pytest
from PIL import Image

from framelog_tagger import pets
from framelog_tagger.pets import decode, head_crop, letterbox, nms
from framelog_tagger.regions import FaceBox


def test_letterbox_keeps_the_shape_and_pads_with_grey():
    x, k, left, top = letterbox(Image.new("RGB", (400, 200), (255, 0, 0)), 100)
    assert x.shape == (1, 3, 100, 100) and k == 0.25 and (left, top) == (0, 25)
    assert x[0, 0, 50, 50] == pytest.approx(1.0) and x[0, 1, 50, 50] == 0                 # the picture, red
    assert x[0, 0, 5, 50] == pytest.approx(114 / 255) and x[0, 2, 95, 50] == pytest.approx(114 / 255)   # grey bars above and below


def test_nms_keeps_the_best_of_overlapping_boxes():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=float)
    assert nms(boxes, np.array([0.5, 0.9, 0.7])) == [1, 2]
    assert nms(boxes[:0], np.array([])) == []


def test_decode_maps_boxes_back_into_the_original_picture():
    # a 400x200 picture letterboxed into 100x100 (scale .25, 25px of padding above): a head at x 100-140, y 60-100
    cx, cy, w, h = (100 + 140) / 2 * 0.25, (80 * 0.25) + 25, 40 * 0.25, 40 * 0.25
    out = np.zeros((1, 5, 3), dtype=np.float32)
    out[0, :, 0] = [cx, cy, w, h, 0.9]       # the head
    out[0, :, 1] = [cx, cy, w, h, 0.8]       # a duplicate of it
    out[0, :, 2] = [10, 10, 5, 5, 0.1]       # below the confidence floor
    found = decode(out, 0.25, 0, 25, (400, 200), 0.25)
    assert len(found) == 1
    box, score = found[0]
    assert score == pytest.approx(0.9)
    assert (box.x, box.y, box.w, box.h) == pytest.approx((100 / 400, 60 / 200, 40 / 400, 40 / 200))
    assert decode(out[:, :, 2:], 0.25, 0, 25, (400, 200), 0.25) == []


def test_decode_clips_a_box_that_runs_off_the_picture_and_accepts_either_layout():
    out = np.zeros((1, 5, 1), dtype=np.float32)
    out[0, :, 0] = [95, 50, 30, 30, 0.9]     # centred near the right edge of a 100x100 letterbox
    box, _ = decode(out, 1.0, 0, 0, (100, 100), 0.25)[0]
    assert box.x + box.w == pytest.approx(1.0) and 0 <= box.x
    assert decode(out.transpose(0, 2, 1), 1.0, 0, 0, (100, 100), 0.25)[0][0] == box   # (1, N, 5) works too


def test_head_crop_is_a_normalised_224_square_with_context():
    im = Image.new("RGB", (800, 600), (124, 116, 104))   # about the ImageNet mean colour, so it normalises to ~0
    x = head_crop(im, FaceBox(0.4, 0.4, 0.1, 0.13))
    assert x.shape == (1, 3, 224, 224) and x.dtype == np.float32 and abs(float(x.mean())) < 0.1
    near_edge = head_crop(im, FaceBox(0.0, 0.0, 0.1, 0.1))   # context beyond the edge is padded, not an error
    assert near_edge.shape == (1, 3, 224, 224)


def test_the_embedder_is_downloaded_once(tmp_path, monkeypatch):
    calls = []

    class Resp:
        def __init__(self): self.data = [b"model-bytes", b""]
        def read(self, n): return self.data.pop(0)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(pets.urllib.request, "urlopen", lambda url, timeout: calls.append(url) or Resp())
    dest = tmp_path / "models" / "dino.onnx"
    assert pets.download_embedder(dest).read_bytes() == b"model-bytes"
    pets.download_embedder(dest)
    assert len(calls) == 1 and calls[0].endswith(f"{pets.EMBED_VARIANT}.onnx") and not dest.with_suffix(".part").exists()


def test_a_failed_download_says_what_was_being_fetched(tmp_path, monkeypatch):
    def offline(url, timeout):
        raise pets.urllib.error.URLError("no network")

    monkeypatch.setattr(pets.urllib.request, "urlopen", offline)
    with pytest.raises(RuntimeError, match="couldn't download the pet model"):
        pets.download_embedder(tmp_path / "dino.onnx")
    assert not (tmp_path / "dino.onnx").exists()
