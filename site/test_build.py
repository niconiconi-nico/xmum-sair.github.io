"""Checks for site/build.py. Run from the repo root: python site/test_build.py"""
import hashlib, sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
ROOT = pathlib.Path(__file__).resolve().parents[1]
import build

# expectations derived from the repo itself, so the suite survives new uploads
N_COURSES = sum(1 for prog in build.PROGS for d in (ROOT / prog).glob("*_*") if d.is_dir())
N_FILES = sum(1 for prog in build.PROGS for f in (ROOT / prog).glob("*_*/*/*/*")
              if f.is_file() and not f.name.startswith(".") and f.parent.name in build.SUBS
              and (build.SESSION_RE.fullmatch(f.parent.parent.name) or f.parent.parent.name == "unknown"))


def test_parse_taken():
    t = build.parse_taken("| 2022-04 | [COM107](COM107_Circuits_and_Devices/) | x |\n"
                          "| 2022-02 | [COM107](COM107_Circuits_and_Devices/) | x |\n"
                          "| 2022-04 | COM999 | x |\n")
    assert t["COM107"] == ["2022-02", "2022-04"] and t["COM999"] == ["2022-04"]


def test_parse_lecturers_link_and_plain():
    md = ("# X\n\n| 学期 / Session | 老师 / Lecturer | 考核 | 来自 | 备注 |\n|---|---|---|---|---|\n"
          "| [2022-04](2022-04/) | Chua Ming Yam |  | L | n |\n"
          "| 2025-04 | Dr. A |  | J | n |\n"
          "| 2026-09 |  |  | J | n |\n")
    assert build.parse_lecturers(md) == {"2022-04": "Chua Ming Yam", "2025-04": "Dr. A"}


def test_scan_real_repo():
    courses = build.scan(ROOT)
    assert len(courses) == N_COURSES >= 58 and {c.prog for c in courses} >= {"EEE"}
    c = next(c for c in courses if c.code == "COM107")
    assert c.name == "Circuits and Devices" and c.path == "EEE/COM107_Circuits_and_Devices"
    assert list(c.sessions) == ["2022-04"]
    assert list(c.sessions["2022-04"]) == ["Past-Papers", "Tutorials", "Handbooks"]
    assert len(c.sessions["2022-04"]["Tutorials"]) == 5
    assert c.taken_in == ["2022-04"] and c.lecturers["2022-04"] == "Chua Ming Yam"
    files = build.all_files(courses)
    assert len(files) == N_FILES >= 482 and all((ROOT / f.path).is_file() for f in files)
    g = next(c for c in courses if c.code == "G0191")                 # G electives and MPU subjects sit under GE/
    assert g.prog == "GE" and g.path == "GE/G0191_Face_Recognition_with_Machine_Learning" and g.taken_in == ["2023-02"]
    assert not [c.code for c in courses if c.prog != "GE" and c.code.startswith(("G", "MPU"))]
    com110 = next(c for c in courses if c.code == "COM110")   # sessions newest first, whatever has been uploaded since
    assert list(com110.sessions) == sorted(com110.sessions, reverse=True) and {"2026-09", "2025-04"} <= set(com110.sessions)


P = "/XMUM-SAIR-Survival-Guide/"


def _site():
    return build.Site(ROOT, P)


def test_slugify_github_style():
    assert build.slugify("欢迎上传 | What to upload", "-") == "欢迎上传--what-to-upload"
    assert build.slugify("修课时间表 | Course timeline", "-") == "修课时间表--course-timeline"


def test_rewrite_link_rules():
    s = _site()
    assert build.rewrite_link(s, "CONTRIBUTING.md", "README.md") == P + "contributing/"
    assert build.rewrite_link(s, "../CONTRIBUTING.md#署名--credit", "EEE/README.md") == P + "contributing/#署名--credit"
    assert build.rewrite_link(s, "COM107_Circuits_and_Devices/", "EEE/README.md") == P + "EEE/COM107_Circuits_and_Devices/"
    assert build.rewrite_link(s, "../EEE/README.md", "AIT/README.md") == P + "EEE/"
    assert build.rewrite_link(s, "2022-04/Tutorials/", "EEE/COM107_Circuits_and_Devices/README.md") == P + "EEE/COM107_Circuits_and_Devices/#2022-04"
    assert build.rewrite_link(s, "../COM108_Engineering_Mathematics_I/", "EEE/COM110_Engineering_Mathematics_I/README.md") == P + "EEE/COM108_Engineering_Mathematics_I/"
    assert build.rewrite_link(s, "scripts/update_index.py", "README.md") == build.GH + "/blob/main/scripts/update_index.py"
    assert build.rewrite_link(s, "https://github.com/nsd-chen", "EEE/README.md") == "https://github.com/nsd-chen"
    assert build.rewrite_link(s, "#top", "README.md") == "#top"
    assert build.rewrite_link(s, "CONTRIBUTORS.md", "README.md") == P + "about/#contributors"
    assert build.rewrite_link(s, "Lecturers.md", "README.md") == P + "reviews/#lecturers"
    assert build.rewrite_link(s, "../GE/G0191_Face_Recognition_with_Machine_Learning/", "EEE/README.md") == P + "GE/G0191_Face_Recognition_with_Machine_Learning/"
    assert build.rewrite_link(s, "../EEE999_X/", "EEE/COM107_Circuits_and_Devices/README.md") == build.GH + "/blob/main/EEE/EEE999_X"


def test_render_md_rewrites_hrefs_and_ids():
    s = _site()
    html = build.render_md(s, "## 欢迎上传 | What to upload\n\nSee [rules](../CONTRIBUTING.md) and [x](../EEE/README.md).\n", "AIT/README.md")
    assert 'id="欢迎上传--what-to-upload"' in html
    assert 'href="/XMUM-SAIR-Survival-Guide/contributing/"' in html and 'href="/XMUM-SAIR-Survival-Guide/EEE/"' in html
    table = build.render_md(s, "| a | b |\n|---|---|\n| 1 | 2 |\n", "README.md")
    assert '<div class="table-wrap"><table>' in table and "</table></div>" in table


