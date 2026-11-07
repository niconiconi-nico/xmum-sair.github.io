"""Checks for intake/redact.py. Run from the repo root: python intake/test_redact.py (no network needed)."""
import json, math, os, pathlib, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import fitz
import redact


def tmp(): return pathlib.Path(tempfile.mkdtemp())


def pdf_pages(pages, rotate=0):
    """A PDF with one text line (or lines, split at "\\n") per page; every page rotated by `rotate`."""
    d = fitz.open()
    for t in pages:
        pg = d.new_page()
        if t: pg.insert_text((72, 72), t)
        if rotate: pg.set_rotation(rotate)
    path = tmp() / "in.pdf"; d.save(path); d.close()
    return path


def image_page(rotate=0, mark=True):
    """A "scanned" A4 page: one light-grey image filling the page, with (when `mark`) a dark block, a signature, at
    10–30 % across and 10–20 % down the unrotated page."""
    pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 300, 400), 0)
    pix.clear_with(235)
    if mark: pix.set_rect(fitz.IRect(30, 40, 90, 80), (40,))
    d = fitz.open(); pg = d.new_page(width=595, height=842)
    pg.insert_image(pg.rect, pixmap=pix)
    if rotate: pg.set_rotation(rotate)
    path = tmp() / "scan.pdf"; d.save(path); d.close()
    return path


def text(path, page):
    with fitz.open(path) as d: return d[page - 1].get_text()


def _gray(path, page, dpi=40):
    with fitz.open(path) as d: return d[page - 1].get_pixmap(colorspace=fitz.csGRAY, dpi=dpi)


def mean_pixel(path, page, region):
    """Mean grey value (0 black, 255 white) of the page as displayed, over `region` given as fractions of its width and
    height (x0, y0, x1, y1)."""
    pix = _gray(path, page)
    x0, y0, x1, y1 = (int(round(f * n)) for f, n in zip(region, (pix.width, pix.height) * 2))
    vals = [pix.samples[y * pix.stride + x] for y in range(y0, y1) for x in range(x0, x1)]
    return sum(vals) / len(vals)


def black_share(path, page):
    pix = _gray(path, page)
    vals = [pix.samples[y * pix.stride + x] for y in range(pix.height) for x in range(pix.width)]
    return sum(v < 20 for v in vals) / len(vals)


def _text(page, s): return {"page": page, "kind": "text", "text": s, "box": [0, 0, 0, 0], "what": "学号 | student ID"}


def _box(page, b): return {"page": page, "kind": "box", "text": "", "box": b, "what": "签名 | signature"}


# ---- the brief's checks

def test_text_redaction_removes_the_strings():
    p = pdf_pages(["Name: Tan Ah Kow   ID: EEE2109300", "Question 1"]); out = tmp() / "o.pdf"
    r = redact.apply(p, out, [{"page": 1, "kind": "text", "text": "EEE2109300"}, {"page": 1, "kind": "text", "text": "Tan Ah Kow"}])
    assert r.ok and "EEE2109300" not in text(out, 1) and "Tan Ah Kow" not in text(out, 1) and "Question 1" in text(out, 2)
    assert redact.text_gone(out, [{"page": 1, "kind": "text", "text": "EEE2109300"}])
    assert r.out == out and r.box_pages == [] and r.problems == [] and len(r.rects) == 2


def test_a_string_split_across_lines_is_still_removed_or_refused():        # Review Focus 2
    p = pdf_pages(["Name: Tan Ah\nKow"]); out = tmp() / "o.pdf"
    r = redact.apply(p, out, [{"page": 1, "kind": "text", "text": "Tan Ah Kow"}])
    assert (r.ok and all(w not in text(out, 1) for w in ("Tan", "Kow"))) or not r.ok


def test_missing_text_fails_closed_without_echoing_it():
    r = redact.apply(pdf_pages(["Question 1"]), tmp() / "o.pdf", [{"page": 1, "kind": "text", "text": "EEE2109300"}])
    assert not r.ok and "EEE2109300" not in " ".join(r.problems)


def test_box_redaction_blackens_the_pixels_and_is_clamped():
    p = image_page(); out = tmp() / "o.pdf"
    r = redact.apply(p, out, [{"page": 1, "kind": "box", "box": [100, 100, 300, 200]}, {"page": 1, "kind": "box", "box": [-50, 950, 1200, 1100]}])
    assert r.ok and r.box_pages == [1] and mean_pixel(out, 1, (0.12, 0.12, 0.28, 0.18)) < 20 and mean_pixel(out, 1, (0.5, 0.5, 0.6, 0.6)) > 200


def test_cached_rects_reproduce_the_redaction():
    p = pdf_pages(["ID: EEE2109300"]); a, b = tmp() / "a.pdf", tmp() / "b.pdf"
    r = redact.apply(p, a, [{"page": 1, "kind": "text", "text": "EEE2109300"}])
    assert redact.apply(p, b, [], cached_rects=r.rects).ok and "EEE2109300" not in text(b, 1)


# ---- controller rulings: validation, whole-document check, rotation, what is removed, cached rectangles

def test_malformed_redactions_fail_closed_and_write_nothing():
    p = pdf_pages(["ID: EEE2109300", "Question 1"])
    bad = [_text(0, "EEE2109300"), _text(3, "EEE2109300"), _text("1", "EEE2109300"), _text(True, "EEE2109300"),
           _text(1.0, "EEE2109300"), _text(None, "EEE2109300"), dict(_text(1, "EEE2109300"), kind="blur"),
           dict(_text(1, "EEE2109300"), kind=None), _text(1, ""), _text(1, "  \n "), dict(_text(1, ""), text=None),
           {"page": 1, "kind": "text"}, _box(1, [1, 2, 3]), _box(1, [1, 2, 3, 4, 5]), _box(1, ["1", 2, 3, 4]),
           _box(1, [0, 0, math.nan, 10]), _box(1, [0, 0, math.inf, 10]), _box(1, [True, 0, 10, 10]), _box(1, None),
           _box(1, [300, 100, 100, 200]), _box(1, [100, 100, 100, 200]), _box(1, [1100, 0, 1200, 100]),
           {"page": 1, "kind": "box"}, "EEE2109300", None, ["EEE2109300"]]
    for entry in bad:
        out = tmp() / "o.pdf"
        r = redact.apply(p, out, [_text(1, "EEE2109300"), entry])
        assert not r.ok and r.out is None and not out.exists() and r.rects == [] and r.box_pages == [], entry
        assert r.problems and all("redaction #2" in x for x in r.problems), (entry, r.problems)
        assert "EEE2109300" not in json.dumps(r.problems, ensure_ascii=False), entry
    for whole in (None, {"page": 1}, "EEE2109300"):                        # not a list at all
        out = tmp() / "o.pdf"
        r = redact.apply(p, out, whole)
        assert not r.ok and r.out is None and not out.exists() and r.problems and "EEE2109300" not in " ".join(r.problems)


