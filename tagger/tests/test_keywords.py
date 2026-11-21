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


def test_a_removed_keyword_stays_out_until_you_use_it(tmp_path):
    path = tmp_path / "keywords.json"
    lib = Library(path)
    lib.add(["Paris", "beach"], touch=False)
    lib.remove(["paris"])
    lib.save()
    lib = Library(path)                               # remembered
    lib.add(["Paris", "dog"], touch=False)            # found again on a Lightroom photo: stays out
    assert {it["name"] for it in lib.to_list()} == {"beach", "dog"}
    lib.add(["PARIS"])                                # typed on a scan: back
    lib.add(["Paris"], touch=False)
    assert lib.canonical("paris") == "PARIS" and not lib.removed


def test_lightroom_face_names_join_the_keyword_library(tmp_path):
    from framelog_tagger.cli import library_keywords
    lib = Library(tmp_path / "keywords.json")
    lib.add(["beach"])
    library_keywords(lib, ["Ana", "Kepler"])
    library_keywords(lib, [])
    again = Library(tmp_path / "keywords.json")                   # saved
    names = {it["name"]: it for it in again.to_list()}
    assert set(names) == {"beach", "Ana", "Kepler"}
    assert names["Ana"]["used"] == "" and names["Ana"]["count"] == 0   # suggested, but not counted as used


def test_region_names_are_read_from_exiftool_and_lightroom_xmp():
    from framelog_tagger.keywords import xmp_region_names
    exiftool = ("<mwg-rs:RegionList><rdf:Bag><rdf:li rdf:parseType='Resource'><mwg-rs:Area stArea:x='0.5'/>"
                "<mwg-rs:Name>Ana &amp; Bo</mwg-rs:Name><mwg-rs:Type>Face</mwg-rs:Type></rdf:li></rdf:Bag></mwg-rs:RegionList>")
    lightroom = ('<mwg-rs:RegionList><rdf:Bag><rdf:li><rdf:Description mwg-rs:Name="Kepler" mwg-rs:Type="Face"/>'
                 '</rdf:li></rdf:Bag></mwg-rs:RegionList>')
    assert xmp_region_names(exiftool) == ["Ana & Bo"]
    assert xmp_region_names(lightroom.encode()) == ["Kepler"]
    assert xmp_region_names(None) == [] and xmp_region_names("<dc:subject/>") == []


def test_the_page_gets_a_scans_own_keywords_without_its_face_names(tmp_path):
    from framelog_tagger.scans import list_scans
    xmp = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description><dc:subject><rdf:Bag><rdf:li>beach</rdf:li>'
           '<rdf:li>Ana</rdf:li></rdf:Bag></dc:subject><mwg-rs:RegionList><rdf:Bag><rdf:li rdf:parseType="Resource">'
           '<mwg-rs:Name>Ana</mwg-rs:Name></rdf:li></rdf:Bag></mwg-rs:RegionList></rdf:Description></rdf:RDF></x:xmpmeta>')
    Image.new("RGB", (40, 30)).save(tmp_path / "a.jpg", xmp=xmp.encode())
    s = list_scans(tmp_path)[0]
    assert s.keywords == ["beach", "Ana"] and s.region_names == ["Ana"]
    d = s.to_dict()
    assert d["keywords"] == ["beach"] and d["region_names"] == ["Ana"]