def test_contributing_toc_anchors_resolve():
    import re
    s = _site()
    html = build.render_md(s, (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8"), "CONTRIBUTING.md")
    ids = set(re.findall(r'id="([^"]+)"', html))
    for frag in re.findall(r'href="#([^"]+)"', html):
        assert frag in ids, frag


import json
import re
import tempfile


def _build(prefix=P):
    out = pathlib.Path(tempfile.mkdtemp()) / "_site"
    return build.build(ROOT, out, prefix), out


def _course_pages(out):
    return [p for p in out.glob("*/*_*/index.html") if build.REDIRECT_MARK not in p.read_text(encoding="utf-8")]


def test_build_writes_every_page():
    site, out = _build()
    for p in ["index.html", "404.html", "about/index.html", "disclaimer/index.html", "qa/index.html", "courses/index.html",
              "upload/index.html", "contributing/index.html", "opportunities/index.html", "news/index.html", "research/index.html", "EEE/index.html", "AIT/index.html",
              "ROBO/index.html", "GE/index.html", "EEE/COM107_Circuits_and_Devices/index.html",
              "GE/G0191_Face_Recognition_with_Machine_Learning/index.html", "search.json", "static/style.css", "static/search.js"]:
        assert (out / p).is_file(), p
    assert len(site.courses) == len(_course_pages(out)) == N_COURSES
    # addresses that moved keep working: the folded pages and the G/MPU courses that used to sit under EEE/
    for old, new in [("contributors/", "about/#contributors"), ("lecturers/", "reviews/#lecturers"),
                     ("EEE/G0191_Face_Recognition_with_Machine_Learning/", "GE/G0191_Face_Recognition_with_Machine_Learning/")]:
        html = (out / old / "index.html").read_text(encoding="utf-8")
        assert build.REDIRECT_MARK in html and f'url={P}{new}"' in html, old


def test_course_page_contents():
    _, out = _build()
    html = (out / "EEE/COM107_Circuits_and_Devices/index.html").read_text(encoding="utf-8")
    assert 'data-mode="course"' in html and 'data-course="COM107"' in html and "giscus" not in html
    assert 'id="2022-04"' in html and "Chua Ming Yam" in html
    assert build.GH + "/blob/main/EEE/COM107_Circuits_and_Devices/2022-04/Tutorials/" in html
    assert build.RAW + "/EEE/COM107_Circuits_and_Devices/2022-04/Tutorials/" in html
    assert "请勿张贴答案" in html and "Student-run, not affiliated with XMUM" in html and "Ciallo～ (∠・ < )⌒☆" in html


def test_course_page_order_and_cleanup():
    """Course review first, then the files, then the details from the README, then Q&A; README boilerplate stays out."""
    _, out = _build()
    html = (out / "EEE/COM208_Engineering_Mathematics_IV/index.html").read_text(encoding="utf-8")
    at = [html.index(x) for x in ('id="review"', 'id="files"', 'id="details"', 'id="qa"')]
    assert at == sorted(at), at
    assert "每个修读学期一行" not in html and "One row per session" not in html and "小贴士 / Tips" not in html
    details = html[html.index('id="details"'):html.index('id="qa"')]
    assert "Mounir Nisse" in details and "LucasXQY" in details and "2023-04" in details
    assert "考核 / Assessment" not in details and "备注 / Notes" not in details      # columns empty in every row are hidden
    assert ">Past-Papers</a>" not in details and ">2021-09</a>" not in details        # no folder links; the files section has them
    com106 = (out / "EEE/COM106_Electrical_Engineering/index.html").read_text(encoding="utf-8")
    assert "COM111" in com106[:com106.index('id="review"')] and '<h2 id="qa">讨论 | Discussion</h2>' in com106                        # the code-change note stays at the top
    assert "习题仅保留题目" in com106[com106.index('id="details"'):]


def test_prog_and_qa_pages():
    _, out = _build()
    eee = (out / "EEE/index.html").read_text(encoding="utf-8")
    assert "<!-- COURSE-INDEX -->" not in eee
    n_eee = sum(1 for d in (ROOT / "EEE").glob("*_*") if d.is_dir())
    assert len(set(re.findall(r'href="/XMUM-SAIR-Survival-Guide/EEE/[^"/]+_[^"/]+/"', eee))) == n_eee
    ait = (out / "AIT/index.html").read_text(encoding="utf-8")                      # AIT now has courses of its own
    n_ait = sum(1 for d in (ROOT / "AIT").glob("*_*") if d.is_dir())
    assert len(set(re.findall(r'href="/XMUM-SAIR-Survival-Guide/AIT/[^"/]+_[^"/]+/"', ait))) == n_ait
    assert ("暂无课程" in ait) == (n_ait == 0)
    # the README's instructions for contributors stay on GitHub; the timeline folds away, one link per row
    assert "update_index.py" not in eee and "还没有你的表" not in eee and "文件命名" not in eee
    assert '<details class="timeline">' in eee and "修课时间表" in eee
    assert not re.search(r"<td><a [^>]*>COM106</a></td>", eee)
    ge = (out / "GE/index.html").read_text(encoding="utf-8")
    ge = ge[ge.index("<h2>G 开头"):]                                   # the course tables, after the recently-added box
    assert "G0191" in ge and "MPU1.1" in ge and ge.index("G0191") < ge.index("MPU1.1") and "MPU" in ge and "选修" in ge
    qa = (out / "qa/index.html").read_text(encoding="utf-8")
    assert "<h1>讨论区 | Discussion</h1>" in qa and 'data-mode="full"' in qa and "giscus" not in qa


def test_search_index():
    _, out = _build()
    idx = json.loads((out / "search.json").read_text(encoding="utf-8"))
    assert len(idx["courses"]) == N_COURSES and len(idx["files"]) == N_FILES
    f = next(f for f in idx["files"] if f["code"] == "COM107" and f["type"] == "Tutorials")
    assert f["preview"].startswith(build.GH + "/blob/main/") and f["download"].startswith(build.RAW + "/")
    assert (ROOT / f["path"]).is_file()
    assert any(f["preview"] is None for f in idx["files"] if f["name"].endswith(".docx"))
    c = next(c for c in idx["courses"] if c["code"] == "COM107")
    assert c["lecturers"] == ["Chua Ming Yam"] and c["url"] == P + "EEE/COM107_Circuits_and_Devices/"
    ng = next(l for l in idx["lecturers"] if l["name"] == "Ng Zi Neng")            # lecturers are searchable on /reviews/
    assert ng["slug"] == "ng-zi-neng" and "COM111" in ng["codes"]


def test_root_prefix_preview():
    _, out = _build("/")
    html = (out / "EEE/index.html").read_text(encoding="utf-8")
    assert 'href="/EEE/COM107_Circuits_and_Devices/"' in html and 'data-base="/"' in html
    assert 'href="/XMUM-SAIR-Survival-Guide/' not in html


def test_check_passes_on_real_repo():
    site, out = _build()
    build.check(site, out)          # must not raise; misplaced content only warns
    assert all(isinstance(p, str) for p in build.stragglers(site))


def test_stragglers_reports_misplaced_file():
    import shutil, subprocess
    tmp = pathlib.Path(tempfile.mkdtemp()) / "repo"
    for p in ["README.md", "DISCLAIMER.md", "CONTRIBUTING.md", "CONTRIBUTORS.md", "Lecturers.md",
              "Research-Opportunities.md", "EEE/README.md", "AIT/README.md", "ROBO/README.md",
              "EEE/COM107_Circuits_and_Devices/README.md"]:
        (tmp / p).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / p, tmp / p)
    (tmp / "EEE/COM107_Circuits_and_Devices/2024-9/Notes").mkdir(parents=True)
    (tmp / "EEE/COM107_Circuits_and_Devices/2024-9/Notes/x.pdf").write_bytes(b"%PDF")
    (tmp / "EEE/COM107_Circuits_and_Devices/stray.pdf").write_bytes(b"%PDF")
    (tmp / "EEE/EEE999_No_Readme/2024-09/Notes").mkdir(parents=True)
    (tmp / "EEE/EEE999_No_Readme/2024-09/Notes/ok.pdf").write_bytes(b"%PDF")
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp, check=True, capture_output=True)
    import contextlib, io
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        site = build.Site(tmp, "/")
    assert "WARNING: EEE/EEE999_No_Readme: README.md missing" in err.getvalue()
    assert [f.path for f in build.all_files(site.courses)] == ["EEE/EEE999_No_Readme/2024-09/Notes/ok.pdf"]
    assert sorted(build.stragglers(site)) == ["EEE/COM107_Circuits_and_Devices/2024-9/Notes/x.pdf",
                                             "EEE/COM107_Circuits_and_Devices/stray.pdf"]