def test_a_spaced_out_repeat_on_another_page_fails_the_whole_document():
    p = pdf_pages(["ID: EEE2109300", "Question 1", "Footer EEE 2109300"]); out = tmp() / "o.pdf"
    r = redact.apply(p, out, [_text(1, "EEE2109300")])              # the AI named page 1 only; search misses page 3
    assert not r.ok and "EEE2109300" not in text(out, 1) and "EEE 2109300" in text(out, 3)
    assert any("page 3" in x for x in r.problems) and not any("page 1 " in x for x in r.problems)
    assert "EEE2109300" not in " ".join(r.problems)
    assert not redact.text_gone(out, [_text(1, "EEE2109300")])
    both = redact.apply(p, tmp() / "o2.pdf", [_text(1, "EEE2109300"), _text(3, "EEE 2109300")])
    assert both.ok and redact.text_gone(both.out, [_text(1, "EEE2109300")])


def test_text_gone_reads_through_whitespace_and_case():
    gone = lambda page, s: redact.text_gone(pdf_pages(["Question 1", page]), [_text(1, s)])
    assert gone("Question 2", "EEE2109300") and gone("", "EEE2109300")
    assert not gone("ID: EEE 2109300", "EEE2109300")                  # a space inside the ID
    assert not gone("ID: EEE2109\n300", "EEE2109300")                 # a line break inside it
    assert not gone("Name: Tan Ah\nKow", "Tan Ah Kow")
    assert not gone("HEADER TAN AH KOW", "Tan  Ah\tKow")              # another case, other whitespace in the needle
    assert not redact.text_gone(pdf_pages(["x"]), [_text(1, "")]) and not redact.text_gone(tmp() / "none.pdf", [])


def test_the_word_fallback_redacts_each_word_or_refuses():
    d = fitz.open(); pg = d.new_page()
    pg.insert_text((150, 72), "Kow"); pg.insert_text((72, 72), "Tan Ah")     # read as "Kow / Tan Ah": the whole name is not found
    pg.insert_text((72, 120), "Question 1")
    p = tmp() / "in.pdf"; d.save(p); d.close()
    out = tmp() / "o.pdf"
    r = redact.apply(p, out, [_text(1, "Tan Ah Kow")])
    assert r.ok and "Tan" not in text(out, 1) and "Kow" not in text(out, 1) and "Question 1" in text(out, 1)
    r = redact.apply(p, tmp() / "o.pdf", [_text(1, "Tan Ah Kow Binti")])        # a word that is nowhere
    assert not r.ok and any("page 1" in x for x in r.problems) and "Binti" not in " ".join(r.problems)
    r = redact.apply(p, tmp() / "o.pdf", [_text(1, "Li Xu")])                   # no word long enough to search
    assert not r.ok and any("page 1" in x for x in r.problems)


def test_the_word_fallback_checks_every_page():
    d = fitz.open(); pg = d.new_page()
    pg.insert_text((150, 72), "Kow"); pg.insert_text((72, 72), "Tan Ah")
    d.new_page().insert_text((72, 72), "Signed: Kow")
    p = tmp() / "in.pdf"; d.save(p); d.close()
    r = redact.apply(p, tmp() / "o.pdf", [_text(1, "Tan Ah Kow")])
    assert not r.ok and any("page 2" in x for x in r.problems) and "Kow" not in " ".join(r.problems)


def test_text_on_a_rotated_page_is_found_and_removed():
    for rot in (90, 180, 270):
        p = pdf_pages(["ID: EEE2109300 Question 1"], rotate=rot); out = tmp() / "o.pdf"
        r = redact.apply(p, out, [_text(1, "EEE2109300")])
        assert r.ok and "EEE2109300" not in text(out, 1) and "Question 1" in text(out, 1), rot
        x0, y0, x1, y1 = r.rects[0]["rect"]                            # unrotated page space: the text runs along y = 72
        assert x0 < x1 and y0 < 72 < y1 and y1 - y0 < 30 and x0 > 72, (rot, r.rects)


def test_a_box_on_a_rotated_page_lands_where_it_was_seen():
    for rot in (90, 180, 270):
        p = image_page(rotate=rot, mark=False); out = tmp() / "o.pdf"
        r = redact.apply(p, out, [_box(1, [100, 100, 300, 200])])           # as displayed: the top left of the page
        assert r.ok and r.box_pages == [1], rot
        assert mean_pixel(out, 1, (0.12, 0.12, 0.28, 0.18)) < 20, rot
        assert mean_pixel(out, 1, (0.5, 0.5, 0.6, 0.6)) > 200 and mean_pixel(out, 1, (0.7, 0.7, 0.9, 0.9)) > 200, rot
        assert abs(black_share(out, 1) - 0.24 * 0.14) < 0.01, (rot, black_share(out, 1))   # widened 2 % a side, nothing else
    p = image_page(rotate=90, mark=False); out = tmp() / "o.pdf"           # the box ignoring rotation would land at 80–90 % across
    redact.apply(p, out, [_box(1, [100, 100, 300, 200])])
    assert mean_pixel(out, 1, (0.82, 0.12, 0.88, 0.28)) > 200


def test_rotated_pages_with_an_offset_crop_box_are_blacked_where_seen():
    for rot in (0, 90, 180, 270):
        pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 300, 400), 0); pix.clear_with(235)
        d = fitz.open(); pg = d.new_page(width=600, height=800)
        pg.insert_image(pg.rect, pixmap=pix)
        pg.insert_text((150, 650), "ID: EEE2109300")
        pg.set_cropbox(fitz.Rect(50, 100, 550, 700)); pg.set_rotation(rot)
        p = tmp() / "in.pdf"; d.save(p); d.close()
        out = tmp() / "o.pdf"
        r = redact.apply(p, out, [_box(1, [100, 100, 300, 200]), _text(1, "EEE2109300")])
        assert r.ok and "EEE2109300" not in text(out, 1), rot
        assert mean_pixel(out, 1, (0.12, 0.12, 0.28, 0.18)) < 20, rot
        pix = _gray(out, 1)                              # no white hole: what was removed is covered in black (but for
        assert sum(v > 245 for v in pix.samples) / len(pix.samples) < 0.001, rot    # a sliver of whole image pixels)


def test_boxes_grow_two_percent_a_side_and_stay_on_the_page():
    inside = [(0.085, 0.15, 0.095, 0.16), (0.305, 0.15, 0.315, 0.16), (0.15, 0.085, 0.16, 0.095), (0.15, 0.205, 0.16, 0.215)]
    outside = [(0.06, 0.15, 0.07, 0.16), (0.33, 0.15, 0.34, 0.16), (0.15, 0.06, 0.16, 0.07), (0.15, 0.23, 0.16, 0.24)]
    for rot in (0, 90):
        p = image_page(rotate=rot, mark=False); out = tmp() / "o.pdf"
        r = redact.apply(p, out, [_box(1, [100, 100, 300, 200]), _box(1, [-50, 950, 1200, 1100])])
        assert r.ok and all(mean_pixel(out, 1, q) < 20 for q in inside) and all(mean_pixel(out, 1, q) > 200 for q in outside), rot
        assert mean_pixel(out, 1, (0.4, 0.935, 0.6, 1.0)) < 20 and mean_pixel(out, 1, (0.4, 0.9, 0.6, 0.92)) > 200, rot  # from 93 %
        assert all(-0.01 <= x0 and x1 <= 595.01 and -0.01 <= y0 and y1 <= 842.01 for x0, y0, x1, y1 in (x["rect"] for x in r.rects)), r.rects


