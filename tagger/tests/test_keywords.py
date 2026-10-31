from PIL import Image

from framelog_tagger.keywords import Library, clean, merge, roll_keywords, xmp_keywords

XMP = """<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
 <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:subject><rdf:Bag><rdf:li>beach</rdf:li><rdf:li>Mom &amp; Dad</rdf:li><rdf:li>Beach</rdf:li></rdf:Bag></dc:subject>
 </rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end='w'?>"""


def test_roll_keywords_join_make_and_camera_and_skip_iso():
    assert roll_keywords("Nikon", "FE", "Portra 400") == ["Nikon FE", "Portra 400"]
    assert roll_keywords("Nikon", "nikon FE", "") == ["nikon FE"]   # the camera already names its make
    assert roll_keywords("", "FE", "") == ["FE"]
    assert roll_keywords("Nikon", "", "  HP5   Plus ") == ["Nikon", "HP5 Plus"]
    assert roll_keywords("", "", "") == []


def test_clean_and_merge():
    assert clean("  Cape   Cod ") == "Cape Cod"
    assert clean("a,b") == "a b"
    assert merge(["beach", "Dog"], ["Beach", " dog ", "", "cat"]) == ["beach", "Dog", "cat"]


def test_xmp_keywords_from_a_jpeg(tmp_path):
    p = tmp_path / "k.jpg"
    Image.new("RGB", (8, 8)).save(p, xmp=XMP.encode())
    with Image.open(p) as im:
        assert xmp_keywords(im.info.get("xmp")) == ["beach", "Mom & Dad"]
    assert xmp_keywords(None) == [] and xmp_keywords(b"<x:xmpmeta/>") == []


def test_library_round_trip_and_order(tmp_path):
    path = tmp_path / "keywords.json"
    lib = Library(path)
    lib.add(["zebra", "apple"], touch=False)
    lib.add(["beach"], written=3)
    assert [it["name"] for it in lib.to_list()] == ["beach", "apple", "zebra"]   # used first, then by name
    assert lib.canonical(" BEACH ") == "beach" and lib.canonical("new  one") == "new one"
    lib.add(["Beach"], written=1)   # same keyword: keeps its spelling, adds to its count
    lib.remove(["ZEBRA"])
    lib.save()
    again = Library(path)
    assert [(it["name"], it["count"]) for it in again.to_list()] == [("beach", 4), ("apple", 0)]
    assert Library(tmp_path / "missing.json").to_list() == []