def test_rewrite_link_loose_file_and_unindexed_session():
    import contextlib, io, shutil
    tmp = pathlib.Path(tempfile.mkdtemp()) / "repo"
    course = "EEE/COM107_Circuits_and_Devices"
    for p in ["EEE/README.md", f"{course}/README.md"]:
        (tmp / p).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / p, tmp / p)
    (tmp / course / "2022-04/Tutorials").mkdir(parents=True)
    (tmp / course / "2022-04/Tutorials/t1.pdf").write_bytes(b"%PDF")
    (tmp / course / "2023-04").mkdir()
    (tmp / course / "2023-04/Week 9 lecture.pdf").write_bytes(b"%PDF")   # loose file, no type folder
    s = build.Site(tmp, P)
    readme = f"{course}/README.md"
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        # a file sitting directly in a session folder is not a type folder: link to it on GitHub
        assert build.rewrite_link(s, "2023-04/Week 9 lecture.pdf", readme) == \
            build.GH + f"/blob/main/{course}/2023-04/Week%209%20lecture.pdf"
        # percent-encoded hrefs resolve to the same file, encoded once
        assert build.rewrite_link(s, "2023-04/Week%209%20lecture.pdf", readme) == \
            build.GH + f"/blob/main/{course}/2023-04/Week%209%20lecture.pdf"
    assert err.getvalue() == ""
    # a session folder with no indexed material has no anchor on the course page: send readers to GitHub
    with contextlib.redirect_stderr(err):
        assert build.rewrite_link(s, "2023-04/", readme) == build.GH + f"/tree/main/{course}/2023-04"
    assert "WARNING" in err.getvalue()
    # the normal case still anchors
    assert build.rewrite_link(s, "2022-04/Tutorials/", readme) == P + f"{course}/#2022-04"


def test_build_refuses_to_delete_a_foreign_directory():
    out = pathlib.Path(tempfile.mkdtemp()) / "not-a-site"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    try:
        build.build(ROOT, out, "/")
        assert False, "build() must refuse to wipe a directory it did not create"
    except SystemExit:
        pass
    assert (out / "keep.txt").is_file()


def test_forbidden_words_checked_in_generator_not_content():
    """A lecturer or file may legitimately be called Claude; only the generator's own files must stay clean."""
    import shutil
    tmp = pathlib.Path(tempfile.mkdtemp()) / "repo"
    course = "EEE/COM107_Circuits_and_Devices"
    for p in ["README.md", "DISCLAIMER.md", "CONTRIBUTING.md", "CONTRIBUTORS.md", "Lecturers.md", "Research-Opportunities.md",
              "EEE/README.md", "AIT/README.md", "ROBO/README.md", f"{course}/README.md"]:
        (tmp / p).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / p, tmp / p)
    (tmp / course / "2022-04/Tutorials").mkdir(parents=True)
    (tmp / course / "2022-04/Tutorials/Claude_Shannon_notes.pdf").write_bytes(b"%PDF")
    with open(tmp / course / "README.md", "a", encoding="utf-8") as fh:
        fh.write("\n- 小贴士 / Tips：read Claude Shannon's paper\n")
    out = pathlib.Path(tempfile.mkdtemp()) / "_site"
    import contextlib, io
    with contextlib.redirect_stderr(io.StringIO()):     # the partial copy has many dangling links; not the point here
        build.check(build.build(tmp, out, P), out)      # must not raise
    assert build.check_generator_sources() == []


UPLOAD_URL = "https://github.com/xmum-sair/xmum-sair.github.io/issues/new?template=upload.yml"