def test_box_redaction_removes_the_image_pixels_not_only_covers_them():
    p = image_page(); out = tmp() / "o.pdf"
    assert redact.apply(p, out, [_box(1, [80, 80, 320, 220])]).ok              # the signature block: 10–30 %, 10–20 %
    with fitz.open(out) as d:
        (xref, *_), = d[0].get_images(full=True)
        pix = fitz.Pixmap(d.extract_image(xref)["image"])
    assert min(pix.pixel(60, 60)) > 200 and min(pix.pixel(200, 300)) > 200      # the dark block is gone from the image itself


def test_rects_hold_no_text_and_reproduce_the_redaction():
    p = pdf_pages(["Name: Tan Ah Kow   ID: EEE2109300", "Question 1"]); a = tmp() / "a.pdf"
    r = redact.apply(p, a, [_text(1, "EEE2109300"), _text(1, "Tan Ah Kow"), _box(2, [500, 500, 600, 600])])
    assert r.ok and r.box_pages == [2] and len(r.rects) == 3
    assert all(set(x) == {"page", "rect"} and len(x["rect"]) == 4 and all(isinstance(v, float) for v in x["rect"]) for x in r.rects)
    flat = json.dumps(r.rects)
    assert "EEE2109300" not in flat and "Tan" not in flat and "text" not in flat
    b = tmp() / "b.pdf"
    again = redact.apply(p, b, [_text(1, "something else entirely")], cached_rects=json.loads(flat))   # redactions are ignored
    assert again.ok and again.rects == r.rects and again.out == b
    assert "EEE2109300" not in text(b, 1) and "Tan Ah Kow" not in text(b, 1) and "Question 1" in text(b, 2)
    assert again.box_pages == [1, 2]                     # which rectangle was a box is not cached: every redacted page
    s = image_page(); c, e = tmp() / "c.pdf", tmp() / "e.pdf"
    box = redact.apply(s, c, [_box(1, [100, 100, 300, 200])])
    assert redact.apply(s, e, [], cached_rects=box.rects).ok and mean_pixel(e, 1, (0.12, 0.12, 0.28, 0.18)) < 20


def test_malformed_cached_rects_fail_closed():
    p = pdf_pages(["ID: EEE2109300"])
    good = redact.apply(p, tmp() / "a.pdf", [_text(1, "EEE2109300")]).rects[0]
    for entry in ({"page": 0, "rect": good["rect"]}, {"page": 2, "rect": good["rect"]}, {"page": "1", "rect": good["rect"]},
                  {"page": 1, "rect": good["rect"][:3]}, {"page": 1, "rect": ["0", 0, 10, 10]}, {"page": 1, "rect": [0, 0, math.nan, 10]},
                  {"page": 1, "rect": [10, 0, 10, 10]}, {"page": 1}, "rect", None):
        out = tmp() / "o.pdf"
        r = redact.apply(p, out, [_text(1, "EEE2109300")], cached_rects=[good, entry])
        assert not r.ok and r.out is None and not out.exists() and any("#2" in x for x in r.problems), entry
    r = redact.apply(p, tmp() / "o.pdf", [], cached_rects={"page": 1})
    assert not r.ok and r.out is None


def test_the_original_is_never_written():
    p = pdf_pages(["Name: Tan Ah Kow   ID: EEE2109300", "Footer EEE2109300"]); before = p.read_bytes()
    redact.apply(p, tmp() / "a.pdf", [_text(1, "EEE2109300"), _text(2, "EEE2109300")])
    redact.apply(p, tmp() / "b.pdf", [_text(1, "EEE2109300")])                           # page 2's copy found too
    redact.apply(p, tmp() / "c.pdf", [_text(9, "EEE2109300")])                           # malformed
    r = redact.apply(p, p, [_text(1, "EEE2109300"), _text(2, "EEE2109300")])             # out is the original
    assert not r.ok and r.out is None and p.read_bytes() == before
    same = p.parent / ".." / p.parent.name / p.name                                    # the same file, spelled differently
    assert not redact.apply(p, same, [_text(1, "EEE2109300")]).ok and p.read_bytes() == before
    redact.apply(p, p.with_name(p.name.upper()), [_text(1, "EEE2109300")])              # the same file on Windows
    assert p.read_bytes() == before
    link = p.with_name("link.pdf")
    try:
        os.link(p, link)
    except OSError:
        link = None
    if link:                                                                           # a hard link to it
        r = redact.apply(p, link, [_text(1, "EEE2109300"), _text(2, "EEE2109300")])
        assert not r.ok and r.out is None and p.read_bytes() == before


def test_metadata_xmp_and_embedded_files_are_stripped():
    d = fitz.open(); d.new_page().insert_text((72, 72), "ID: EEE2109300")
    d.set_metadata({"author": "Tan Ah Kow", "title": "EEE2109300 final"})
    d.set_xml_metadata("<x:xmpmeta xmlns:x='adobe:ns:meta/'><dc>Tan Ah Kow</dc></x:xmpmeta>")
    d.embfile_add("answers.txt", b"Tan Ah Kow EEE2109300")
    p = tmp() / "in.pdf"; d.save(p); d.close()
    out = tmp() / "o.pdf"
    assert redact.apply(p, out, [_text(1, "EEE2109300")]).ok
    with fitz.open(out) as o:
        assert not any(o.metadata.get(k) for k in ("author", "title", "subject", "keywords", "creator", "producer"))
        assert o.get_xml_metadata() == "" and o.embfile_names() == []
    raw = out.read_bytes()
    assert b"Tan Ah Kow" not in raw and b"EEE2109300" not in raw