# A minimal DOM for site.js's /upload/ helper: only the ids it reads, submit handlers kept so the test can fire them.
UPLOAD_HARNESS = r"""
const fs = require('fs');
const els = {}, handlers = {};
function el(id, value) {
  return els[id] = { id, value: value || '', hidden: false, dataset: {}, textContent: '', firstChild: null,
    addEventListener(ev, fn) { (handlers[id + ':' + ev] = handlers[id + ':' + ev] || []).push(fn); },
    focus() {}, appendChild() {}, removeChild() {}, setAttribute() {}, getAttribute() { return null; }, contains() { return false; } };
}
const vals = JSON.parse(process.argv[3]);
for (const id of ['up-form', 'up-code', 'up-name', 'up-name-row', 'up-course-note', 'up-lect', 'up-lects', 'up-year', 'up-term',
                  'up-other', 'up-type', 'up-assess', 'up-assess-row', 'up-error']) el(id, vals[id]);
el('up-data').textContent = JSON.stringify({ courses: {}, lecturers: [] });
els['up-form'].dataset.form = 'https://github.com/o/r/issues/new?template=upload.yml';
global.document = { documentElement: el('html'), body: { dataset: {} }, querySelectorAll: () => [], querySelector: () => null,
  getElementById: id => els[id] || null, createElement: () => el('x'), addEventListener() {} };
global.window = {}; global.localStorage = { getItem() { throw new Error('blocked'); } };
global.location = { pathname: '/upload/', href: '' };
eval(fs.readFileSync(process.argv[2], 'utf8'));
for (const fn of handlers['up-form:submit'] || []) fn({ preventDefault() {} });
console.log(JSON.stringify({ href: location.href, error: els['up-error'].textContent }));
"""


def _submit_upload_helper(**values):
    """Fill the /upload/ helper with values by element id, press Next; (prefilled GitHub URL or "", error text)."""
    import shutil, subprocess
    node = shutil.which("node")
    if not node:
        return None
    harness = pathlib.Path(tempfile.mkdtemp()) / "harness.js"
    harness.write_text(UPLOAD_HARNESS, encoding="utf-8")
    r = subprocess.run([node, str(harness), str(ROOT / "site/static/site.js"), json.dumps(values)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    return out["href"], out["error"]


def test_upload_helper_needs_nothing_and_passes_hints_through():
    """Spec §4: every hint is optional and the GitHub form itself asks for the programme, so the helper asks for nothing.
    Empty hints are left out of the prefilled form; a hint in an odd shape goes through for the bot to tidy (issue #67)."""
    from urllib.parse import parse_qs, urlsplit
    out = _submit_upload_helper()
    if out is None:
        print("node not found: upload helper behaviour not checked")
        return
    href, error = out
    q = parse_qs(urlsplit(href).query)
    assert error == "" and q["template"] == ["upload.yml"] and "prog" not in q, (href, error)
    assert not {"code", "name", "session", "type", "lecturer", "assessment"} & set(q) and q["title"] == ["[上传 | Upload]"]
    href, error = _submit_upload_helper(**{"up-code": "com107", "up-name": "Circuits", "up-year": "2022",
                                           "up-term": "04", "up-type": "Past-Papers", "up-assess": "Final"})
    q = parse_qs(urlsplit(href).query)
    assert (q["code"], q["name"], q["session"], q["type"], q["assessment"]) == (["COM107"], ["Circuits"], ["2022-04"], ["Past-Papers"], ["Final"])
    assert q["title"] == ["[上传 | Upload] COM107 2022-04 Past-Papers"] and "prog" not in q
    href, error = _submit_upload_helper(**{"up-code": "swe 404", "up-term": "other", "up-other": "2024/04"})
    q = parse_qs(urlsplit(href).query)
    assert error == "" and q["code"] == ["SWE404"] and q["session"] == ["2024/04"], (href, error)     # the bot tidies it
    href, error = _submit_upload_helper(**{"up-year": "2022"})          # a year without a term names no month: left out
    assert error == "" and "session" not in parse_qs(urlsplit(href).query), (href, error)


def test_upload_page_suggests_courses_and_lecturers():
    """GitHub forms cannot suggest values, so /upload/ does: known courses and lecturers as you type, anything new is
    taken as new, and the answers ride along to the GitHub form, where only the files are left to add."""
    _, out = _build()
    up = (out / "upload/index.html").read_text(encoding="utf-8")
    assert f'data-form="{UPLOAD_URL}"' in up and 'list="up-codes"' in up and 'list="up-lects"' in up
    assert '<option value="COM107">Circuits and Devices</option>' in up and '<option value="Wong Shen Yuong">' in up
    assert 'id="up-year"' in up and 'id="up-term"' in up and all(f'<option value="{t}">' in up for t in ("02", "04", "09"))
    data = json.loads(re.search(r'<script type="application/json" id="up-data">(.*?)</script>', up, flags=re.S).group(1))
    assert data["courses"]["COM106"]["lecturers"] == ["Wong Shen Yuong"] and data["courses"]["G0191"]["prog"] == "GE"
    assert "Wong Shen Yuong" in data["lecturers"]
    assert 'id="up-prog"' not in up and up.index('<details class="up-hints">') < up.index('id="up-code"')   # nothing to fill up front
    assert "逐个分拣" in up and "Sorts each file" in up and "自动涂黑" in up and "Blacks out" in up    # what the bot does
    assert "任何人都能下载" in up and "anyone can download" in up        # what it cannot do: attachments are public
    js = (out / "static/site.js").read_text(encoding="utf-8")
    assert "up-data" in js and "URLSearchParams" in js and "up-name-row" in js
    for p in ["index.html", "EEE/index.html", "contributing/index.html", "wanted/index.html"]:
        assert f'href="{P}upload/"' in (out / p).read_text(encoding="utf-8"), p


def test_nav_is_blocks_with_courses_menu():
    """Blocks with icons; one Courses block opens a row with the programmes and GE instead of three separate blocks;
    Lecturers and Contributors are folded into Reviews and About."""
    _, out = _build()
    html = (out / "index.html").read_text(encoding="utf-8")
    nav = re.search(r'<nav class="nav-blocks"[^>]*>(.*?)</nav>', html, flags=re.S)
    assert nav, "nav.nav-blocks missing"
    blocks = re.findall(r'<a class="nb[^"]*"[^>]*>', nav.group(1))
    assert len(blocks) == len(build.NAV), blocks
    order = [re.search(r'href="([^"]*)"', b).group(1) for b in blocks]     # maintainer's order, 2026-11-06: contribute and upload last
    assert order == [P + "courses/", P + "qa/", P + "reviews/", P + "opportunities/", P + "about/", build.GH,
                     P + "contributing/", P + "upload/"], order
    assert P + "lecturers/" not in nav.group(1) and P + "contributors/" not in nav.group(1) and P + "EEE/" not in nav.group(1)
    assert 'aria-controls="nav-courses"' in blocks[0] and 'aria-expanded="false"' in blocks[0]
    sub = re.search(r'<div class="nav-sub" id="nav-courses" hidden>(.*?)</div>', html, flags=re.S)
    assert sub and all(f'href="{P}{p}/"' in sub.group(1) for p in ("EEE", "AIT", "ROBO", "GE")) and f'href="{P}courses/"' in sub.group(1)
    assert nav.group(1).count('class="nb-ic"') == len(blocks) and nav.group(1).count('class="nb-t"') == len(blocks)
    assert "<small>Discussion</small>" in nav.group(1) and "讨论" in nav.group(1) and "问答" not in nav.group(1)
    assert "<small>Opportunities</small>" in nav.group(1) and 'class="nb upload"' in nav.group(1) and "<small>仓库</small>" in nav.group(1)
    css = (out / "static/style.css").read_text(encoding="utf-8")
    assert ".nb:hover" in css and "transition" in css and "prefers-reduced-motion" in css and ".nav-sub" in css
    js = (out / "static/site.js").read_text(encoding="utf-8")
    assert "aria-current" in js and "aria-expanded" in js and "Escape" in js
    courses = (out / "courses/index.html").read_text(encoding="utf-8")
    assert all(f'href="{P}{p}/"' in courses for p in ("EEE", "AIT", "ROBO", "GE"))


# ---- phase 3

def test_theme_toggle_and_data_theme_css():
    _, out = _build()
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'class="theme-toggle"' in html and "localStorage.getItem('theme')" in html   # no-flash inline script
    css = (out / "static/style.css").read_text(encoding="utf-8")
    assert ':root[data-theme="dark"]' in css and ':root:not([data-theme="light"])' in css
    js = (out / "static/site.js").read_text(encoding="utf-8")
    assert "giscus" not in js and "localStorage.setItem('theme'" in js




def test_recent_uploads_and_feed():
    site, out = _build()
    entries = build.recent_entries(site)
    assert entries and entries == sorted(entries, key=lambda e: e["date"], reverse=True)
    e = entries[0]
    assert set(e) >= {"date", "code", "name", "session", "type", "n", "url", "by"} and re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["date"])
    home = (out / "index.html").read_text(encoding="utf-8")
    assert 'class="recent' not in home and 'type="application/atom+xml"' in home   # recent uploads live on programme pages now
    prog = (out / f"{e['prog']}/index.html").read_text(encoding="utf-8")
    assert "最近新增 | Recently added" in prog and e["code"] in prog and prog.count("<li><time") <= 5
    feed = (out / "feed.xml").read_text(encoding="utf-8")
    assert feed.startswith("<?xml") and "<feed" in feed and "<entry>" in feed and build.SITE_URL in feed
    assert (out / "recent/index.html").is_file()
    import contextlib, io, shutil                                           # a tree without git history: no entries, no crash
    tmp = pathlib.Path(tempfile.mkdtemp()) / "repo"
    for p in ["README.md", "EEE/README.md", "AIT/README.md", "ROBO/README.md"]:
        (tmp / p).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / p, tmp / p)
    with contextlib.redirect_stderr(io.StringIO()):
        assert build.recent_entries(build.Site(tmp, P)) == []


def test_wanted_page_sitemap_and_canonical():
    site, out = _build()
    wanted = (out / "wanted/index.html").read_text(encoding="utf-8")
    assert "征集榜" in wanted and all(c.code in wanted for c in site.courses if not c.sessions)
    no_papers = [c for c in site.courses if c.sessions and not any("Past-Papers" in t for t in c.sessions.values())]
    assert "缺空白试卷" in wanted and no_papers and all(c.code in wanted for c in no_papers)
    gaps = [(c.code, s) for c in site.courses for s in c.taken_in if s not in c.sessions]
    assert gaps and all(f"{code} " in wanted and f"· {s}" in wanted for code, s in gaps[:5])
    sm = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert sm.count("<loc>") >= len(site.courses) + 8 and f"<loc>{build.SITE_URL}EEE/COM107_Circuits_and_Devices/</loc>" in sm
    assert "Sitemap:" in (out / "robots.txt").read_text(encoding="utf-8")
    assert f'<link rel="canonical" href="{build.SITE_URL}EEE/">' in (out / "EEE/index.html").read_text(encoding="utf-8")


def test_session_zip_download_links():
    _, out = _build()
    html = (out / "EEE/COM107_Circuits_and_Devices/index.html").read_text(encoding="utf-8")
    assert build.GH + "/releases/download/downloads/COM107_2022-04.zip" in html and "打包下载" in html