def test_unreadable_or_non_pdf_input_fails_closed():
    junk = tmp() / "x.pdf"; junk.write_bytes(b"not a pdf at all")
    png = tmp() / "x.png"; png.write_bytes(fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 10, 10), 0).tobytes("png"))
    d = fitz.open(); d.new_page().insert_text((72, 72), "ID: EEE2109300")
    locked = tmp() / "locked.pdf"; d.save(locked, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o"); d.close()
    for src in (junk, tmp() / "missing.pdf", png, locked):
        out = tmp() / "o.pdf"
        r = redact.apply(src, out, [_text(1, "EEE2109300")])
        assert not r.ok and r.out is None and not out.exists() and r.problems


def test_no_redactions_still_clears_the_metadata():
    d = fitz.open(); d.new_page().insert_text((72, 72), "Question 1"); d.set_metadata({"author": "Tan Ah Kow"})
    p = tmp() / "in.pdf"; d.save(p); d.close()
    out = tmp() / "o.pdf"
    r = redact.apply(p, out, [])
    assert r.ok and r.rects == [] and r.box_pages == [] and "Question 1" in text(out, 1)
    with fitz.open(out) as o:
        assert not o.metadata.get("author")


def test_render_draws_the_pages_as_displayed():
    p = image_page(rotate=90)
    (png,) = redact.render(p, [1])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pix = fitz.Pixmap(png)
    assert abs(pix.width - 842 * 150 / 72) <= 1 and abs(pix.height - 595 * 150 / 72) <= 1       # landscape: rotation applied
    two = pdf_pages(["a", "b", "c"])
    assert len(redact.render(two, [3, 1], dpi=50)) == 2
    for bad in (0, 4, -1, "1"):
        try:
            redact.render(two, [bad]); assert False, bad
        except ValueError:
            pass


# ---- fix round 1: line art, annotations and form fields, outline and labels, whole words, text_gone, stale output

ID = "EEE2109300"


def anywhere(path, s):
    """Is `s` anywhere in the file: raw bytes, any object, any stream decompressed, the fully expanded file?"""
    raw = s.encode()
    if raw in pathlib.Path(path).read_bytes():
        return True
    with fitz.open(path) as d:
        for x in range(1, d.xref_length()):
            if s in (d.xref_object(x) or "") or (d.xref_is_stream(x) and raw in (d.xref_stream(x) or b"")):
                return True
        return raw in d.tobytes(expand=255)


def _save(d):
    p = tmp() / "in.pdf"; d.save(p); d.close()
    return p


def _field(pg, value=ID, rect=(72, 300, 300, 330), name="sid"):
    w = fitz.Widget(); w.field_type = fitz.PDF_WIDGET_TYPE_TEXT; w.field_name = name; w.field_value = value
    w.rect = fitz.Rect(rect); pg.add_widget(w)
    return [x.xref for x in pg.widgets()][-1]


def test_line_art_a_box_only_touches_is_removed():
    d = fitz.open(); pg = d.new_page(width=600, height=800)
    sh = pg.new_shape(); sh.draw_polyline([(100, 100), (200, 150), (300, 100), (400, 150)]); sh.finish(color=(0, 0, 0), width=4)
    sh.commit()
    p = _save(d); out = tmp() / "o.pdf"
    r = redact.apply(p, out, [_box(1, [150, 100, 400, 200])])                   # covers x 78–252: the signature's left part
    assert r.ok
    with fitz.open(out) as o:
        assert [x for x in o[0].get_drawings() if x["type"] == "s"] == []     # no stroke left, only the black fill
    assert mean_pixel(out, 1, (0.5, 0.11, 0.7, 0.2)) > 250                   # nothing of it right of the box


def test_a_form_field_on_a_redacted_page_is_gone_from_the_file():
    for hidden in (False, True):
        d = fitz.open(); pg = d.new_page(); pg.insert_text((72, 72), "Question 1")
        x = _field(pg)
        if hidden: d.xref_set_key(x, "F", "2")
        p = _save(d); out = tmp() / "o.pdf"
        r = redact.apply(p, out, [_box(1, [100, 340, 520, 410])])               # the box covers the field
        assert r.ok and not anywhere(out, ID) and "Question 1" in text(out, 1), hidden
        with fitz.open(out) as o:
            assert list(o[0].widgets()) == [] and o.xref_get_key(o.pdf_catalog(), "AcroForm")[0] == "null", hidden
        again = tmp() / "c.pdf"                                                 # a rebuild from the cache does the same
        assert redact.apply(p, again, [], cached_rects=r.rects).ok and not anywhere(again, ID), hidden


def test_a_parent_field_value_does_not_survive():
    d = fitz.open(); pg = d.new_page(); pg.insert_text((72, 72), "Question 1")
    kid = _field(pg)
    parent = d.get_new_xref(); d.update_object(parent, f"<</FT/Tx/T(student)/V({ID})/Kids[{kid} 0 R]>>")
    for k, v in (("Parent", f"{parent} 0 R"), ("T", "null"), ("V", "null"), ("FT", "null")): d.xref_set_key(kid, k, v)
    d.xref_set_key(d.pdf_catalog(), "AcroForm/Fields", f"[{parent} 0 R]")
    p = _save(d)
    assert anywhere(p, ID)
    out = tmp() / "o.pdf"
    assert redact.apply(p, out, [_box(1, [100, 340, 520, 410])]).ok and not anywhere(out, ID)
    # still referenced from elsewhere (a tagged PDF's structure tree): emptied, not only dropped from /Fields
    d = fitz.open(p)
    d.xref_set_key(d.pdf_catalog(), "StructTreeRoot", f"<</K[<</Type/OBJR/Obj {parent} 0 R>><</Type/OBJR/Obj {kid} 0 R>>]>>")
    p2 = _save(d); out = tmp() / "o2.pdf"
    assert redact.apply(p2, out, [_box(1, [100, 340, 520, 410])]).ok and not anywhere(out, ID)


def test_fields_on_other_pages_are_kept():
    d = fitz.open(); pg = d.new_page(); pg.insert_text((72, 72), "ID: " + ID); _field(pg)
    other = d.new_page(); other.insert_text((72, 72), "Question 1")
    _field(other, value="Answer here", rect=(72, 400, 300, 430), name="answer")
    p = _save(d); out = tmp() / "o.pdf"
    r = redact.apply(p, out, [_text(1, ID)])
    assert r.ok and not anywhere(out, ID)
    with fitz.open(out) as o:
        assert list(o[0].widgets()) == [] and [w.field_value for w in o[1].widgets()] == ["Answer here"]
        assert len(redact._refs(o, o.xref_get_key(o.pdf_catalog(), "AcroForm/Fields"))) == 1


def test_notes_ink_and_free_text_on_a_redacted_page_are_gone():
    d = fitz.open(); pg = d.new_page(width=600, height=800); pg.insert_text((72, 72), "Question 1")
    pg.add_text_annot((500, 500), "note " + ID)                                  # a sticky note: get_text never reads it
    pg.add_ink_annot([[(100, 600), (150, 650), (200, 600), (250, 650)]])         # a signature drawn with a pen tool
    pg.add_freetext_annot(fitz.Rect(72, 400, 300, 430), "Student " + ID, fontsize=12)
    p = _save(d); out = tmp() / "o.pdf"
    r = redact.apply(p, out, [_box(1, [150, 730, 450, 830]), _text(1, ID)])     # the box covers the ink
    assert not r.ok or not anywhere(out, ID)
    r = redact.apply(p, out, [_box(1, [150, 730, 450, 830])])
    assert r.ok and not anywhere(out, ID) and "Question 1" in text(out, 1)
    with fitz.open(out) as o:
        assert list(o[0].annots()) == [] and list(o[0].widgets()) == []
    assert mean_pixel(out, 1, (0.2, 0.76, 0.38, 0.8)) < 20                       # the ink is not drawn over the black


def test_a_note_or_an_author_on_another_page_is_emptied():
    # final review: every committed PDF loses its annotations' authors and contents (scrub_pdf), so a note or an
    # author on a page without a redaction no longer fails the file: it is emptied, and the strings are gone
    for add in (lambda pg: pg.add_text_annot((500, 500), "note " + ID),
                lambda pg: pg.add_highlight_annot(fitz.Rect(72, 60, 200, 76)).set_info(title="Tan Ah Kow")):
        d = fitz.open(); d.new_page().insert_text((72, 72), "Name: Tan Ah Kow  ID: " + ID)
        pg = d.new_page(); pg.insert_text((72, 72), "Question 1"); add(pg)
        p = _save(d); out = tmp() / "o.pdf"
        r = redact.apply(p, out, [_text(1, ID), _text(1, "Tan Ah Kow")])
        assert r.ok and not anywhere(out, ID) and not anywhere(out, "Tan Ah Kow")
        with fitz.open(out) as o:
            assert len(list(o[1].annots())) >= 1                                  # the annotation itself stays
    d = fitz.open(); d.new_page().insert_text((72, 72), "ID: " + ID)            # drawn text is still caught
    d.new_page().add_freetext_annot(fitz.Rect(72, 400, 300, 430), "Student " + ID, fontsize=12)
    p = _save(d); out = tmp() / "o.pdf"
    assert not redact.text_gone(p, [_text(1, ID)])                              # the check reads what the note draws
    r = redact.apply(p, out, [_text(1, ID)])                                    # and the search finds it: blacked out
    assert r.ok and not anywhere(out, ID)


def test_outline_page_labels_and_page_extras_are_stripped():
    d = fitz.open(); d.new_page().insert_text((72, 72), "ID: " + ID); d.new_page().insert_text((72, 72), "Question 1")
    d.set_toc([[1, "Answers of " + ID, 1], [1, "Part 2", 2]])                    # page objects reload here
    tx = d.get_new_xref(); d.update_object(tx, "<</Type/XObject/Subtype/Image/Width 2/Height 2/ColorSpace/DeviceGray/BitsPerComponent 8>>")
    d.update_stream(tx, ID.encode()[:4]); d.xref_set_key(d[0].xref, "Thumb", f"{tx} 0 R")
    mx = d.get_new_xref(); d.update_object(mx, "<</Type/Metadata/Subtype/XML>>"); d.update_stream(mx, f"<x>{ID}</x>".encode())
    d.xref_set_key(d[0].xref, "Metadata", f"{mx} 0 R")
    d.xref_set_key(d[1].xref, "PieceInfo", f"<</App<</Private({ID})>>>>")       # on the page without a redaction
    d.xref_set_key(d.pdf_catalog(), "PageLabels", f"<</Nums[0<</P({ID})>>]>>")
    p = _save(d)
    assert anywhere(p, ID)
    out = tmp() / "o.pdf"
    assert redact.apply(p, out, [_text(1, ID)]).ok and not anywhere(out, ID)
    with fitz.open(out) as o:
        assert o.get_toc() == [] and o.xref_get_key(o.pdf_catalog(), "PageLabels")[0] == "null"
        assert all(o.xref_get_key(page.xref, k)[0] == "null" for page in o for k in ("Thumb", "Metadata", "PieceInfo"))


def test_the_word_fallback_matches_whole_words_only():
    d = fitz.open(); pg = d.new_page()
    pg.insert_text((150, 72), "KOW,"); pg.insert_text((72, 72), "Tan Ah")      # read as "KOW, / Tan Ah": not found whole
    pg.insert_text((72, 120), "Find the important constant of integration")
    p = _save(d); out = tmp() / "o.pdf"
    r = redact.apply(p, out, [_text(1, "Tan Ah Kow")])
    t = text(out, 1)
    assert r.ok and "Tan" not in t and "KOW" not in t                            # any case, edge punctuation trimmed
    assert "Find the important constant of integration" in t                     # "tan" inside words is left alone


def test_text_gone_validates_and_checks_fallback_words_only_when_given():
    p = pdf_pages(["Question 1", "Signed: Kow"])
    assert not redact.text_gone(p, None) and not redact.text_gone(p, {"page": 1, "kind": "text", "text": "x"})
    for bad in (_text(3, "Tan Ah Kow"), _text(0, "x"), dict(_text(1, "x"), kind="blur"), _box(1, [1, 2, 3]), "Tan", None):
        assert not redact.text_gone(p, [bad]), bad
    for bad in ("Kow", [None], ["  "], [3], {"Kow": 1}):
        assert not redact.text_gone(p, [], fallback_words=bad), bad
    assert redact.text_gone(p, [_box(1, [0, 0, 100, 100])]) and redact.text_gone(p, [])
    assert redact.text_gone(p, [_text(1, "Tan Ah Kow")])                        # a single word of it: not checked
    assert not redact.text_gone(p, [_text(1, "Tan Ah Kow")], fallback_words=["Tan", "Kow"])   # unless the fallback used it
    assert redact.text_gone(pdf_pages(["Kowloon", "the important constant"]), [], fallback_words=("Tan", "Kow"))  # whole words
    assert not redact.text_gone(pdf_pages(["Signed: KOW."]), [], fallback_words=["Kow"])    # in any case


def test_a_common_surname_elsewhere_does_not_fail_a_full_match():
    p = pdf_pages(["Name: Tan Ah Kow   Lecturer: Dr Tan", "Prepared by Dr Tan", "TAN TABLE: Sin Cos Tan"])
    out = tmp() / "o.pdf"
    r = redact.apply(p, out, [_text(1, "Tan Ah Kow")])                          # found whole: no word fallback
    assert r.ok and r.problems == [] and "Tan Ah Kow" not in text(out, 1)
    assert "Dr Tan" in text(out, 1) and "Dr Tan" in text(out, 2)


def test_a_fallback_after_a_hyphenated_break_still_checks_its_words():
    page = "Name: Tan Ah-\nKow end"                                              # not found whole: the fallback runs
    out = tmp() / "o.pdf"
    r = redact.apply(pdf_pages([page]), out, [_text(1, "Tan Ah Kow")])
    assert r.ok and "Tan" not in text(out, 1) and "Kow" not in text(out, 1)
    r = redact.apply(pdf_pages([page, "Signed: Kow"]), tmp() / "o.pdf", [_text(1, "Tan Ah Kow")])
    assert not r.ok and any("page 2" in x for x in r.problems) and "Kow" not in " ".join(r.problems)


def test_a_stale_output_is_deleted_whenever_nothing_is_written():
    p = pdf_pages(["ID: " + ID])
    junk = tmp() / "x.pdf"; junk.write_bytes(b"not a pdf")
    for src, reds in ((p, [_text(5, ID)]), (p, "no list"), (junk, [_text(1, ID)]), (tmp() / "missing.pdf", [])):
        out = tmp() / "o.pdf"; out.write_bytes(b"stale output of an earlier run " + ID.encode())
        r = redact.apply(src, out, reds)
        assert not r.ok and r.out is None and not out.exists(), reds
    out = tmp() / "o.pdf"; out.write_bytes(b"stale")
    real = fitz.Page.apply_redactions
    fitz.Page.apply_redactions = lambda *a, **k: (_ for _ in ()).throw(RuntimeError(ID))
    try:
        r = redact.apply(p, out, [_text(1, ID)])
    finally:
        fitz.Page.apply_redactions = real
    assert not r.ok and r.out is None and not out.exists() and ID not in " ".join(r.problems)
    out = tmp() / "o.pdf"; out.write_bytes(b"stale")
    r = redact.apply(p, out, [], cached_rects=[{"page": 2, "rect": [0, 0, 1, 1]}])
    assert not r.ok and not out.exists()


def test_review_focus_2_an_id_broken_over_lines_is_refused():
    for layout in ("ID: EEE2109\n300 end", "ID: EEE2109-\n300 end"):
        p = pdf_pages([layout])
        assert not redact.text_gone(p, [_text(1, ID)]), layout                  # the check sees it in the original
        r = redact.apply(p, tmp() / "o.pdf", [_text(1, ID)])
        assert not r.ok and ID not in " ".join(r.problems), layout


# ---- fix round 3: XFA in a direct /AcroForm, refs nested in direct annotations, fallback words in any case

def _two_field_pages(indirect_form=False):
    """Page 1: a field holding the ID; page 2: a field to keep; XFA datasets holding the ID. /AcroForm stays a direct
    dict (as PyMuPDF writes it) unless `indirect_form`."""
    d = fitz.open(); pg = d.new_page(); pg.insert_text((72, 72), "Question 1"); _field(pg)
    p2 = d.new_page(); p2.insert_text((72, 72), "Question 2"); _field(p2, value="Answer", rect=(72, 400, 300, 430), name="ans")
    xs = d.get_new_xref(); d.update_object(xs, "<<>>"); d.update_stream(xs, f"<xfa:datasets><sid>{ID}</sid></xfa:datasets>".encode())
    cat = d.pdf_catalog()
    if indirect_form:
        ax = d.get_new_xref(); d.update_object(ax, d.xref_get_key(cat, "AcroForm")[1]); d.xref_set_key(cat, "AcroForm", f"{ax} 0 R")
        d.xref_set_key(ax, "XFA", f"[(datasets) {xs} 0 R]")
    else:
        d.xref_set_key(cat, "AcroForm/XFA", f"[(datasets) {xs} 0 R]")
    return _save(d)


def test_xfa_goes_from_a_direct_or_indirect_form_with_a_field_kept():
    for indirect in (False, True):
        p = _two_field_pages(indirect)
        with fitz.open(p) as s:
            assert s.xref_get_key(s.pdf_catalog(), "AcroForm")[0] == ("xref" if indirect else "dict") and anywhere(p, ID)
        out = tmp() / "o.pdf"
        r = redact.apply(p, out, [_box(1, [100, 340, 520, 410])])
        assert r.ok and not anywhere(out, ID), indirect
        with fitz.open(out) as o:
            assert [w.field_value for w in o[1].widgets()] == ["Answer"], indirect
            assert o.xref_get_key(o.pdf_catalog(), "AcroForm/XFA")[0] == "null", indirect


def test_refs_inside_a_direct_annotation_are_not_followed():
    d = fitz.open(); pg = d.new_page(); pg.insert_text((72, 72), "ID: " + ID); pg.insert_text((72, 120), "Question 1")
    d.new_page().insert_text((72, 72), "Question 2")
    px = d[0].xref
    d.xref_set_key(px, "Annots", f"[<</Type/Annot/Subtype/Text/Rect[500 500 520 520]/Contents({ID})/P {px} 0 R>>]")
    p = _save(d); out = tmp() / "o.pdf"
    r = redact.apply(p, out, [_text(1, ID)])
    assert r.ok and not anywhere(out, ID)
    with fitz.open(out) as o:
        assert o.page_count == 2 and "Question 1" in o[0].get_text() and "Question 2" in o[1].get_text()
        assert o.xref_get_key(o[0].xref, "Type")[1] == "/Page" and list(o[0].annots()) == []
    r = redact.apply(p, tmp() / "o.pdf", [_text(1, "Zzyzx")])                       # not found: ok semantics unchanged
    assert not r.ok and any("page 1" in x for x in r.problems)


def test_only_top_level_array_entries_are_followed():
    arr = "[1 0 R <</P 4 0 R /Popup 5 0 R /Contents (a\\) 6 0 R) /AP <</N 12 0 R>> >> 7 0 R [8 0 R] (9 0 R \\( 11 0 R) <0A3F> 10 0 R]"
    assert redact._top_refs(arr) == [1, 7, 10] and redact._top_refs("<</P 4 0 R>>") == [] and redact._top_refs("[]") == []


def test_a_parent_named_inside_a_direct_widget_dict_is_not_emptied():
    d = fitz.open(); d.new_page().insert_text((72, 72), "ID: " + ID); p2 = d.new_page(); p2.insert_text((72, 72), "Question 2")
    kid = _field(p2, value="Answer", name="ans")
    parent = d.get_new_xref(); d.update_object(parent, f"<</FT/Tx/T(grp)/V(Answer)/Kids[{kid} 0 R]>>")
    for k, v in (("Parent", f"{parent} 0 R"), ("T", "null"), ("V", "null"), ("FT", "null")): d.xref_set_key(kid, k, v)
    d.xref_set_key(d.pdf_catalog(), "AcroForm/Fields", f"[{parent} 0 R]")
    d.xref_set_key(d[0].xref, "Annots", f"[<</Type/Annot/Subtype/Widget/Rect[72 300 300 330]/Parent {parent} 0 R>>]")
    p = _save(d); out = tmp() / "o.pdf"
    r = redact.apply(p, out, [_text(1, ID)])
    assert r.ok and not anywhere(out, ID)
    with fitz.open(out) as o:                                                    # page 2's field keeps its parent's value
        assert [w.field_value for w in o[1].widgets()] == ["Answer"]


def test_a_page_or_the_page_tree_listed_as_an_annotation_or_field_is_never_emptied():
    d = fitz.open(); d.new_page().insert_text((72, 72), "ID: " + ID); d.new_page().insert_text((72, 72), "Question 2")
    _field(d[1], value="Answer", name="ans")
    cat = d.pdf_catalog(); pages = int(d.xref_get_key(cat, "Pages")[1].split()[0]); p1, p2 = d[0].xref, d[1].xref
    d.xref_set_key(p1, "Annots", f"[{p1} 0 R {pages} 0 R {p2} 0 R]")              # a broken file: pages as annotations
    fields = d.xref_get_key(cat, "AcroForm/Fields")[1]
    d.xref_set_key(cat, "AcroForm/Fields", fields[:-1] + f" {pages} 0 R {p1} 0 R]")
    p = _save(d); out = tmp() / "o.pdf"
    r = redact.apply(p, out, [_text(1, ID)])
    assert r.ok and not anywhere(out, ID)
    with fitz.open(out) as o:
        assert o.page_count == 2 and "Question 2" in o[1].get_text() and [w.field_value for w in o[1].widgets()] == ["Answer"]
        assert all(o.xref_get_key(pg.xref, "Type")[1] == "/Page" for pg in o)


def test_a_fallback_word_in_another_case_on_another_page_fails():
    d = fitz.open(); pg = d.new_page(); pg.insert_text((150, 72), "Kow,"); pg.insert_text((72, 72), "Tan Ah")
    d.new_page().insert_text((72, 72), "Signed: KOW")
    p = _save(d)
    r = redact.apply(p, tmp() / "o.pdf", [_text(1, "Tan Ah Kow")])
    assert not r.ok and any("page 2" in x for x in r.problems) and "KOW" not in " ".join(r.problems)


# ---- final review: what every committed PDF loses (redacted or not), an /Annot without a Subtype

NAME = "Tan Ah Kow"
HIGHLIGHT = fitz.Rect(72, 60, 200, 76)


def _annotated():
    """Page 1: a highlight (author, subject, contents and rich text naming the student), a sticky note holding the ID,
    a file attachment, a direct annotation dict, piece info and a thumbnail; page 2: an annotation only the structure
    tree points at. Plus an embedded file, catalog piece info, an outline entry, XMP and an author."""
    d = fitz.open(); pg = d.new_page(); pg.insert_text((72, 72), "Question 1 highlight me")
    h = pg.add_highlight_annot(HIGHLIGHT); h.set_info(title=NAME, subject=NAME, content=ID); h.set_popup(fitz.Rect(300, 300, 400, 400))
    d.xref_set_key(h.xref, "RC", f"(<body><p>{NAME}</p></body>)")
    note = pg.add_text_annot((500, 500), "note " + ID); note.set_info(title=NAME)
    pg.add_file_annot((500, 600), ID.encode(), "notes.txt", desc=NAME)
    px = pg.xref
    annots = d.xref_get_key(px, "Annots")[1]
    d.xref_set_key(px, "Annots", annots[:-1] + f" <</Type/Annot/Subtype/Square/Rect[100 400 200 450]/C[1 0 0]/T({NAME})/Contents({ID})>>]")
    d.xref_set_key(px, "PieceInfo", f"<</App<</Private({ID})>>>>")
    tx = d.get_new_xref(); d.update_object(tx, "<</Type/XObject/Subtype/Image/Width 2/Height 2/ColorSpace/DeviceGray/BitsPerComponent 8>>")
    d.update_stream(tx, ID.encode()[:4]); d.xref_set_key(px, "Thumb", f"{tx} 0 R")
    p2 = d.new_page(); p2.insert_text((72, 72), "Question 2")
    orphan = d.get_new_xref(); d.update_object(orphan, f"<</Type/Annot/Subtype/Text/Rect[72 300 90 320]/Contents({ID})/T({NAME})>>")
    se = d.get_new_xref(); d.update_object(se, f"<</Type/StructElem/S/Annot/Pg {p2.xref} 0 R/K<</Type/OBJR/Obj {orphan} 0 R>>>>")
    st = d.get_new_xref(); d.update_object(st, f"<</Type/StructTreeRoot/K[{se} 0 R]>>")
    cat = d.pdf_catalog()
    d.xref_set_key(cat, "StructTreeRoot", f"{st} 0 R"); d.xref_set_key(cat, "PieceInfo", f"<</App<</Private({NAME})>>>>")
    d.embfile_add("x.txt", ID.encode(), desc=NAME)
    d.set_toc([[1, "Part 1", 1]])
    d.set_metadata({"author": NAME}); d.set_xml_metadata(f"<x:xmpmeta xmlns:x='adobe:ns:meta/'><dc>{NAME}</dc></x:xmpmeta>")
    return _save(d)


def _yellow(path, rect=HIGHLIGHT):
    """(mean red, mean blue) over the inside of `rect` on page 1: a highlight is drawn when red stays high, blue drops."""
    with fitz.open(path) as d:
        pix = d[0].get_pixmap(dpi=72, colorspace=fitz.csRGB, clip=rect + (8, 3, -8, -3))
    px = [pix.pixel(x, y) for y in range(pix.height) for x in range(pix.width)]
    return sum(p[0] for p in px) / len(px), sum(p[2] for p in px) / len(px)


def test_scrub_pdf_empties_what_no_committed_pdf_keeps_and_keeps_what_is_drawn():
    p = _annotated()
    assert anywhere(p, ID) and anywhere(p, NAME)
    red, blue = _yellow(p)
    assert red > 200 and blue < 120                                             # the highlight is drawn
    with fitz.open(p) as d:
        redact.scrub_pdf(d)
        out = tmp() / "o.pdf"; d.save(out, garbage=3, deflate=True)
    assert not anywhere(out, ID) and not anywhere(out, NAME)
    red, blue = _yellow(out)
    assert red > 200 and blue < 120                                             # still drawn
    with fitz.open(out) as o:
        kinds = [a.type[1] for a in o[0].annots()]
        assert "Highlight" in kinds and "Text" in kinds and "FileAttachment" not in kinds and o.embfile_names() == []
        assert o.get_toc() == [[1, "Part 1", 1]] and "Question 1 highlight me" in o[0].get_text()   # outline and text stay
        assert all(o.xref_get_key(pg.xref, k)[0] == "null" for pg in o for k in ("Thumb", "PieceInfo"))
        assert o.xref_get_key(o.pdf_catalog(), "PieceInfo")[0] == "null" and o.get_xml_metadata() == ""
        assert not any(o.metadata.get(k) for k in ("author", "title", "subject", "keywords", "creator", "producer"))


def test_scrub_pdf_passes_over_objects_mupdf_cannot_load():
    p = _annotated()                                    # a real upload had xref entries MuPDF cannot load ("cannot find object")
    with fitz.open(p) as d:
        cat, real = d.pdf_catalog(), redact._annotation
        redact._annotation = lambda doc, x, keep_out: (_ for _ in ()).throw(RuntimeError("cannot find object")) \
            if x == cat else real(doc, x, keep_out)
        try:
            redact.scrub_pdf(d)
        finally:
            redact._annotation = real
        out = tmp() / "o.pdf"; d.save(out, garbage=3)
    assert not anywhere(out, ID) and not anywhere(out, NAME)


def test_scrub_pdf_keeps_form_field_names():
    d = fitz.open(); pg = d.new_page(); pg.insert_text((72, 72), "Question 1"); _field(pg, value="Answer here", name="answer")
    p = _save(d)
    with fitz.open(p) as d:
        redact.scrub_pdf(d)
        out = tmp() / "o.pdf"; d.save(out, garbage=3)
    with fitz.open(out) as o:
        assert [(w.field_name, w.field_value) for w in o[0].widgets()] == [("answer", "Answer here")]


def test_a_redacted_file_gets_the_same_scrub_on_its_other_pages():
    p = _annotated()
    two = p.with_name("two.pdf")
    with fitz.open(p) as d:                                                     # the student's ID in page 2's text too
        d[1].insert_text((72, 120), "ID: " + ID); d.save(two)
    out = tmp() / "o.pdf"
    r = redact.apply(two, out, [_text(2, ID)])                                  # page 1 keeps its annotations, emptied
    assert r.ok and not anywhere(out, ID) and not anywhere(out, NAME)
    red, blue = _yellow(out)
    assert red > 200 and blue < 120


def test_an_annot_without_a_subtype_is_emptied_with_the_page():
    d = fitz.open(); pg = d.new_page(); pg.insert_text((72, 72), "ID: " + ID); d.new_page().insert_text((72, 72), "Q2")
    a = d.get_new_xref(); d.update_object(a, f"<</Type/Annot/Rect[72 300 300 330]/Contents({ID})>>"); px = d[0].xref
    d.xref_set_key(px, "Annots", f"[{a} 0 R]")
    se = d.get_new_xref(); d.update_object(se, f"<</Type/StructElem/S/Link/Pg {px} 0 R/K<</Type/OBJR/Obj {a} 0 R>>>>")
    st = d.get_new_xref(); d.update_object(st, f"<</Type/StructTreeRoot/K[{se} 0 R]>>")
    d.xref_set_key(d.pdf_catalog(), "StructTreeRoot", f"{st} 0 R")
    p = _save(d)
    with fitz.open(p) as s:
        assert redact._ours(s, a, set())
    out = tmp() / "o.pdf"
    fitz.TOOLS.mupdf_display_errors(False)              # MuPDF complains it cannot draw an annotation without a subtype
    try:
        r = redact.apply(p, out, [_text(1, ID)])
    finally:
        fitz.TOOLS.mupdf_display_errors(True)
    assert r.ok and not anywhere(out, ID)


# ---- exact repeats on other pages are blacked out; a one-word key there must not be split by spaces to count

def _key(page, s): return dict(_text(page, s), what="key")


def _raw_page(d, stream: bytes):
    """A page drawn by `stream` (text set in /helv, the font insert_text adds)."""
    pg = d.new_page(); pg.insert_text((72, 72), "x")
    d.update_stream(pg.get_contents()[0], stream)


def test_an_exact_repeat_on_another_page_is_blacked_out_too():
    p = pdf_pages(["ID: EEE2109300", "Question 1", "Footer ID:EEE2109300", "Name: Tan Ah Kow", "Header: TAN AH KOW."])
    out = tmp() / "o.pdf"
    r = redact.apply(p, out, [_text(1, ID), _text(4, "Tan Ah Kow")])            # each named on one page only
    assert r.ok and r.problems == [] and not anywhere(out, ID)
    assert "Question 1" in text(out, 2) and "Footer" in text(out, 3) and "Header" in text(out, 5)
    assert "TAN" not in text(out, 5) and "KOW" not in text(out, 5)               # any case, edge punctuation trimmed
    assert sorted({x["page"] for x in r.rects}) == [1, 3, 4, 5]


def test_repeats_on_rotated_pages_land_on_the_words():
    for rot in (90, 180, 270):
        p = pdf_pages(["ID: EEE2109300 Name: Tan Ah Kow", "Footer EEE2109300 keep this\nSigned Tan Ah Kow keep that"],
                      rotate=rot)
        out = tmp() / "o.pdf"
        r = redact.apply(p, out, [_text(1, ID), _text(1, "Tan Ah Kow")])
        t = text(out, 2)
        assert r.ok and ID not in t and "Kow" not in t and "keep this" in t and "keep that" in t, (rot, t)


def test_a_name_spaced_differently_on_another_page_still_fails():
    cases = []
    d = fitz.open(); d.new_page().insert_text((72, 72), "Student: Hassan Ali")
    _raw_page(d, b"q BT 1 0 0 1 72 700 Tm /helv 14 Tf 6 Tc (Hassan Ali) Tj ET Q")              # letter-spaced heading
    cases.append((_save(d), "Hassan Ali"))
    d = fitz.open(); d.new_page().insert_text((72, 72), "Student: Hassan Ali")
    _raw_page(d, b"q BT 1 0 0 1 72 700 Tm /helv 11 Tf [(Has) -300 (san Ali)] TJ ET Q")          # a kerning gap
    cases.append((_save(d), "Hassan Ali"))
    cases.append((pdf_pages(["Student: Hassan Ali", "Contact: hassanali@student.xmu.edu.my"]), "Hassan Ali"))
    cases.append((pdf_pages(["Student: Muhammad Hassanuddin", "Group: Muhammad Hassanud\ndin"]), "Muhammad Hassanuddin"))
    cases.append((pdf_pages(["Student: Hassan", "Leader: Has san"]), "Hassan"))
    for p, name in cases:
        for what in ("name", "other", "key") if " " in name else ("name", "other"):   # a handle can be "other"
            r = redact.apply(p, tmp() / "o.pdf", [dict(_text(1, name), what=what)])
            assert not r.ok and any("page 2" in x for x in r.problems), (name, what, r.problems)


def test_a_key_on_another_page_is_not_an_ordinary_phrase_with_its_space():
    p = pdf_pages(["Topic: Blue Sky Thinking", "Access code: BlueSky", "Week 3: Blue\nSky methods"])
    out = tmp() / "o.pdf"
    r = redact.apply(p, out, [_key(2, "BlueSky")])                              # the key is a phrase without its space
    assert r.ok and r.problems == [] and "BlueSky" not in text(out, 2)
    assert "Blue Sky Thinking" in text(out, 1) and "Sky methods" in text(out, 3)
    assert redact.text_gone(out, [_key(2, "BlueSky")])
    assert not redact.text_gone(out, [_text(2, "BlueSky")])                     # as a name it still counts
    assert not redact.text_gone(pdf_pages(["Key: x", "Topic: bluesky."]), [_key(1, "BlueSky")])     # not split: found
    assert not redact.text_gone(pdf_pages(["Key: x", "Topic: Blue-\nSky"]), [_key(1, "BlueSky")])   # hyphen break: found


def test_on_its_own_page_or_with_digits_spacing_is_still_ignored():
    r = redact.apply(pdf_pages(["Access code: BlueSky   Topic: Blue Sky"]), tmp() / "o.pdf", [_key(1, "BlueSky")])
    assert not r.ok and any("page 1" in x for x in r.problems) and "BlueSky" not in " ".join(r.problems)
    assert not redact.text_gone(pdf_pages(["Question 1", "Footer EEE 2109300"]), [_key(1, ID)])
    assert not redact.text_gone(pdf_pages(["Question 1", "Name: Tan Ah\nKow"]), [_text(1, "Tan Ah Kow")])


def test_repeats_of_single_words_short_strings_and_keys_are_left_for_a_person():
    p = pdf_pages(["Student: Siti Aminah", "Leader: SITI AMINAH.", "Siti Aminahwati binti Omar"]); out = tmp() / "o.pdf"
    r = redact.apply(p, out, [_text(1, "Siti Aminah")])
    assert "AMINAH" not in text(out, 2) and "Siti Aminahwati binti Omar" in text(out, 3)   # whole words only
    assert not r.ok and any("page 3" in x for x in r.problems) and not any("page 2" in x for x in r.problems)
    for reds, pages, kept in (([_text(1, "Crystal")], ["Name: Crystal", "The crystal oscillator"], "crystal oscillator"),
                              ([_text(1, "12A")], ["Seat: 12A", "Table 12A"], "Table 12A"),
                              ([_key(1, "Class 4471920")], ["Class 4471920", "Join class 4471920"], "4471920")):
        out = tmp() / "o.pdf"                                                   # one word, too short, a key: not repeated
        r = redact.apply(pdf_pages(pages), out, reds)
        assert kept in text(out, 2) and not r.ok and any("page 2" in x for x in r.problems), reds


def test_a_string_named_on_many_pages_is_looked_for_once():
    pages = [f"Question {i}\nFooter: Tan Ah Kow {ID}" for i in range(1, 31)]
    reds = [x for i in range(1, 31) for x in (_text(i, "Tan Ah Kow"), _text(i, ID))]
    r = redact.apply(pdf_pages(pages), tmp() / "o.pdf", reds)                   # every page names both: no repeats
    assert r.ok and len(r.rects) == 60
    r = redact.apply(pdf_pages([f"ID: {ID}", f"ID: {ID}", f"Footer {ID}"]), tmp() / "o.pdf", [_text(1, ID), _text(2, ID)])
    assert r.ok and [x["page"] for x in r.rects] == [1, 2, 3]                   # page 3 blacked out once


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests: t()
    print(f"ok {len(tests)} tests")