def test_ratings_hooks_and_reviews_page():
    _, out = _build()
    assert build.lecturer_slug("Dr. Chua Ming Yam") == build.lecturer_slug("chua  ming yam") == "chua-ming-yam"
    course = (out / "EEE/COM107_Circuits_and_Devices/index.html").read_text(encoding="utf-8")
    assert 'class="ratings" data-code="COM107"' in course and "评价这门课" in course
    # a lecturer named only in a review has no directory page: ratings.js links only the names listed in data-known
    known = re.search(r'data-known="([^"]*)"', course).group(1).split()
    assert "chua-ming-yam" in known and all((out / "lecturers" / k / "index.html").is_file() for k in known)
    assert known == sorted({build.lecturer_slug(n) for n in build.lecturers(build.Site(ROOT, P))})
    rjs = (out / "static/ratings.js").read_text(encoding="utf-8")
    assert "dataset.known" in rjs and "data.courses[want" in rjs
    assert f'href="{P}rate/?code=COM107&amp;lecturer=Chua%20Ming%20Yam"' in course   # one known lecturer: prefilled
    lect = (out / "lecturers/chua-ming-yam/index.html").read_text(encoding="utf-8")
    assert 'class="ratings"' not in lect and "giscus" not in lect              # lecturer pages carry no scores and no threads
    table = (out / "reviews/index.html").read_text(encoding="utf-8")
    assert 'class="ratings" data-all' in table and 'id="rated"' in table and "已有评价的课程" in table
    assert "ratings.json" in (out / "static/ratings.js").read_text(encoding="utf-8") and f"{build.SITE_URL}reviews/" in (out / "sitemap.xml").read_text(encoding="utf-8")






def test_lecturer_pages_are_a_directory():
    """Lecturer pages list courses taught and link to the course review sections. No comment threads, no per-lecturer
    scores, no written reviews of lecturers anywhere (maintainer's decision, 2026-11-05). The directory sits on /reviews/."""
    site, out = _build()
    assert build.lecturer_slug("Lohgheswary A/P Nagarethinam") == "lohgheswary-a-p-nagarethinam"
    lects = build.lecturers(site)
    assert "Nurul Hafeeza Jumaah" in lects and "Maryani Ahmad" in lects and "Lohgheswary A/P Nagarethinam" in lects
    assert ("COM111", "2025-09") in lects["Ng Zi Neng"]
    idx = (out / "reviews/index.html").read_text(encoding="utf-8")
    assert 'id="lecturers"' in idx and "不收关于老师的文字评价" in idx and f'href="{P}lecturers/ng-zi-neng/"' in idx
    assert len(set(re.findall(rf'href="{P}lecturers/([^"/]+)/"', idx))) == len({build.lecturer_slug(n) for n in lects})
    pg = (out / "lecturers/ng-zi-neng/index.html").read_text(encoding="utf-8")
    assert "<h1>Ng Zi Neng</h1>" in pg and f'href="{P}EEE/COM111_Electrical_Engineering/#2025-09"' in pg
    assert f'href="{P}EEE/COM111_Electrical_Engineering/#review"' in pg          # scores live on the course page
    assert "giscus" not in pg and 'class="ratings"' not in pg and "同学描述" not in pg and "Rate this" not in pg
    course = (out / "EEE/COM111_Electrical_Engineering/index.html").read_text(encoding="utf-8")
    assert f'href="{P}lecturers/ng-zi-neng/"' in course                          # click the lecturer → their courses
    js = (out / "static/site.js").read_text(encoding="utf-8")
    assert "rate-q" not in js and "#profile" not in js and "Describe" not in js and "ratings-loaded" not in js
    rate = (out / "rate/index.html").read_text(encoding="utf-8")
    assert 'id="rate-q"' in rate and "写描述" not in rate and 'id="rate-form"' in rate
    for word in ("教师 | Lecturer:", "Lecturer Profiles", "教师档案"):
        assert word not in idx and word not in pg, word


def test_reviews_page_search_and_course_panel():
    """One add-review button, a search box for courses and lecturers, and a course panel (tiles + per-lecturer table)."""
    _, out = _build()
    table = (out / "reviews/index.html").read_text(encoding="utf-8")
    assert table.count("添加评价 | Add a review") == 1 and "评价这门课" not in table         # no second button for the same thing
    assert 'id="rv-q"' in table and 'id="rv-panel"' in table and 'data-courses="' + P + 'search.json"' in table
    assert f'data-form="{P}rate/?from=reviews"' in table                             # the panel's Rate button opens the site's form
    js = (out / "static/ratings.js").read_text(encoding="utf-8")
    assert "rv-panel" in js and "tile" in js and "data.courses[" in js and ".all" in js and "lecturers/" in js
    assert "box.dataset.form" in js and "formUrl" not in js
    assert "lcard" in js and "data-sort" not in js and "chips" in js       # rated courses as chips, lecturers as score cards
    assert "全部评分" not in table and "Click a header" not in table
    css = (out / "static/style.css").read_text(encoding="utf-8")
    base_tile = css.split("\n.tile {")[1][:300]                              # the full-size tile, not .tiles.mini .tile
    assert "--v" in base_tile and ".lcard {" in css and ".tiles.mini" in css


def test_about_page_holds_contributors():
    _, out = _build()
    about = (out / "about/index.html").read_text(encoding="utf-8")
    assert 'id="contributors"' in about and "niconiconi-nico" in about and "XHZ" in about and "MXZ" in about
    assert "[Name]" not in about and "[贡献 / Contribution]" not in about                # template rows stay on GitHub
    assert about.count("贡献者 | Contributors") == 1


def test_added_dates_follow_renames():
    """Moving a course folder (EEE/ → GE/) must not make its files look newly uploaded."""
    raw = ("2026-11-06\n\nR100\tEEE/G0103_X/2021-09/Syllabus/a.pdf\tGE/G0103_X/2021-09/Syllabus/a.pdf\n"
           "A\tGE/G0103_X/2021-09/Notes/new.pdf\n2026-11-04\n\nA\tEEE/G0103_X/2021-09/Syllabus/a.pdf\n")
    d = build.parse_added(raw)
    assert d["GE/G0103_X/2021-09/Syllabus/a.pdf"] == "2026-11-04" and d["GE/G0103_X/2021-09/Notes/new.pdf"] == "2026-11-06"


def _forum(html: str) -> tuple[dict, dict]:
    """The forum mount point's data attributes and its embedded JSON."""
    tag = re.search(r'<div class="forum-app" ([^>]*)>', html).group(1)
    attrs = dict(re.findall(r'data-([a-z]+)="([^"]*)"', tag))
    data = json.loads(re.search(r'<script type="application/json" class="forum-data">(.*?)</script>', html, re.S).group(1))
    return attrs, data


def test_discussion_area_needs_no_github():
    """The discussion area is served by the Worker: no giscus, no GitHub post links, the old posts linked as an archive."""
    _, out = _build()
    qa = (out / "qa/index.html").read_text(encoding="utf-8")
    attrs, data = _forum(qa)
    assert attrs["mode"] == "full" and attrs["forum"] == build.WORKER_URL + "/forum" and attrs["sitekey"] == build.TURNSTILE_SITE_KEY
    boards = json.loads((ROOT / "discussions/boards.json").read_text(encoding="utf-8"))["boards"]
    assert [b["key"] for b in data["boards"]] == [b["key"] for b in boards] and all(b["zh"] in qa for b in boards)
    assert data["courses"]["COM107"]["url"] == P + "EEE/COM107_Circuits_and_Devices/"
    assert "discussions/new" not in qa and "giscus" not in qa and "不用登录" in qa and "举报" in qa
    assert build.GH + "/discussions" in qa                                       # the old posts, read-only
    assert qa.count('class="forum-app"') == 1 and "static/forum.js" in qa
    for page_file in out.rglob("*.html"):
        assert "giscus" not in page_file.read_text(encoding="utf-8"), page_file
    assert (out / "static/forum.js").is_file()


def test_course_home_and_wanted_pages_carry_the_forum():
    _, out = _build()
    com107 = (out / "EEE/COM107_Circuits_and_Devices/index.html").read_text(encoding="utf-8")
    attrs, _ = _forum(com107[com107.index('id="qa"'):])
    assert attrs["mode"] == "course" and attrs["course"] == "COM107"
    home = (out / "index.html").read_text(encoding="utf-8")
    assert _forum(home)[0]["mode"] == "preview" and "board-app" in home        # the opportunity wall keeps its own mount point
    wanted = (out / "wanted/index.html").read_text(encoding="utf-8")
    assert _forum(wanted)[0]["mode"] == "requests"


def test_forum_data_cannot_close_its_script_tag():
    site = build.Site(ROOT, P)
    site.courses[0].name = "</script><b>x"
    html = build.forum_html(site, "full")
    # the JSON ends at the first </script>: it parses only if the name's </script> was escaped
    assert json.loads(re.search(r'class="forum-data">(.*?)</script>', html, re.S).group(1))["courses"][site.courses[0].code]["name"] == "</script><b>x"


def test_worker_boards_match_the_site():
    """assistant/src/forum.js keeps its own copy of the board keys; the build's self-check compares them."""
    keys = [b["key"] for b in json.loads((ROOT / "discussions/boards.json").read_text(encoding="utf-8"))["boards"]]
    assert build.forum_board_keys() == keys
    fake = pathlib.Path(tempfile.mkdtemp()) / "forum.js"
    fake.write_text('export const BOARD_HINTS = {\n  study: "x",\n  other: "y",\n};\n', encoding="utf-8")
    assert build.forum_board_keys(fake) == ["study", "other"]


def test_search_index_carries_topics():
    """The topic index (topics/topics.py) makes file contents searchable: Fourier finds the notes about it."""
    rel = "EEE/COM107_Circuits_and_Devices/2022-04/Tutorials/" + sorted(
        p.name for p in (ROOT / "EEE/COM107_Circuits_and_Devices/2022-04/Tutorials").iterdir())[0]
    topics = {rel: {"sha": "x", "topics": ["基尔霍夫定律 Kirchhoff's laws", "节点分析 nodal analysis"], "about": "电路习题 | circuit problems"}}
    out = pathlib.Path(tempfile.mkdtemp()) / "_site"
    build.build(ROOT, out, P, topics=topics)
    idx = json.loads((out / "search.json").read_text(encoding="utf-8"))
    f = next(f for f in idx["files"] if f["path"] == rel)
    assert f["topics"] == ["基尔霍夫定律 Kirchhoff's laws", "节点分析 nodal analysis"] and f["about"] == "电路习题 | circuit problems"
    assert all("topics" not in g for g in idx["files"] if g["path"] != rel)          # files without topics stay small
    js = (out / "static/search.js").read_text(encoding="utf-8")
    assert "f.topics" in js and "知识点" in js


def test_helper_window_appears_only_when_configured():
    """The chat window (static/assistant.js) needs the Worker address and the Turnstile site key; BOTS_PAUSED hides it."""
    import os
    keys = ("ASSISTANT_URL", "TURNSTILE_SITE_KEY", "BOTS_PAUSED")
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        _, out = _build()
        assert 'id="helper"' not in (out / "index.html").read_text(encoding="utf-8")
        os.environ.update({"ASSISTANT_URL": "https://helper.example.workers.dev", "TURNSTILE_SITE_KEY": "0x4AAA"})
        _, out = _build()
        for p in ["index.html", "EEE/COM107_Circuits_and_Devices/index.html", "404.html"]:
            html = (out / p).read_text(encoding="utf-8")
            assert 'id="helper" data-url="https://helper.example.workers.dev" data-sitekey="0x4AAA"' in html, p
            assert f'src="{P}static/assistant.js?v=' in html, p
        js = (out / "static/assistant.js").read_text(encoding="utf-8")
        assert "turnstile" in js and "sessionStorage" in js and "xmum-sair\\.github\\.io" in js   # links only to the site
        os.environ["BOTS_PAUSED"] = "1"
        assert 'id="helper"' not in (_build()[1] / "index.html").read_text(encoding="utf-8")
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def test_reviews_are_submitted_on_the_site_without_github():
    """The rating form posts to the Worker (assistant/src/reviews.js): no GitHub account, a human check on every vote,
    ratings read from the Worker with the old data-branch file as a fallback (maintainer's decision, 2026-11-06)."""
    site, out = _build()
    rate = (out / "rate/index.html").read_text(encoding="utf-8")
    assert f'data-vote="{build.WORKER_URL}/vote"' in rate and f'data-sitekey="{build.TURNSTILE_SITE_KEY}"' in rate
    for dim in ("course", "instructor", "difficulty", "work", "learned", "communication", "access", "recommend"):
        assert f'name="{dim}"' in rate, dim
    assert 'id="rate-confirm"' in rate and "不需要 GitHub 账号" in rate and 'id="rate-year"' in rate and 'id="rate-term"' in rate
    assert f'src="{P}static/rate.js?v=' in rate
    js = (out / "static/rate.js").read_text(encoding="utf-8")
    assert "turnstile" in js and "localStorage" in js and "URLSearchParams" in js and "data-vote" not in js and "dataset.vote" in js
    course = (out / "EEE/COM107_Circuits_and_Devices/index.html").read_text(encoding="utf-8")
    assert f'data-src="{build.WORKER_URL}/ratings"' in course and f'data-fallback="{build.LEGACY_RATINGS_URL}"' in course
    assert "dataset.fallback" in (out / "static/ratings.js").read_text(encoding="utf-8")
    for page in out.rglob("*.html"):                                                  # the GitHub form is gone everywhere
        assert "template=review.yml" not in page.read_text(encoding="utf-8"), page


def test_goatcounter_snippet_on_every_page():
    _, out = _build()
    for p in ["index.html", "EEE/COM107_Circuits_and_Devices/index.html", "404.html"]:
        html = (out / p).read_text(encoding="utf-8")
        assert 'data-goatcounter="https://xmum-sair.goatcounter.com/count"' in html and "gc.zgo.at/count.js" in html, p
    assert "GoatCounter" in (out / "index.html").read_text(encoding="utf-8")   # the footer says so


def test_news_changelog():
    rel = build.parse_news("## v2 · 2026-11-05\n- 甲 | A\n- 乙 | B\n\n## v1 · 2026-11-04\n- 丙 | C\n")
    assert [r["version"] for r in rel] == ["v2", "v1"] and rel[0]["items"] == [("甲", "A"), ("乙", "B")]
    site, out = _build()
    real = build.news(site)
    assert real and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["date"]) and r["items"] for r in real)
    home = (out / "index.html").read_text(encoding="utf-8")
    assert 'class="news-strip"' in home and real[0]["version"] in home and home.count("<li>", home.index("news-strip")) >= 1
    page = (out / "news/index.html").read_text(encoding="utf-8")
    assert all(r["version"] in page for r in real)
    assert 'href="/' not in page.replace(f'href="{P}', "")    # root-relative links in news.md get the site prefix


def test_home_is_short_and_has_no_more_list():
    _, out = _build()
    home = (out / "index.html").read_text(encoding="utf-8")
    assert "更多 | More" not in home and 'ul class="links"' not in home
    assert f'href="{P}wanted/"' in home and f'href="{P}rate/"' in home            # the two links the header lacks
    assert 'class="board-app"' in home and 'data-preview="3"' in home and "static/board.js" in home


def test_opportunities_page_and_research_redirect():
    site, out = _build()
    curated = build.curated_opportunities(site)
    assert curated and all(c["title"] and not c["title"].startswith("[") for c in curated)    # template rows left out
    assert {c["cat"] for c in curated} <= {k for k, *_ in build.BOARD_CATS["opp"]}
    guide = build.research_guide(site)
    assert guide.startswith('<details class="board-how guide" open>') and "<strong>" in guide and "这一篇在网站上" not in guide
    page = (out / "opportunities/index.html").read_text(encoding="utf-8")
    assert 'data-compose="opp"' in page and 'data-compose="story"' not in page and build.WORKER_URL + "/board" in page
    assert f'href="{P}blog/?write=1"' in page and "科研入门" in page                 # experience posts are written on the blog now
    assert '"story"' not in page
    data = json.loads(re.search(r'<script type="application/json" class="board-data">(.*?)</script>', page, re.S).group(1))
    worker = (ROOT / "assistant/src/board.js").read_text(encoding="utf-8")
    for kind, cats in data["cats"].items():                                    # the site and the Worker agree on the keys
        keys = re.search(rf'{kind}: \[([^\]]*)\]', worker).group(1)
        assert [c["key"] for c in cats] == re.findall(r'"(\w+)"', keys), kind
    assert build.REDIRECT_MARK in (out / "research/index.html").read_text(encoding="utf-8")
    assert "Research-Opportunities.md" not in build.ROOT_PAGES and build.LINK_ALIASES["Research-Opportunities.md"] == "opportunities/"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"ok {len(tests)} tests")


def test_blog_page_and_tabs():
    """The blog sits next to the discussion area: its own page, the same Worker, its categories match the Worker's."""
    _, out = _build()
    blog = (out / "blog/index.html").read_text(encoding="utf-8")
    attrs, data = _forum(blog)
    assert attrs["mode"] == "blog" and attrs["forum"] == build.WORKER_URL + "/forum"
    assert [c["key"] for c in data["cats"]] == [k for k, *_ in build.BLOG_CATS] == build.forum_board_keys(name="BLOG_HINTS")
    assert "<h1>博客 | Blog</h1>" in blog and "最多 6 张" in blog and "1600" in blog
    qa = (out / "qa/index.html").read_text(encoding="utf-8")
    for page_html, here in ((qa, "qa"), (blog, "blog")):
        tabs = re.search(r'<nav class="switch".*?</nav>', page_html, re.S).group(0)
        assert f'href="{P}qa/"' in tabs and f'href="{P}blog/"' in tabs and f'href="{P}{here}/" aria-current="page"' in tabs
    assert "blog/" in (out / "sitemap.xml").read_text(encoding="utf-8")
    js = (out / "static/site.js").read_text(encoding="utf-8")
    assert "/qa/" in js and "blog" in js                                         # the Discussion block lights up on the blog
    forum_js = (out / "static/forum.js").read_text(encoding="utf-8")
    assert "innerHTML" not in forum_js and "insertAdjacentHTML" not in forum_js     # post text never becomes HTML


def test_static_files_are_versioned():
    """Each page asks for static/x.css?v=<hash of the file>, so a release never pairs new pages with cached old files."""
    _, out = _build()
    page_html = (out / "blog/index.html").read_text(encoding="utf-8")
    for name in ("style.css", "site.js", "forum.js"):
        v = hashlib.sha256((ROOT / "site/static" / name).read_bytes()).hexdigest()[:8]
        assert f'static/{name}?v={v}"' in page_html, name
    assert not re.search(r'static/[\w.-]+\.(css|js)"', page_html)                 # none left without a version
    assert (out / "static/style.css").is_file()
