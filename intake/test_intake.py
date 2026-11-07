"""Checks for intake/intake.py. Run from the repo root: python intake/test_intake.py (no network needed)."""
import json, re, sys, pathlib, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
ROOT = pathlib.Path(__file__).resolve().parents[1]
import intake

# the body GitHub renders from the issue form: "### <title>", blank line, value, blank line
SAMPLE_BODY = """### 专业 | Programme

EEE

### 课程代码 | Course code

com107

### 课程名称 | Course name

Circuits and Devices

### 学期 | Session

2022-04

### 资料类型 | Type

Past-Papers

### 文件 | Files

[final.pdf](https://github.com/user-attachments/files/111/final.pdf)
[bundle.zip](https://github.com/user-attachments/files/112/bundle.zip)
![shot](https://github.com/user-attachments/assets/0f0f0f0f-aaaa-bbbb-cccc-0123456789ab)

### 考核名称 | Assessment（试卷用）

Final

### 老师 | Lecturer

_No response_

### 考核比例 | Assessment weighting

_No response_

### 你修这门课的学期 | Session you took it

_No response_

### 署名方式 | Credit

用下面填的昵称 | The nickname below

### 昵称 | Nickname

Joyce

### 入学年份 | Intake year

2021

### 备注 / 小贴士 | Notes

_No response_

### 确认 | Checklist

- [x] {c0}
- [x] {c1}
- [x] {c2}
- [x] {c3}
"""


def _body(**over):
    b = SAMPLE_BODY.format(c0=intake.CHECKS[0], c1=intake.CHECKS[1], c2=intake.CHECKS[2], c3=intake.CHECKS[3])
    for k, v in over.items():
        b = b.replace(k, v)
    return b


def test_parse_form_fields():
    f = intake.parse_form(_body())
    assert (f.prog, f.code, f.name, f.session, f.type) == ("EEE", "COM107", "Circuits and Devices", "2022-04", "Past-Papers")
    assert f.links == [("final.pdf", "https://github.com/user-attachments/files/111/final.pdf"),
                       ("bundle.zip", "https://github.com/user-attachments/files/112/bundle.zip")]
    assert f.lecturer == "" and f.taken == "2022-04" and f.credit == intake.CREDIT_NICK and f.nickname == "Joyce" and f.intake == "2021"
    assert f.checked == [True, True, True, True]
    assert intake.validate(f) == []


def test_normalize_session_reads_common_ways_of_writing_a_month():
    for raw in ("2024-04", "2024-4", "2024/04", "2024/4", "2024.04", "2024 04", "202404", "2024_04", "2024-04-15",
                "04/2024", "4-2024", "Apr 2024", "april 2024", "2024 Apr", "APR-2024", "Apr. 2024", "2024年4月", "2024年04月",
                "２０２４／０４", " 2024 / 04 "):
        assert intake.normalize_session(raw) == "2024-04", raw
    assert intake.normalize_session("Sept 2025") == intake.normalize_session("September, 2025") == "2025-09"
    assert intake.normalize_session("Feb 2023") == "2023-02" and intake.normalize_session("Ogos 2023") == "2023-08"
    for raw in ("", "Y2S1", "last year", "2024", "2024/25", "2023/2024", "2024-13", "13/2024", "unknown", "不知道", "sem 2 2024"):
        assert intake.normalize_session(raw) == "", raw


def test_normalize_code_finds_the_course_code_in_what_was_typed():
    for raw, code in (("COM107", "COM107"), ("com 107", "COM107"), ("swe-404", "SWE404"), ("SWE404 Software Engineering", "SWE404"),
                      ("MPU3.1", "MPU3.1"), ("ｃｏｍ１０７", "COM107"), ("G0191", "G0191")):
        assert intake.normalize_code(raw) == code, raw
    for raw in ("", "TBD_1", "software engineering", "不知道"):
        assert intake.normalize_code(raw) == "", raw


def test_hints_are_tidied_or_ignored_never_sent_back():
    # issue #67: "2024/04" for the session you took it sent the upload back; hints are only hints, so none blocks
    f = intake.parse_form(_body(**{"com107": "swe 404", "2022-04": "2024/04"}))
    assert (f.code, f.session, f.taken) == ("SWE404", "2024-04", "2024-04") and intake.validate(f) == []
    g = intake.parse_form(_body(**{"com107": "TBD_1", "2022-04": "Y2S1"}))
    assert (g.code, g.session, g.taken) == ("", "", "") and intake.validate(g) == []


def test_personal_data_box_says_attachments_are_public_and_old_wording_still_counts():
    # the box says what the bot cannot do: an attachment is public from the moment it is uploaded (AI-PIPELINE.md §3)
    assert "任何人都能下载" in intake.CHECKS[1] and "can be downloaded by anyone" in intake.CHECKS[1]
    assert "机器人还会再查一遍" in intake.CHECKS[1]
    old = "没有学生姓名、学号、签名，没有老师的私人联系方式和 Moodle 密钥 | No student names, IDs or signatures; no lecturers' private contacts or Moodle keys"
    f = intake.parse_form(_body().replace(intake.CHECKS[1], old))     # an issue opened before the change still rebuilds
    assert f.checked == [True] * 4 and intake.validate(f) == []
    g = intake.parse_form(_body().replace("- [x] " + intake.CHECKS[1], "- [ ] " + intake.CHECKS[1]))
    assert any("Unticked" in p for p in intake.validate(g))


def test_validate_unticked_box_and_missing_nickname():
    body = _body().replace("- [x] " + intake.CHECKS[2], "- [ ] " + intake.CHECKS[2]).replace("Joyce", "_No response_")
    problems = intake.validate(intake.parse_form(body))
    assert any(intake.CHECKS[2] in p and "Unticked" in p for p in problems)
    assert any("Nickname" in p for p in problems)
    assert len(problems) == 2, problems


def test_form_course_fields_are_optional():
    f = intake.parse_form(_body().replace("com107", "_No response_").replace("2022-04", "_No response_"))
    assert intake.validate(f) == []
    g = intake.parse_form(_body().replace("com107", "_No response_").replace("Circuits and Devices", "_No response_")
                          .replace("Past-Papers", "_No response_").replace("2022-04", "_No response_"))
    assert intake.validate(g) == [] and (g.code, g.name, g.session, g.type, g.taken) == ("", "", "", "", "")
    vague = intake.parse_form(_body().replace("2022-04", "_No response_").replace(
        "### 你修这门课的学期 | Session you took it\n\n_No response_", "### 你修这门课的学期 | Session you took it\n\nlast year"))
    assert intake.validate(vague) == [] and vague.taken == ""     # a hint the bot cannot read is left out, not sent back


def test_unselected_optional_dropdowns_count_as_empty():
    # GitHub writes "None" (not "_No response_") for an optional dropdown left unselected (issue #61)
    body = _body()
    for title in ("资料类型 | Type", "入学年份 | Intake year"):
        body = re.sub(r"(### " + re.escape(title) + r"\n\n)[^\n]*", lambda m: m.group(1) + "None", body)
    f = intake.parse_form(body)
    assert (f.type, f.intake) == ("", "")
    assert intake.validate(f) == []
    yml = (ROOT / ".github/ISSUE_TEMPLATE/upload.yml").read_text(encoding="utf-8")
    ids = re.findall(r"^  - type: dropdown\n    id: (\w+)$", yml, re.M)
    assert sorted(ids) == sorted(intake.DROPDOWNS)        # every dropdown in the form gets the "None" rule


def test_issue_form_marks_course_fields_optional():
    yml = (ROOT / ".github/ISSUE_TEMPLATE/upload.yml").read_text(encoding="utf-8")
    blocks = {m.group(1): part for part in yml.split("\n  - type: ")[1:] for m in [re.search(r"^    id: (\w+)$", part, re.M)] if m}
    for fid in ("code", "name", "session", "type"):
        assert 'description: "选填，给机器人的提示 | optional, a hint for the bot"' in blocks[fid], fid
        assert "required: false" in blocks[fid] and "required: true" not in blocks[fid], fid
    for fid in ("prog", "files", "credit"):
        assert "required: true" in blocks[fid], fid
    assert "一次只选一种" not in yml and "一次提交只选一种资料类型" not in yml and "One type" not in yml


def test_attachment_links_accepts_only_file_urls():
    links = intake.attachment_links("[a.pdf](https://github.com/user-attachments/files/1/a.pdf) "
                                    "[b.pdf](https://github.com/xmum-sair/xmum-sair.github.io/files/2/b.pdf) "
                                    "[x](https://github.com/user-attachments/assets/abc) "
                                    "[a.pdf](https://github.com/user-attachments/files/1/a.pdf)")
    assert links == [("a.pdf", "https://github.com/user-attachments/files/1/a.pdf"),
                     ("b.pdf", "https://github.com/xmum-sair/xmum-sair.github.io/files/2/b.pdf")]


def test_no_files_message_mentions_images():
    f = intake.parse_form(_body().replace("[final.pdf](https://github.com/user-attachments/files/111/final.pdf)\n"
                                          "[bundle.zip](https://github.com/user-attachments/files/112/bundle.zip)\n", ""))
    assert any("PDF" in p for p in intake.validate(f))


def test_clean_label():
    assert intake.clean_label("COM105 Week 4 Lecture 4 Newton’s Laws of Motion") == "COM105_Week_4_Lecture_4_Newton_s_Laws_of_Motion"
    assert intake.clean_label(" (期末) Final—Set A ") == "Final_Set_A"


def test_unpack_zip_rules():
    import zipfile
    work = pathlib.Path(tempfile.mkdtemp())
    z = work / "bundle.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a/b.pdf", b"%PDF"); zf.writestr("notes.PDF", b"%PDF"); zf.writestr("../evil.pdf", b"%PDF")
        zf.writestr("__MACOSX/._x.pdf", b"x"); zf.writestr("sub/", b""); zf.writestr("pic.jpg", b"\xff\xd8"); zf.writestr(".hidden.pdf", b"%PDF")
    (work / "final.pdf").write_bytes(b"%PDF")
    ok, bad, origins = intake.unpack([("final.pdf", work / "final.pdf"), ("bundle.zip", z)], work)
    assert [n for n, _ in ok] == ["final.pdf", "b.pdf", "notes.PDF"] and all(p.is_file() for _, p in ok)
    # zip members are named "<zip> › <n>", n counting every non-directory, non-skipped entry (accepted and rejected alike)
    assert {n for n, _ in bad} == {"bundle.zip › 3", "bundle.zip › 4"} and all(" | " in r for _, r in bad)
    assert origins == ["final.pdf", "bundle.zip › 1", "bundle.zip › 2"]
    assert not any("evil" in n or "pic" in n for n, _ in bad)


def test_unpack_limits():
    work = pathlib.Path(tempfile.mkdtemp()); files = []
    for i in range(intake.MAX_FILES + 1):
        p = work / f"f{i}.pdf"; p.write_bytes(b"%PDF"); files.append((p.name, p))
    try:
        intake.unpack(files, work); assert False
    except intake.IntakeLimit as e:
        assert "60" in str(e)


def _pdf(path, text="Question 1: find x.", author="Someone"):
    import fitz
    d = fitz.open(); pg = d.new_page(); pg.insert_text((72, 72), text)
    d.set_metadata({"author": author, "title": "t"}); d.save(path); d.close()


def _docx(path, creator="Someone", body="Student ID: EEE2109300 answer scheme"):
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("docProps/core.xml", f'<cp:coreProperties xmlns:cp="x" xmlns:dc="y"><dc:creator>{creator}</dc:creator>'
                                        f'<cp:lastModifiedBy>{creator}</cp:lastModifiedBy><dc:title>T</dc:title></cp:coreProperties>')
        z.writestr("docProps/app.xml", "<Properties><Company>ACME</Company></Properties>")
        z.writestr("word/document.xml", f"<w:document><w:p><w:t>{body}</w:t></w:p></w:document>")


def test_clear_metadata_pdf_and_docx():
    import fitz, zipfile
    w = pathlib.Path(tempfile.mkdtemp()); _pdf(w / "a.pdf"); _docx(w / "b.docx")
    assert intake.clear_metadata(w / "a.pdf") == "cleared"
    assert not any(v for k, v in fitz.open(w / "a.pdf").metadata.items() if k not in ("format", "encryption"))
    assert intake.clear_metadata(w / "b.docx") == "cleared"
    core = zipfile.ZipFile(w / "b.docx").read("docProps/core.xml").decode()
    assert "<dc:creator></dc:creator>" in core and "Someone" not in core
    assert "ACME" not in zipfile.ZipFile(w / "b.docx").read("docProps/app.xml").decode()
    (w / "c.doc").write_bytes(b"\xd0\xcf"); assert "manually" in intake.clear_metadata(w / "c.doc")


def test_inspect_file_flags():
    w = pathlib.Path(tempfile.mkdtemp())
    _pdf(w / "clean.pdf", text="Question 1: find x. " * 10, author=""); _docx(w / "bad.docx")
    c = intake.inspect_file(w / "clean.pdf"); b = intake.inspect_file(w / "bad.docx")
    assert c["pages"] == 1 and c["pii"] == [] and c["answers"] == [] and c["low_text"] is False
    assert any("EEE2109300" in m for m in b["pii"]) and any("answer scheme" in m.lower() for m in b["answers"]) and b["meta_empty"] is False
    _pdf(w / "scan.pdf", text="x", author=""); assert intake.inspect_file(w / "scan.pdf")["low_text"] is True


def test_thumbnails_sheet():
    import fitz
    w = pathlib.Path(tempfile.mkdtemp()); _pdf(w / "a.pdf")
    d = fitz.open(); [d.new_page() for _ in range(3)]; d.save(w / "b.pdf"); d.close()     # only its first two pages are shown
    assert intake.thumbnails([w / "a.pdf", w / "b.pdf"], w / "sheet.png") == 3 and (w / "sheet.png").stat().st_size > 1000
    assert intake.thumbnails([], w / "none.png") == 0 and not (w / "none.png").exists()


def _repo_copy():
    import shutil
    tmp = pathlib.Path(tempfile.mkdtemp()) / "repo"
    for rel in ["README.md", "CONTRIBUTORS.md", "CONTRIBUTING.md", "DISCLAIMER.md", "Lecturers.md", "Research-Opportunities.md",
                "EEE/README.md", "AIT/README.md", "ROBO/README.md", "scripts/update_index.py", "EEE/COM107_Circuits_and_Devices/README.md",
                "GE/README.md", "GE/MPU3.1_Introduction_to_the_Constitution_of_Malaysia/README.md"]:
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True); shutil.copy(ROOT / rel, tmp / rel)
    src = ROOT / "EEE/COM107_Circuits_and_Devices/2022-04/Tutorials"
    (tmp / "EEE/COM107_Circuits_and_Devices/2022-04/Tutorials").mkdir(parents=True)
    shutil.copy(next(src.glob("*.pdf")), tmp / "EEE/COM107_Circuits_and_Devices/2022-04/Tutorials/t1.pdf")
    return tmp


def test_credit_name():
    f = intake.parse_form(_body()); assert intake.credit_name(f, "someone") == "Joyce"
    assert intake.credit_name(intake.parse_form(_body().replace(intake.CREDIT_NICK, intake.CREDIT_LOGIN)), "someone") == "[someone](https://github.com/someone)"
    assert intake.credit_name(intake.parse_form(_body().replace(intake.CREDIT_NICK, intake.CREDIT_ANON)), "someone") == "匿名 / anonymous"
    assert intake.credit_name(intake.parse_form(_body().replace("Joyce", "a|b")), "x") == "a\\|b"


def test_update_course_readme_existing_and_new_row():
    text = (ROOT / "EEE/COM107_Circuits_and_Devices/README.md").read_text(encoding="utf-8")
    f = intake.parse_form(_body()); out = intake.update_course_readme(text, f, "Joyce")
    row = next(l for l in out.splitlines() if l.startswith("| [2022-04]"))
    assert "LucasXQY · [nsd-chen](https://github.com/nsd-chen) · Joyce" in row and "[Past-Papers](2022-04/Past-Papers/)" in row and row.count("[Past-Papers](") == 1
    assert intake.update_course_readme(out, f, "Joyce") == out          # idempotent
    g = intake.parse_form(_body().replace("2022-04", "2025-09").replace("_No response_\n\n### 考核比例", "Dr. New\n\n### 考核比例"))
    out2 = intake.update_course_readme(text, g, "Joyce").splitlines()
    i = next(k for k, l in enumerate(out2) if l.startswith("| [2025-09]"))
    assert out2[i - 1].startswith("| [2022-04]") and "| Dr. New |" in out2[i] and "[Past-Papers](2025-09/Past-Papers/)" in out2[i]


def test_update_timeline_and_contributors():
    eee = (ROOT / "EEE/README.md").read_text(encoding="utf-8"); f = intake.parse_form(_body())
    out = intake.update_timeline(eee, f, "Joyce", "COM107_Circuits_and_Devices")
    assert "| 2022-04 | [COM107](COM107_Circuits_and_Devices/) | [Circuits and Devices](COM107_Circuits_and_Devices/) | Joyce |" in out
    assert intake.update_timeline(out, f, "Joyce", "COM107_Circuits_and_Devices") == out
    ait = (ROOT / "AIT/README.md").read_text(encoding="utf-8")
    assert "### 其他同学 | Other contributors" in intake.update_timeline(ait, f, "Joyce", "COM107_Circuits_and_Devices")
    c = (ROOT / "CONTRIBUTORS.md").read_text(encoding="utf-8")
    assert intake.update_contributors(c, f, "Joyce", 2) == c            # Joyce already listed
    out = intake.update_contributors(c, f, "[newbie](https://github.com/newbie)", 2).splitlines()
    i = next(k for k, l in enumerate(out) if l.startswith("| [newbie]"))
    assert "2021 入学（2025 届）" in out[i] and "2 份 COM107 2022-04 Past-Papers" in out[i] and out[i + 1].startswith("| [Name] | 2021")
    assert intake.update_contributors(c, intake.parse_form(_body().replace(intake.CREDIT_NICK, intake.CREDIT_ANON)), "匿名 / anonymous", 2) == c


def test_apply_decisions_keeps_a_course_in_its_programme_and_creates_new_ones():
    Decision, Check, Item = _decide_bits()
    tmp = _repo_copy(); w = pathlib.Path(tempfile.mkdtemp()); items = _mk_items(w, [(1, "a"), (2, "b")])
    f = intake.parse_form(_body().replace("EEE\n", "AIT\n"))         # an EEE course uploaded from the AIT form stays under EEE
    pl = intake.apply_decisions(tmp, f, [Decision(1, "commit", "COM107", "", "2022-04", "Past-Papers", "Final")], items, "someone", 1)
    assert pl.courses == ["EEE/COM107_Circuits_and_Devices"] and not list((tmp / "AIT").glob("COM107*"))
    assert pl.placed == [(1, "EEE/COM107_Circuits_and_Devices/2022-04/Past-Papers/COM107_202204_Final_Question.pdf")]
    assert any("EEE" in n for n in pl.notes) and (tmp / pl.placed[0][1]).is_file()
    assert "| 2022-04 | [COM107](COM107_Circuits_and_Devices/) |" in (tmp / "EEE/README.md").read_text(encoding="utf-8")
    g = intake.parse_form(_body().replace("com107", "EEE999").replace("Circuits and Devices", "New Course"))
    pl2 = intake.apply_decisions(tmp, g, [Decision(2, "commit", "EEE999", "New Course", "2022-04", "Past-Papers", "Final")], items, "someone", 2)
    assert pl2.created_readme and (tmp / "EEE/EEE999_New_Course/README.md").read_text(encoding="utf-8").startswith("# EEE999 New Course")
    assert "| EEE999 |" in (tmp / "EEE/README.md").read_text(encoding="utf-8")   # index regenerated


def test_shared_courses_live_under_ge():
    """G electives and MPU compulsory courses belong to every programme: they sit under GE/ whatever the form says,
    and the uploader's own programme timeline links to them."""
    Decision, Check, Item = _decide_bits()
    assert all(intake.is_shared(c) for c in ("G0191", "GE0227", "MPU3.1", "MPU1.1")) and not intake.is_shared("COM107")
    tmp = _repo_copy(); w = pathlib.Path(tempfile.mkdtemp()); items = _mk_items(w, [(1, "a"), (2, "b")])
    f = intake.parse_form(_body().replace("EEE\n", "AIT\n").replace("com107", "MPU3.1").replace("Circuits and Devices", "Constitution"))
    pl = intake.apply_decisions(tmp, f, [Decision(1, "commit", "MPU3.1", "", "2022-04", "Past-Papers", "Final")], items, "someone", 1)
    assert pl.courses == ["GE/MPU3.1_Introduction_to_the_Constitution_of_Malaysia"] and not list((tmp / "AIT").glob("MPU3.1*"))
    assert not any("already lives" in n for n in pl.notes)
    assert "| 2022-04 | [MPU3.1](../GE/MPU3.1_Introduction_to_the_Constitution_of_Malaysia/) |" in (tmp / "AIT/README.md").read_text(encoding="utf-8")
    ge = (tmp / "GE/README.md").read_text(encoding="utf-8")
    assert "2022-04" in next(l for l in ge.splitlines() if l.startswith("| MPU3.1 |"))   # index rerun, taken-in from the AIT timeline
    g = intake.parse_form(_body().replace("com107", "G0999").replace("Circuits and Devices", "New Elective"))
    pl2 = intake.apply_decisions(tmp, g, [Decision(2, "commit", "G0999", "New Elective", "2022-04", "Past-Papers", "Final")], items, "someone", 2)
    assert pl2.courses == ["GE/G0999_New_Elective"] and pl2.created_readme and not list((tmp / "EEE").glob("G0999*"))
    assert "| G0999 |" in (tmp / "GE/README.md").read_text(encoding="utf-8")
    eee = (tmp / "EEE/README.md").read_text(encoding="utf-8")
    assert "| 2022-04 | [G0999](../GE/G0999_New_Elective/) |" in eee and "| G0999 |" not in eee


SECTIONS = ("## 收录 | Committed", "## 不收 | Not included", "## 可能误判 | Maybe wrong", "## 需要你看 | Needs you",
            "## 打码记录 | Redactions", "## 登记 | Registered", "## 检查 | Checks", "## 本次 AI 花费 | AI spend this run",
            "## 维护者清单 | Maintainer checklist")


def _in_order(text, parts):
    at = [text.find(p) for p in parts]
    return -1 not in at and at == sorted(at)


def _tables_hold(text):
    """Every markdown table keeps its header's column count: no stray pipe from bilingual text splits a cell."""
    blocks, cur = [], []
    for l in text.splitlines() + [""]:
        if l.startswith("|"): cur.append(len(re.findall(r"(?<!\\)\|", l)))
        elif cur: blocks.append(cur); cur = []
    return bool(blocks) and all(len(set(b)) == 1 for b in blocks)


def test_dry_run_end_to_end():
    import os, shutil, subprocess, sys
    tmp = _repo_copy(); w = pathlib.Path(tempfile.mkdtemp()); _pdf(w / "final.pdf"); _docx(w / "sheet.docx")
    form = w / "form.md"; form.write_text(_body().replace("Past-Papers", "Tutorials").replace("Final", "_No response_"), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}             # no key: the stand-in AI
    r = subprocess.run([sys.executable, str(ROOT / "intake/intake.py"), "--dry-run", "--form", str(form), "--files", str(w / "final.pdf"), str(w / "sheet.docx"),
                        "--repo", str(tmp), "--login", "tester", "--issue", "7", "--work", str(w / "work")],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr
    body = (w / "work/PR_BODY.md").read_text(encoding="utf-8")
    assert "EEE/COM107_Circuits_and_Devices/2022-04/Tutorials/COM107_202204_Tutorials.pdf" in body
    assert "EEE2109300" not in body                                       # a keyword hit is counted, never quoted
    assert "python site/build.py" in body and "/approve #k" in body and "/retry" in body
    docx = tmp / "EEE/COM107_Circuits_and_Devices/2022-04/Tutorials/COM107_202204_Tutorials.docx"
    if shutil.which("soffice") or shutil.which("libreoffice"):           # the check reads the converted PDF
        assert _in_order(body, SECTIONS + ("Closes #7",)), body
        assert "COM107_202204_Tutorials.docx" in body and docx.is_file()
    else:                                                                 # nothing for Claude to read: held back, unchecked
        assert _in_order(body, SECTIONS + ("Refs #7",)), body
        needs = body.split("## 需要你看")[1].split("## 打码记录")[0]
        assert "#2" in needs and "没能转成 PDF" in needs and not docx.exists()
    b = subprocess.run([sys.executable, str(ROOT / "site/build.py"), "--root", str(tmp), "--out", str(w / "site2")], capture_output=True, text=True, encoding="utf-8")
    assert b.returncode == 0 and "not indexed" not in b.stderr, b.stderr


def test_dry_run_with_nothing_committable_writes_the_issue_comment():
    import os, subprocess, sys
    tmp = _repo_copy(); w = pathlib.Path(tempfile.mkdtemp()); (w / "old.doc").write_bytes(b"\xd0\xcf legacy")   # cannot be converted
    (w / "notes.txt").write_text("Tips for the final", encoding="utf-8")                                       # never given a view
    form = w / "form.md"; form.write_text(_body(), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    r = subprocess.run([sys.executable, str(ROOT / "intake/intake.py"), "--dry-run", "--form", str(form), "--files", str(w / "old.doc"),
                        str(w / "notes.txt"), "--repo", str(tmp), "--issue", "8", "--work", str(w / "work")],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr
    text = (w / "work/ISSUE_COMMENT.md").read_text(encoding="utf-8")
    assert not (w / "work/PR_BODY.md").exists() and "需要你看" in text and "#1" in text and "Closes" not in text
    needs = text.split("## 需要你看")[1].split("## 打码记录")[0]
    one, two = (next(l for l in needs.splitlines() if l.startswith(f"- #{k} ")) for k in (1, 2))
    assert "没能转成 PDF" in one and "纯文本文件暂不自动检查 | plain-text files are not checked automatically yet" in two
    assert not list(tmp.rglob("*.doc")) and not list(tmp.rglob("*.txt"))


def test_the_fixture_form_is_valid():
    f = intake.parse_form((ROOT / "intake/fixtures/form.md").read_text(encoding="utf-8"))
    assert intake.validate(f) == [] and f.code == "COM107" and f.credit == intake.CREDIT_ANON


def test_dry_run_validation_failure_writes_problems():
    import subprocess, sys
    tmp = _repo_copy(); w = pathlib.Path(tempfile.mkdtemp())
    form = w / "form.md"; form.write_text(_body().replace("- [x] " + intake.CHECKS[0], "- [ ] " + intake.CHECKS[0]), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "intake/intake.py"), "--dry-run", "--form", str(form), "--repo", str(tmp), "--work", str(w / "work")],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "Unticked" in (w / "work/PROBLEMS.md").read_text(encoding="utf-8") and not (w / "work/PR_BODY.md").exists()


def test_issue_form_matches_parser():
    """The form's labels are the parser's keys; they must agree byte for byte."""
    yml = (ROOT / ".github/ISSUE_TEMPLATE/upload.yml").read_text(encoding="utf-8")
    for title in intake.FIELDS.values():
        assert f'label: "{title}"\n' in yml, title
    for s in intake.CHECKS:
        assert f'- label: "{s}"\n' in yml, s
    for opt in intake.PROGS + intake.SUBS + intake.CREDITS:
        assert f'- "{opt}"\n' in yml, opt
    assert "labels: [upload]" in yml
    assert (ROOT / ".github/ISSUE_TEMPLATE/config.yml").read_text(encoding="utf-8").strip() == "blank_issues_enabled: true"


# ---- review fixes

def test_validate_accepts_general_education_codes():
    for code in ("MPU3.1", "MPU1.1", "G0191", "GE0227", "EEE999", "com107"):
        f = intake.parse_form(_body(com107=code))
        assert intake.validate(f) == [], (code, intake.validate(f))
    for code in ("MP.1", "COM1", "MPU3.1.2", "1234"):         # a code the bot cannot read is left out, never sent back
        f = intake.parse_form(_body(com107=code))
        assert intake.validate(f) == [] and f.code in ("", "MPU3.1"), (code, f.code)


def test_cell_neutralizes_html_and_course_name_too():
    assert intake._cell("<b>x</b> | y\nz") == "&lt;b&gt;x&lt;/b&gt; \\| y z"
    readme = intake.new_course_readme("AIT101", "Intro <b>AI</b>")
    assert "<b>" not in readme and readme.startswith("# AIT101 Intro &lt;b&gt;AI&lt;/b&gt;")


def test_pr_body_keeps_user_text_in_code_spans():
    git, gh, _ = run_with_fakes({1: SORT_OK}, {1: CHECK_OK}, zip_name="@someone fixes #3 `x`.zip",
                                failed=[("<img src=x onerror=1>.jpg", "r | r")])
    body = pr_body_of(gh)
    spans = re.findall(r"`[^`\n]*@someone fixes #3 'x'\.zip › 1`", body)
    assert spans and body.count("@someone") == len(spans) and "<img" not in body and "Closes #5" in body


def test_unpack_reports_broken_zip_members():
    import zipfile
    work = pathlib.Path(tempfile.mkdtemp()); z = work / "b.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("ok.pdf", b"%PDF ok"); zf.writestr("x.pdf", b"%PDF hello world")
    raw = bytearray(z.read_bytes()); i = raw.find(b"hello world"); raw[i:i + 5] = b"HELLO"; z.write_bytes(bytes(raw))   # bad CRC
    ok, bad, _ = intake.unpack([("b.zip", z)], work)
    assert [n for n, _ in ok] == ["ok.pdf"] and [n for n, _ in bad] == ["b.zip › 2"] and " | " in bad[0][1]


def test_zip_entry_names_with_backslashes():
    """Python's zipfile normalises separators on Windows but not on the Linux runner, so the helpers are tested directly."""
    assert intake._basename("dir\\c.pdf") == "c.pdf" and intake._basename("a/b/c.pdf") == "c.pdf"
    assert intake._skip("__MACOSX\\._c.pdf") and intake._skip("__MACOSX/._c.pdf") and not intake._skip("dir\\c.pdf")
    assert intake._reject_reason("..\\x.pdf", "pdf") and intake._reject_reason("../x.pdf", "pdf")


def test_contributors_dedupe_by_name_cell():
    c = (ROOT / "CONTRIBUTORS.md").read_text(encoding="utf-8")
    f = intake.parse_form(_body(Joyce="Lu").replace("2021", "2023"))
    assert "| Lu | 2023 入学（2027 届）|" in intake.update_contributors(c, f, "Lu", 1)        # "Lu" is inside "LucasXQY" but not a listed name
    g = intake.parse_form(_body().replace(intake.CREDIT_NICK, intake.CREDIT_LOGIN))
    assert intake.update_contributors(c, g, "[LucasXQY](https://github.com/LucasXQY)", 1) == c   # listed as plain LucasXQY


def test_course_readme_order_and_login_variant():
    text = ("# X\n\n| 学期 / Session | 老师 / Lecturer | 考核 / Assessment | 资料来自 / Contributed by | 备注 / Notes |\n|---|---|---|---|---|\n"
            "| [unknown](unknown/) |  |  | LucasXQY | [Notes](unknown/Notes/) |\n"
            "| [2024-09](2024-09/) |  |  | LucasXQY | [Tutorials](2024-09/Tutorials/) |\n"
            "| [2025-04](2025-04/) |  |  | LucasXQY | [Labs](2025-04/Labs/) |\n")
    out = intake.update_course_readme(text, intake.parse_form(_body().replace("2022-04", "2026-04")), "Joyce").splitlines()
    assert out[-1].startswith("| [2026-04]") and out[-2].startswith("| [2025-04]")
    g = intake.parse_form(_body().replace("2022-04", "2024-09"))
    row = next(l for l in intake.update_course_readme(text, g, "[LucasXQY](https://github.com/LucasXQY)").splitlines() if l.startswith("| [2024-09]"))
    assert row.count("LucasXQY") == 1


def test_rejected_lines_capped():
    text = intake.rejected_lines([(f"p{i}.jpg", "no | no") for i in range(100)])
    assert text.count("\n") < 40 and "70" in text


def test_course_dir_name_collapses_separators():
    assert intake.course_dir_name("EEE212", "Signals & Systems") == "EEE212_Signals_Systems"
    assert intake.course_dir_name("MPU3.1", " Intro  to  Law ") == "MPU3.1_Intro_to_Law"


def test_attachment_name_comes_from_url():
    assert intake.attachment_links("[x.pdf](https://github.com/user-attachments/files/1/x.zip)") == [("x.zip", "https://github.com/user-attachments/files/1/x.zip")]
    assert intake.attachment_links("[a](https://github.com/user-attachments/files/2/My%20File.pdf)") == [("My File.pdf", "https://github.com/user-attachments/files/2/My%20File.pdf")]


def test_git_errors_carry_stderr():
    try:
        intake.Git(pathlib.Path(tempfile.mkdtemp())).head(); assert False
    except RuntimeError as e:
        assert "repository" in str(e).lower()


# ---- the full check's answer as a decide.Check (ai.check_items does the calls; see test_ai.py)

FULL = {"answers": "no", "marking_scheme": "no", "personal_data": "no", "summary": "期末试卷 | final paper", "notes": "",
        "lecturers": ["Dr Tan"], "weighting": "Final 60%", "redactions": []}


def test_check_results_map_to_check_only_with_every_schema_key():
    from decide import Check
    c = intake._check_of(dict(FULL, extra="dropped"))
    assert isinstance(c, Check) and c == Check(**FULL)                                    # only the schema's keys
    for k in FULL:                                                                        # a missing key: no check at all
        assert intake._check_of({x: v for x, v in FULL.items() if x != k}) is None, k
    for bad in ({"lecturers": "Dr Tan"}, {"lecturers": [3]}, {"redactions": {"page": 1}}, {"redactions": ["x"]},
                {"summary": None}, {"weighting": 50}, {"answers": ["no"]}):
        assert intake._check_of(dict(FULL, **bad)) is None, bad
    assert intake._check_of(None) is None and intake._check_of("no") is None
    box = {"page": 1, "kind": "box", "box": [0, 0, 9, 9], "text": "", "what": "签名 | signature"}
    assert intake._check_of(dict(FULL, redactions=[box])).personal_data == "yes"          # something to black out
    assert intake._check_of(dict(FULL, answers="maybe")).answers == "maybe"               # decide() calls it unreadable


def test_lecturers_and_weighting_are_filtered_and_capped():
    lect = ["Dr Tan", "tan@gmail.com", "ABC2109123", "Dr Lim (call 0123456789)", " ", "x" * 100, "A", "B", "C", "D"]
    c = intake._check_of(dict(FULL, lecturers=lect, weighting="ABC2109123 Final 60%"))
    assert c.lecturers == ["Dr Tan", "x" * 80, "A", "B", "C"] and c.weighting == ""       # 5 entries x 80 characters
    assert intake._check_of(dict(FULL, weighting=" Final 60%, " + "y" * 300)).weighting == ("Final 60%, " + "y" * 300)[:200]
    assert intake._check_of(dict(FULL, weighting="mail a.b@gmail.com for marks")).weighting == ""
    assert intake._check_of(dict(FULL, weighting="Final 50%, Lab 20% (lecturer: tan@xmu.edu.my)")).weighting.startswith("Final 50%")


# ---- the whole run with fakes: GitHub, git, the bot-state cache and Claude all in memory

SORT_OK = {"course_code": "COM107", "course_name": "Circuits and Devices", "session": "2022-04", "type": "Past-Papers",
           "label": "Final", "assessment": "Final", "set": "", "keep": True, "skip_reason": "", "confident": True,
           "note": "期末试卷 | final paper"}
CHECK_OK = {"summary": "期末试卷 | final paper", "answers": "no", "personal_data": "no", "marking_scheme": "no", "notes": "",
            "lecturers": [], "weighting": "", "redactions": []}
# four files from "papers.zip": 1 clean COM107 final, 2 course unsure, 3 personal data unsure, 4 lecture slides
SORTS = {1: SORT_OK, 2: dict(SORT_OK, course_code="", course_name="", confident=False),
         3: dict(SORT_OK, label="Midterm", assessment="Midterm"),
         4: dict(SORT_OK, type="Notes", label="", assessment="", keep=False, skip_reason="lecture_slides")}
CHECKS = {1: CHECK_OK, 2: CHECK_OK, 3: dict(CHECK_OK, personal_data="unsure", notes="第 2 页右上角像有学号 | page 2, top right, may hold an ID")}
TEXTS = {1: "COM107 Final Examination April 2022", 2: "Quiz on resistors", 3: "COM107 Midterm", 4: "Lecture 3 slides"}
_BYTES = {}


def _picture(text, dpi=60):
    """A grey page image of typed text: what a scanner makes of a cover sheet."""
    import fitz
    with fitz.open() as src:
        pg = src.new_page(width=595, height=842)
        pg.insert_text((60, 80), text, fontsize=14)
        pg.insert_text((60, 200), "COM107 Final Examination April 2022 - Question 1", fontsize=14)
        return pg.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)


def _wrapped(text_):
    """Long text as lines that fit on the page (text off the page cannot be read back)."""
    import textwrap
    return "\n".join(textwrap.wrap(text_, 70)) if len(text_) > 70 else text_


ANNOTATED_HIGHLIGHT = (72, 60, 260, 76)


def _pdf_bytes(k, pages=1, text=None, kind="text"):
    """One fixed PDF per number (PyMuPDF output is not byte-stable, so each is made once). kind "text": a text line per
    page; "scan": every page one picture of the text, no text layer; "image": the text line and a picture beside it;
    "annotated": the text line under a highlight whose author is "Tan Ah Kow", and a sticky note holding "EEE2109300"
    (what Acrobat or Preview leave in a file nobody redacts: nothing of it is drawn as text)."""
    key = (k, pages, text, kind)
    if key not in _BYTES:
        import fitz
        parts = [_wrapped(t) for t in (text or TEXTS.get(k, f"file {k}")).split("")]   # "" starts a new page
        d = fitz.open()
        for i in range(max(pages, len(parts))):
            line = parts[min(i, len(parts) - 1)]
            pg = d.new_page(width=595, height=842)
            if kind == "scan":
                pg.insert_image(pg.rect, pixmap=_picture(line))
                continue
            pg.insert_text((72, 72), line)
            if kind == "image":
                pg.insert_image(fitz.Rect(72, 300, 272, 500), pixmap=_picture(line, dpi=20))
            if kind == "annotated":
                pg.add_highlight_annot(ANNOTATED_HIGHLIGHT).set_info(title="Tan Ah Kow")
                pg.add_text_annot((500, 500), "EEE2109300")
        _BYTES[key] = d.tobytes(); d.close()
    return _BYTES[key]


SHA1 = __import__("hashlib").sha256(_pdf_bytes(1)).hexdigest()
FORM_BODY = _body().replace("com107", "_No response_").replace("Circuits and Devices", "_No response_").replace("2022-04", "_No response_").replace(
    "[final.pdf](https://github.com/user-attachments/files/111/final.pdf)\n"
    "[bundle.zip](https://github.com/user-attachments/files/112/bundle.zip)", "[papers.zip](https://github.com/user-attachments/files/112/papers.zip)")


CACHE_PATH = "intake-cache/issue-5.json"


class FakeGH:
    """GitHub in memory. The upload cache is the real cache.IntakeCache over test_cache's in-memory contents API, so
    the run and the commands go through the same read / sha / write path as on bot-state; every PUT that reaches the
    store (already scrubbed) is kept in cache_writes."""
    repo = "o/r"

    def __init__(self, body=None, labels=("upload",), cache=None, pr=None, head="upload/issue-5", prs=(), issues=()):
        from test_cache import FakeStore
        self.body, self.labels, self.pr, self.head = body, list(labels), pr, head
        self.calls, self.cache_writes, self.ai_calls, self.ai_text, self.comments = [], [], [], [], {}
        self.prs, self.issues = list(prs), list(issues)
        self.state = FakeStore(branch=True)
        if cache is not None:
            self.state.files[CACHE_PATH] = (json.loads(json.dumps(cache)), "c")

    @property
    def cache(self):
        entry = self.state.files.get(CACHE_PATH)
        return entry[0] if entry else None

    def _contents(self, *args, input=None):
        if "-X" in args and args[args.index("-X") + 1] == "PUT":
            import base64
            self.cache_writes.append(json.loads(base64.b64decode(json.loads(input)["content"])))
        return self.state.gh(*args, input=input)

    def intake_cache(self, issue):
        import cache
        return cache.IntakeCache(self.repo, issue, gh=self._contents)

    def issue(self, n): return {"body": self.body, "title": "t", "state": "open", "login": "alice", "id": 111, "labels": self.labels}
    def comment(self, n, text): self.calls.append(("comment", text))
    def comment_pr(self, n, text): self.calls.append(("comment_pr", text))
    def label(self, n, add=(), remove=()): self.calls.append(("label", tuple(add), tuple(remove)))
    def pr_for(self, branch): return self.pr
    def pr_head(self, n): return self.head
    def pr_head_sha(self, n): return f"PRHEAD{n}"
    def create_or_edit_pr(self, branch, title, body, draft=False):
        self.calls.append(("pr", title, body)); self.pr = self.pr or 1
        return f"https://example/pr/{self.pr}"
    def set_draft(self, pr, draft): self.calls.append(("draft", pr, draft))
    def set_status(self, sha, ok, text): self.calls.append(("status", sha, ok))
    def react(self, comment_id, content): self.calls.append(("react", comment_id, content))
    def upsert_comment(self, n, marker, text): self.calls.append(("upsert_comment", n, text)); self.comments[(n, marker)] = text
    def open_upload_prs(self): return self.prs
    def open_upload_issues(self): return self.issues
    def dispatch(self, n): self.calls.append(("dispatch", n))


class FakeGit:
    def __init__(self): self.calls = []
    def reset_branch(self, name): self.calls.append(("reset", name))
    def head(self): return "HEAD" if any(c[0] == "commit" for c in self.calls) else "0123456789abcdef"
    def commit_all(self, message, author): self.calls.append(("commit", message, author))
    def force_push(self, name): self.calls.append(("push", name))


class FakeBudget:
    def __init__(self): self.calls = []
    def reserve(self, pool, usd): self.calls.append(("reserve", pool, usd)); return True
    def settle(self, pool, delta): self.calls.append(("settle", pool, delta))
    def spent(self, pool): return 1.25, 4.5


def fake_claude(gh, sorts, checks, during=None, verify="no", compare="different"):
    """asks {"sort", "check", "verify", "compare"}: sort and check answer from the tables by file number (read from the
    "File: <zip> › <n>" line); verify answers `verify` (a list: one answer per call, in order; "fail": no answer, as
    from a failed call) and logs the number of
    page images and their sizes; compare answers `compare`. Every question is logged in gh.ai_calls. `during()` runs
    once, on the first question: something that happens while the rebuild is under way."""
    hooks = [during] if during else []
    verdicts = list(verify) if isinstance(verify, list) else None
    gh.verify_sizes = []

    def text(content):
        while hooks:
            hooks.pop()()
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))

    def number(content):
        m = re.search(r"^File: .* › (\d+)$", text(content), re.M)
        return int(m.group(1)) if m else 1

    def sort(content, on_cost=None):
        k = number(content)
        gh.ai_calls.append(("sort", k)); gh.ai_text.append(text(content))
        if on_cost: on_cost(0.001)
        return __import__("copy").deepcopy(sorts.get(k))

    def check(content, on_cost=None):
        k = number(content)
        gh.ai_calls.append(("check", k)); gh.ai_text.append(text(content))
        if on_cost: on_cost(0.02)
        return __import__("copy").deepcopy(checks.get(k))

    def verify_(content, on_cost=None):
        images = [b for b in content if isinstance(b, dict) and b.get("type") == "image"]
        gh.ai_calls.append(("verify", len(images))); gh.ai_text.append(text(content))
        gh.verify_sizes.append([len(b["source"]["data"]) for b in images])
        if on_cost: on_cost(0.01)
        v = verdicts.pop(0) if verdicts else verify if verdicts is None else "unsure"
        return None if v == "fail" else {"readable": v}                   # "fail": the call gave no answer

    def compare_(content, on_cost=None):
        gh.ai_calls.append(("compare", 0)); gh.ai_text.append(text(content))
        if on_cost: on_cost(0.003)
        return {"relation": compare}
    return {"sort": sort, "check": check, "verify": verify_, "compare": compare_}


_THUMBS = []        # (path, bytes) of every PDF intake.thumbnails was given, at the time it was given
_BEFORE = {}        # repo copy -> its files before the run


def run_with_fakes(sorts, checks, member=None, gh=None, zip_name="papers.zip", failed=(), during=None, pages=None,
                   pdf_text=None, scanned=False, image=False, verify="no", compare="different", prepare=None,
                   annotated=False):
    """Issue #5 with one attachment, a zip holding files 1..n (n = the highest number in the tables, at least 1);
    `pages` gives some of them more than one page. `pdf_text` is file 1's text (or {k: text}); `scanned` makes file 1 a
    scan (pictures, no text layer), `image` gives file 1's pages a picture beside the text, `annotated` a highlight and a
    note that hold personal data (see _pdf_bytes). `verify` and `compare` are
    the fake re-check's and comparison's answers (compare=False: no comparison can be asked at all); `prepare(repo)` runs on the repo copy before the run. The run's
    ledger is gh.budget; the PDFs the thumbnail sheet was made from are kept for thumbnail_sources."""
    import zipfile
    tmp = _repo_copy(); w = pathlib.Path(tempfile.mkdtemp())
    if prepare:
        prepare(tmp)
    _BEFORE[str(tmp)] = set(files_in(tmp))
    texts = pdf_text if isinstance(pdf_text, dict) else {1: pdf_text} if pdf_text else {}
    z = w / "upload.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for k in range(1, max([*sorts, *checks, *(pages or {}), 1]) + 1):
            kind = ("scan" if scanned else "image" if image else "annotated" if annotated else "text") if k == 1 else "text"
            zf.writestr(f"d{k}/{member}" if member else f"orig_{k}.pdf", _pdf_bytes(k, (pages or {}).get(k, 1), texts.get(k), kind))
    gh = gh or FakeGH()
    gh.body = gh.body or FORM_BODY
    gh.budget = FakeBudget()
    git = FakeGit()
    asks = fake_claude(gh, sorts, checks, during, verify, compare)
    if compare is False:
        del asks["compare"]
    real, real_thumbs = intake.download, intake.thumbnails

    def thumbs(pdfs, out_png, *a, **kw):
        _THUMBS.extend((pathlib.Path(p), pathlib.Path(p).read_bytes()) for p in pdfs)
        return real_thumbs(pdfs, out_png, *a, **kw)
    intake.download = lambda links, work: ([(zip_name, z)], list(failed))
    intake.thumbnails = thumbs
    try:
        intake.run_issue(5, tmp, gh, git, "https://run", w / "work", asks=asks, budget=gh.budget)
    finally:
        intake.download, intake.thumbnails = real, real_thumbs
    return git, gh, tmp


def files_in(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def pr_body_of(gh):
    return next(c[2] for c in reversed(gh.calls) if c[0] == "pr")


def issue_comment_or_body(gh):
    """The last thing the run wrote for people: the issue comment that stands in for the PR, else the PR body."""
    return next((c[2] for c in reversed(gh.calls) if c[0] in ("upsert_comment", "pr")), "")


def text(path, page):
    import fitz
    with fitz.open(path) as d: return d[page - 1].get_text()


def committed_pdfs(tmp):
    """The PDFs the run added to the tree (the commit), repo-relative."""
    return sorted(p for p in files_in(tmp) if p.endswith(".pdf") and p not in _BEFORE[str(tmp)])


def thumbnail_sources(tmp):
    """The repo-relative PDFs the run's thumbnail sheet was made from."""
    return sorted(p.relative_to(tmp).as_posix() for p, _ in _THUMBS if tmp in p.parents)


def _leaks(data: bytes, secret: str) -> bool:
    """Is `secret` anywhere in a PDF: its raw bytes, every decompressed stream, its text, as plain or hex-encoded text."""
    import fitz
    forms = [secret.encode(), secret.encode().hex().encode(), secret.encode().hex().upper().encode()]
    blobs = [data]
    with fitz.open(stream=data, filetype="pdf") as d:
        for x in range(1, d.xref_length()):
            try:
                if d.xref_is_stream(x):
                    blobs.append(d.xref_stream(x) or b"")
                blobs.append(d.xref_object(x).encode("latin-1", "ignore"))
            except Exception:
                continue
        blobs.append("".join(p.get_text() for p in d).encode())
    return any(f in b for f in forms for b in blobs)


def _section(body, name, after):
    return body.split(name)[1].split(after)[0]


def tmp_repo():
    return _repo_copy()


def test_mixed_upload_commits_parks_holds_and_blocks():
    # four files from "papers.zip": 1 clean COM107 final, 2 course unsure, 3 personal data unsure, 4 lecture slides
    git, gh, tmp = run_with_fakes(SORTS, CHECKS)
    committed = files_in(tmp)
    assert any(p.endswith("COM107_202204_Final_Question.pdf") for p in committed) and "_待整理/issue-5/item-2.pdf" in committed
    assert not any("item-3" in p or "item-4" in p for p in committed)
    body = pr_body_of(gh)
    for s in ("#3", "需要你看", "#4", "lecture", "Refs #5"): assert s in body
    assert ("draft", 1, True) in gh.calls and ("status", "HEAD", False) in gh.calls
    # only the files sorted as keep are checked; the cache keeps the numbering, the verdicts and the spend
    assert ("check", 4) not in gh.ai_calls and sorted(k for kind, k in gh.ai_calls if kind == "check") == [1, 2, 3]
    assert [i["source"] for i in gh.cache["items"]] == [f"papers.zip › {k}" for k in (1, 2, 3, 4)]
    assert len(gh.cache["sort"]) == 4 and len(gh.cache["check"]) == 3 and gh.cache["spent_usd"] > 0
    assert _in_order(body, SECTIONS + ("Refs #5",)) and "Closes" not in body
    assert _tables_hold(body), body
    needs = body.split("## 需要你看")[1].split("## 打码记录")[0]
    assert "#3" in needs and "#2" in needs and "_待整理/issue-5/item-2.pdf" in needs and "#1" not in needs
    redactions = body.split("## 打码记录")[1].split("## 登记")[0]
    assert "#3" in redactions and "第 2 页" in redactions and "学号" not in redactions   # number and page, not what the AI wrote
    assert "$1.25" in body and "$4.50" in body                                          # the pool's day and month totals
    title = next(c[1] for c in gh.calls if c[0] == "pr")
    assert title == "[上传 | Upload] #5：2 个文件 | files（COM107）"
    msg = next(c[1] for c in git.calls if c[0] == "commit")
    assert msg.splitlines()[0] == "content: add files from #5" and "EEE/COM107_Circuits_and_Devices" in msg
    assert "Co-Authored-By" not in msg and "Generated with" not in body


def test_zip_member_names_never_leak():                                     # Review Focus 5
    import contextlib, io
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        git, gh, tmp = run_with_fakes(SORTS, CHECKS, member="张三_EEE2109300.pdf")
    assert "EEE2109300" not in json.dumps(gh.calls, ensure_ascii=False) and not any("EEE2109300" in p for p in files_in(tmp))
    assert "EEE2109300" not in json.dumps(gh.cache_writes, ensure_ascii=False)
    assert "EEE2109300" not in json.dumps(git.calls, ensure_ascii=False) and "EEE2109300" not in out.getvalue()
    assert not any("EEE2109300" in t or "张三" in t for t in gh.ai_text)                  # nor what Claude is told


def test_nothing_committable_means_one_issue_comment_and_no_pr():           # Review Focus 4
    git, gh, _ = run_with_fakes({1: SORT_OK}, {1: dict(CHECK_OK, personal_data="unsure")})
    assert git.calls == [("reset", "upload/issue-5")] and [c[0] for c in gh.calls].count("upsert_comment") == 1
    assert not any(c[0] == "pr" for c in gh.calls)
    text = next(c[2] for c in gh.calls if c[0] == "upsert_comment")
    assert "#1" in text and "需要你看" in text and "/approve #k" in text and "Closes" not in text and "<!--" in text
    run_with_fakes({1: SORT_OK}, {}, gh=gh)                                  # a rebuild edits the same comment
    assert [c[0] for c in gh.calls].count("upsert_comment") == 2 and len(gh.comments) == 1 and gh.ai_calls.count(("check", 1)) == 1


def test_rebuild_reuses_the_cache_and_only_pays_for_new_work():
    gh = FakeGH(cache={"sort": {SHA1: SORT_OK}, "check": {SHA1: CHECK_OK}})
    git, gh, _ = run_with_fakes({}, {}, gh=gh)
    assert gh.ai_calls == []
    assert ("draft", 1, False) in gh.calls and ("status", "HEAD", True) in gh.calls and "Closes #5" in pr_body_of(gh)


def test_unchecked_files_wait_for_a_retry():
    gh = FakeGH()
    git, gh, tmp = run_with_fakes({1: SORT_OK, 2: None}, {1: None}, gh=gh)           # check of 1 failed, sorting of 2 failed
    assert not any("Past-Papers" in p or "_待整理" in p for p in files_in(tmp)) and git.calls == [("reset", "upload/issue-5")]
    assert SHA1 not in gh.cache.get("check", {}) and len(gh.cache["sort"]) == 1        # failures are never cached
    assert ("check", 2) not in gh.ai_calls                                              # an unsorted file is not checked
    text = next(c[2] for c in gh.calls if c[0] == "upsert_comment")
    assert "#1" in text and "#2" in text and "/retry" in text.split("## 需要你看")[1].split("## 打码记录")[0]


def test_a_check_that_keeps_failing_waits_for_retry_after_two_tries():       # final review, finding 3
    gh = FakeGH()
    for run in range(3):                                                        # the check never gives an answer
        git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: None}, gh=gh)
        assert committed_pdfs(tmp) == []
    assert gh.ai_calls.count(("check", 1)) == 2 and gh.cache["check_failed"] == {SHA1: 2}
    needs = _section(issue_comment_or_body(gh), "## 需要你看", "## 打码记录")
    assert "检查失败 2 次，用 `/retry` 再试 | the check failed 2 times; use `/retry` to try again" in needs
    assert "之后的重建会再试" not in needs
    assert intake.store_commands(_event("/retry"), gh) == 5
    git, gh, tmp = run_with_fakes({}, {1: CHECK_OK}, gh=gh)                      # /retry: asked again, and it answers
    assert gh.ai_calls.count(("check", 1)) == 3 and committed_pdfs(tmp) == [FINAL] and not gh.cache.get("check_failed")
    data = {"overrides": {"_retry": {"seq": 2}}, "retry_done": 1, "check_failed": {"a": 2, "b": 1}}
    assert intake.apply_retry(data) and data["check_failed"] == {}             # /retry clears every count


def test_overrides_steer_the_next_run():
    sha = lambda k: __import__("hashlib").sha256(_pdf_bytes(k)).hexdigest()
    gh = FakeGH(cache={"overrides": {sha(1): {"skip": True}, sha(3): {"approved_by": "m-1"}, sha(4): {"keep": True},
                                     sha(2): {"place": {"code": "COM107", "session": "unknown", "type": "Notes", "label": "Quiz"}}}})
    git, gh, tmp = run_with_fakes(SORTS, {**CHECKS, 4: CHECK_OK}, gh=gh)
    files = files_in(tmp)
    assert ("sort", 1) not in gh.ai_calls and ("check", 1) not in gh.ai_calls and ("check", 4) in gh.ai_calls  # /skip unasked, /keep checked
    assert not any(p.endswith("COM107_202204_Final_Question.pdf") for p in files)
    assert any(p.endswith("COM107_202204_Midterm_Question.pdf") for p in files)                   # /approve: committed
    assert "EEE/COM107_Circuits_and_Devices/unknown/Notes/COM107_unknown_Quiz.pdf" in files        # /place
    assert any(p.endswith("2022-04/Notes/COM107_202204_Notes.pdf") for p in files)                 # /keep
    body = pr_body_of(gh)
    assert "`m-1`" in body and "@m-1" not in body and "Closes #5" in body and ("draft", 1, False) in gh.calls


def test_personal_text_in_lecturers_or_weighting_reaches_no_readme_and_no_cache():
    sorts = {1: dict(SORT_OK, session="2023-09"), 2: dict(SORT_OK, session="2024-02", label="Midterm", assessment="Midterm")}
    checks = {1: dict(CHECK_OK, lecturers=["Dr Tan", "tan@gmail.com", "ABC2109123", "call 0123456789"], weighting="ABC2109123 Final 60%"),
              2: dict(CHECK_OK, lecturers=["Dr Lim"], weighting="Final 60%, Coursework 40%")}
    git, gh, tmp = run_with_fakes(sorts, checks)
    readme = (tmp / "EEE/COM107_Circuits_and_Devices/README.md").read_text(encoding="utf-8")
    row1 = next(l for l in readme.splitlines() if l.startswith("| [2023-09]"))
    row2 = next(l for l in readme.splitlines() if l.startswith("| [2024-02]"))
    assert "| Dr Tan |  |" in row1 and "| Dr Lim | Final 60%, Coursework 40% |" in row2   # clean text still flows
    for bad in ("gmail", "ABC2109123", "0123456789"):
        assert bad not in readme and bad not in json.dumps(gh.cache_writes, ensure_ascii=False), bad
        assert bad not in json.dumps(gh.calls, ensure_ascii=False) and bad not in json.dumps(git.calls, ensure_ascii=False), bad


SECRET_PAGE = "Name: Zhang San  ID: EEE2109300  COM107 Final Examination April 2022"
FOUND = dict(CHECK_OK, personal_data="yes", notes="第 1 页页眉 | page 1 header", redactions=[
    {"page": 1, "kind": "text", "text": "EEE2109300", "box": [0, 0, 0, 0], "what": "学号 EEE2109300 | student ID EEE2109300"},
    {"page": 1, "kind": "text", "text": "Zhang San", "box": [0, 0, 0, 0], "what": "name"}])
FINAL = "EEE/COM107_Circuits_and_Devices/2022-04/Past-Papers/COM107_202204_Final_Question.pdf"


def test_redaction_targets_never_leave_the_run():                           # the privacy invariant, end to end
    import contextlib, io
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: FOUND}, pdf_text=SECRET_PAGE)
    original = _pdf_bytes(1, 1, SECRET_PAGE, "text")
    assert all(_leaks(original, s) for s in ("EEE2109300", "Zhang San"))              # the upload holds both
    assert gh.ai_calls == [("sort", 1), ("check", 1)] and gh.cache_writes              # text only: no re-check needed
    assert committed_pdfs(tmp) == [FINAL] and ("push", "upload/issue-5") in git.calls  # now redacted and committed
    committed = (tmp / FINAL).read_bytes()
    for secret in ("EEE2109300", "Zhang San"):
        assert not _leaks(committed, secret), secret                                   # text, raw bytes, streams
        assert not any(_leaks(b, secret) for p, b in _THUMBS if tmp in p.parents), secret   # nor the thumbnails
        assert secret not in json.dumps(gh.calls, ensure_ascii=False), secret
        assert secret not in json.dumps(git.calls, ensure_ascii=False), secret
        assert secret not in json.dumps(gh.cache_writes, ensure_ascii=False), secret
        assert secret not in out.getvalue(), secret
    assert "Final Examination" in text(tmp / FINAL, 1)                                 # the rest of the page stays
    body = pr_body_of(gh)
    record = _section(body, "## 打码记录", "## 登记")
    line = next(l for l in record.splitlines() if l.startswith("- #1"))
    assert "第 1 页" in line and "姓名 | name ×1" in line and "其他 | other ×1" in line and "学号" not in line   # page and kind only
    assert "#1" not in _section(body, "## 需要你看", "## 打码记录").split("不挡合并")[0]
    stored = gh.cache["check"][_sha_of(gh, 1)]
    assert [r.get("text") for r in stored["redactions"]] == [None, None] and [r["page"] for r in stored["redactions"]] == [1, 1]
    assert stored["redactions_ok"] is True and stored["redactions_rects"] and all(set(r) == {"page", "rect"} for r in stored["redactions_rects"])


def test_an_unredacted_pdf_is_committed_without_annotation_authors_or_notes():    # final review, finding 1
    import fitz
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: CHECK_OK}, annotated=True)   # the check found nothing to black out
    original = _pdf_bytes(1, 1, None, "annotated")
    assert _leaks(original, "Tan Ah Kow") and _leaks(original, "EEE2109300")
    assert committed_pdfs(tmp) == [FINAL]
    committed = (tmp / FINAL).read_bytes()
    assert not _leaks(committed, "Tan Ah Kow") and not _leaks(committed, "EEE2109300")
    assert not any(_leaks(b, "Tan Ah Kow") or _leaks(b, "EEE2109300") for p, b in _THUMBS if tmp in p.parents)
    def highlight(pdf_bytes):
        with fitz.open(stream=pdf_bytes, filetype="pdf") as d:
            assert "Highlight" in [a.type[1] for a in d[0].annots()] and "COM107 Final" in d[0].get_text()
            return d[0].get_pixmap(dpi=72, colorspace=fitz.csRGB, clip=fitz.Rect(ANNOTATED_HIGHLIGHT)).samples
    drawn = highlight(committed)
    assert drawn == highlight(original)                                          # the highlight is drawn as before
    with fitz.open(stream=original, filetype="pdf") as d:
        d[0].delete_annot(d[0].first_annot)
        assert d[0].get_pixmap(dpi=72, colorspace=fitz.csRGB, clip=fitz.Rect(ANNOTATED_HIGHLIGHT)).samples != drawn


def test_a_file_over_100_pages_is_held_back_unchecked():
    git, gh, _ = run_with_fakes({1: SORT_OK}, {1: CHECK_OK}, pages={1: 101})
    assert gh.ai_calls == [("sort", 1)] and git.calls == [("reset", "upload/issue-5")]   # never sent to the check
    text = next(c[2] for c in gh.calls if c[0] == "upsert_comment")
    assert "#1" in text.split("## 需要你看")[1].split("## 打码记录")[0] and "超过 100 页，没有检查 | over 100 pages, not checked" in text
    (sha,) = gh.cache["check_skipped"]
    assert gh.cache["check_skipped"][sha] == "too_long" and not gh.cache.get("check")


def test_the_check_shares_the_sorting_meter_and_its_spend_is_shown():
    git, gh, _ = run_with_fakes({1: SORT_OK, 2: SORT_OK}, {1: CHECK_OK, 2: CHECK_OK}, pages={2: 3})
    reserves = [round(c[2], 6) for c in gh.budget.calls if c[0] == "reserve"]
    assert reserves == [0.004, 0.065, 0.115]                     # sorting 2 x 0.002, then 0.04 + 0.025 x pages per file
    settles = [round(c[2], 6) for c in gh.budget.calls if c[0] == "settle"]
    assert settles == [round(0.042 - 0.184, 6)] and abs(gh.cache["spent_usd"] - 0.042) < 1e-9   # one settlement for both
    assert "分拣 | Sorting: $0.0020 · 检查 | Checks: $0.0400" in pr_body_of(gh)


def test_an_old_pre_check_result_is_checked_again():
    old = {"summary": "s", "answers": "no", "personal_data": "no", "marking_scheme": "no", "notes": ""}
    gh = FakeGH(cache={"sort": {SHA1: SORT_OK}, "check": {SHA1: old}})
    git, gh, _ = run_with_fakes({}, {1: CHECK_OK}, gh=gh)
    assert gh.ai_calls == [("check", 1)] and gh.cache["check"][SHA1] == CHECK_OK and "Closes #5" in pr_body_of(gh)


# ---- redaction, re-check and near-duplicates in the run (spec §5 steps 3–5)

TEXT_ID = [{"page": 1, "kind": "text", "text": "EEE2109300", "what": "student_id"}]
BOX_SIG = [{"page": 1, "kind": "box", "box": [0, 0, 500, 100], "what": "signature"}]


def test_redacted_copy_is_what_gets_committed():
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: dict(CHECK_OK, personal_data="yes", redactions=TEXT_ID)},
                                  pdf_text="ID: EEE2109300 Final Examination COM107")
    p = next(p for p in files_in(tmp) if p.endswith("COM107_202204_Final_Question.pdf"))
    assert "EEE2109300" not in text(tmp / p, 1) and "学号" in pr_body_of(gh) and "EEE2109300" not in pr_body_of(gh)
    assert not any(kind == "verify" for kind, _ in gh.ai_calls)          # text on a page without pictures: no re-check
    body = pr_body_of(gh)
    assert "Closes #5" in body and ("status", "HEAD", True) in gh.calls
    row = next(l for l in body.splitlines() if l.startswith("| #1 "))
    assert "打码 1 处 \\| 1 redaction" in row and _tables_hold(body)       # a table cell: its pipes are escaped
    line = next(l for l in _section(body, "## 打码记录", "## 登记").splitlines() if l.startswith("- #1"))
    assert "第 1 页" in line and "没有图片" in line and "复查通过" not in line


def test_failed_verification_holds_the_file_back():
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: dict(CHECK_OK, personal_data="yes", redactions=BOX_SIG)},
                                  verify="unsure", scanned=True)
    assert committed_pdfs(tmp) == [] and "需要你看" in issue_comment_or_body(gh)
    assert ("verify", 1) in gh.ai_calls and git.calls == [("reset", "upload/issue-5")]
    text_ = issue_comment_or_body(gh)
    assert "打码后复查没通过" in _section(text_, "## 需要你看", "## 打码记录")
    assert "复查没通过 | re-check did not pass" in _section(text_, "## 打码记录", "## 登记")
    assert gh.cache["verify"][_sha_of(gh, 1)] == "unsure"                  # an answer is kept: /retry asks again


def test_thumbnails_come_only_from_committed_files():
    _, _, tmp = run_with_fakes(SORTS, CHECKS)
    assert thumbnail_sources(tmp) == committed_pdfs(tmp)
    assert committed_pdfs(tmp) == ["EEE/COM107_Circuits_and_Devices/2022-04/Past-Papers/COM107_202204_Final_Question.pdf",
                                   "_待整理/issue-5/item-2.pdf"]
    _, _, tmp = run_with_fakes({1: SORT_OK}, {1: FOUND}, pdf_text=SECRET_PAGE)          # a redacted file: its redacted copy
    assert thumbnail_sources(tmp) == committed_pdfs(tmp) == [FINAL]
    assert [b for p, b in _THUMBS if tmp in p.parents] == [(tmp / FINAL).read_bytes()]
    assert not any(_leaks(b, "EEE2109300") for p, b in _THUMBS if tmp in p.parents)


def test_a_box_redaction_that_passes_the_re_check_is_committed_and_rebuilt_for_free():
    import fitz
    found = dict(CHECK_OK, personal_data="yes", redactions=BOX_SIG)
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, scanned=True, verify="no")
    assert committed_pdfs(tmp) == [FINAL] and gh.ai_calls == [("sort", 1), ("check", 1), ("verify", 1)]
    with fitz.open(tmp / FINAL) as d:                                         # the top of the page is black now
        pix = d[0].get_pixmap(colorspace=fitz.csGRAY, dpi=20)
        top = [pix.samples[y * pix.stride + x] for y in range(2, int(pix.height * 0.1)) for x in range(2, int(pix.width * 0.45))]
        assert sum(top) / len(top) < 20
    body = pr_body_of(gh)
    line = next(l for l in _section(body, "## 打码记录", "## 登记").splitlines() if l.startswith("- #1"))
    assert "复查通过 | re-check passed" in line and "签名 | signature ×1" in line
    assert "已打码" in _section(body, "## 需要你看", "## 打码记录")                       # worth a look, not blocking
    assert ("draft", 1, False) in gh.calls and gh.cache["verify"] == {_sha_of(gh, 1): "no"}
    first = (tmp / FINAL).read_bytes()
    git, gh, tmp = run_with_fakes({}, {}, gh=gh, scanned=True)                # a rebuild: cached rectangles and verdict
    assert gh.ai_calls == [("sort", 1), ("check", 1), ("verify", 1)] and committed_pdfs(tmp) == [FINAL]
    with fitz.open(tmp / FINAL) as a, fitz.open(stream=first, filetype="pdf") as b:
        assert a[0].get_pixmap(dpi=20).samples == b[0].get_pixmap(dpi=20).samples


def test_the_re_check_goes_eight_pages_at_a_time_and_every_batch_must_pass():
    boxes = [{"page": p, "kind": "box", "box": [0, 0, 400, 100], "what": "签名"} for p in range(1, 11)]
    found = dict(CHECK_OK, personal_data="yes", redactions=boxes)
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, scanned=True, pages={1: 10}, verify="no")
    assert [c for c in gh.ai_calls if c[0] == "verify"] == [("verify", 8), ("verify", 2)] and committed_pdfs(tmp) == [FINAL]
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, scanned=True, pages={1: 10}, verify=["no", "unsure"])
    assert [c for c in gh.ai_calls if c[0] == "verify"] == [("verify", 8), ("verify", 2)] and committed_pdfs(tmp) == []
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, scanned=True, pages={1: 10}, verify=["unsure"])
    assert [c for c in gh.ai_calls if c[0] == "verify"] == [("verify", 8)] and committed_pdfs(tmp) == []   # stops at the first


def test_page_images_over_the_limit_are_rendered_again_smaller():
    import ai, redact
    from test_ai import FakeBudget as Ledger
    dpis, sent = [], []

    def render(pdf, pages, dpi=150):
        dpis.append(dpi)
        return [b"x" * (5_000_000 if dpi >= 150 else 4_000_000 if dpi >= 100 else 1000) for _ in pages]

    def ask(content, on_cost=None):
        sent.append([len(b["source"]["data"]) for b in content if b.get("type") == "image"])
        return {"readable": "no"}
    real = redact.render
    redact.render = render
    try:
        meter = ai.Meter(Ledger(ok=True), run_cap=100)
        assert intake._verify(pathlib.Path("x.pdf"), [1], ask, meter) == "no"
        assert dpis[0] == 150 and all(d < 150 for d in dpis[1:]) and len(sent) == 1
        assert sent[0][0] <= intake.VERIFY_PNG_MAX * 4 // 3 + 4                 # the small image went, base64-encoded
        dpis.clear(); sent.clear()
        redact.render = lambda pdf, pages, dpi=150: [b"x" * 3_000_000 for _ in pages]   # 8 pages, 24 MB: over one call's share
        assert intake._verify(pathlib.Path("x.pdf"), list(range(1, 9)), ask, meter) == "no"
        assert sum(len(s) for s in sent) == 8 and all(len(s) * 3_000_000 <= intake.VERIFY_CALL_MAX for s in sent)
        sent.clear()
        redact.render = lambda pdf, pages, dpi=150: [b"x" * 9_000_000 for _ in pages]   # never small enough: not sent
        assert intake._verify(pathlib.Path("x.pdf"), [1], ask, meter) == "unsure" and sent == []
        redact.render = lambda pdf, pages, dpi=150: [b"png" for _ in pages]
        assert intake._verify(pathlib.Path("x.pdf"), [1], None, meter) == "not_run"         # could not be asked
        assert intake._verify(pathlib.Path("x.pdf"), [1], ask, None) == "not_run"
        assert intake._verify(pathlib.Path("x.pdf"), [1], lambda c, on_cost=None: None, meter) == "not_run"   # failed call
        assert intake._verify(pathlib.Path("x.pdf"), [1], ask, ai.Meter(Ledger(ok=False), run_cap=100)) == "not_run"
        assert intake._verify(pathlib.Path("x.pdf"), [1], lambda c, on_cost=None: {"readable": "yes"}, meter) == "unsure"
    finally:
        redact.render = real


def test_text_blacked_out_on_a_page_with_a_picture_is_re_checked():
    found = dict(CHECK_OK, personal_data="yes", redactions=TEXT_ID)
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, pdf_text="ID: EEE2109300 Final Examination COM107", image=True)
    assert ("verify", 1) in gh.ai_calls and committed_pdfs(tmp) == [FINAL] and "EEE2109300" not in text(tmp / FINAL, 1)


def test_a_rebuild_redacts_from_the_cached_rectangles():
    import redact
    found = dict(CHECK_OK, personal_data="yes", redactions=TEXT_ID)
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, pdf_text="ID: EEE2109300 Final Examination COM107")
    saved = gh.cache_writes[-1]["check"][_sha_of(gh, 1)]
    assert "text" not in saved["redactions"][0] and saved["redactions_rects"][0]["page"] == 1
    calls, real = [], redact.apply
    redact.apply = lambda *a, **kw: calls.append(kw.get("cached_rects")) or real(*a, **kw)
    try:
        git, gh, tmp = run_with_fakes({}, {}, gh=gh, pdf_text="ID: EEE2109300 Final Examination COM107")
    finally:
        redact.apply = real
    assert calls == [saved["redactions_rects"]] and not any(k == "check" for k, _ in gh.ai_calls[2:])
    assert committed_pdfs(tmp) == [FINAL] and "EEE2109300" not in text(tmp / FINAL, 1) and "Final Examination" in text(tmp / FINAL, 1)


def test_a_cached_check_without_text_or_rectangles_is_held_until_retry():
    sha = __import__("hashlib").sha256(_pdf_bytes(1, 1, "ID: EEE2109300 Final Examination COM107", "text")).hexdigest()
    scrubbed = dict(CHECK_OK, personal_data="yes", redactions=[{"page": 1, "kind": "text", "box": [0, 0, 0, 0], "what": "学号"}])
    gh = FakeGH(cache={"items": [{"k": 1, "sha": sha, "source": "papers.zip › 1"}], "sort": {sha: SORT_OK}, "check": {sha: scrubbed}})
    git, gh, tmp = run_with_fakes({}, {}, gh=gh, pdf_text="ID: EEE2109300 Final Examination COM107")
    assert gh.ai_calls == [] and committed_pdfs(tmp) == []
    assert "没有打码副本" in _section(issue_comment_or_body(gh), "## 需要你看", "## 打码记录")   # nothing to black out with
    assert gh.cache["check"][sha]["redactions_ok"] is False                   # kept, marked failed
    git, gh, tmp = run_with_fakes({}, {}, gh=gh, pdf_text="ID: EEE2109300 Final Examination COM107")
    assert gh.ai_calls == [] and committed_pdfs(tmp) == []                    # an ordinary rebuild pays nothing
    assert intake.store_commands(_event("/retry"), gh) == 5 and gh.cache["overrides"]["_retry"] == {"seq": 1}
    assert sha in gh.cache["check"]                                            # the next run drops it, not the command
    git, gh, tmp = run_with_fakes({}, {1: dict(CHECK_OK, personal_data="yes", redactions=TEXT_ID)}, gh=gh,
                                  pdf_text="ID: EEE2109300 Final Examination COM107")
    assert gh.ai_calls == [("check", 1)] and committed_pdfs(tmp) == [FINAL]


def test_redaction_pages_outside_the_file_fail_closed_before_redacting():
    import redact
    from decide import Check
    from items import Item
    w = pathlib.Path(tempfile.mkdtemp()); p = w / "item-1.pdf"; p.write_bytes(_pdf_bytes(1, 1, "ID: EEE2109300", "text"))
    it = Item(1, "a.pdf", p, "s" * 64, "pdf", 1, True, ["ID: EEE2109300"], p)
    for page in (2, 0, "1", None):
        c = Check(**dict(CHECK_OK, personal_data="yes", redactions=[{"page": page, "kind": "text", "text": "EEE2109300", "what": "x"}]))
        cache = {"check": {it.sha: {"redactions": c.redactions}}}
        calls, real = [], redact.apply
        redact.apply = lambda *a, **kw: calls.append(a) or real(*a, **kw)
        try:
            b = intake._redact_one(it, c, cache["check"][it.sha], cache, w / "redacted")
        finally:
            redact.apply = real
        assert (b.status, b.out, calls) == ("failed", None, []), page
        assert cache["check"][it.sha]["redactions_ok"] is False and not (w / "redacted" / "item-1.pdf").exists()


def test_the_redaction_record_names_pages_where_a_repeat_was_blacked_out():
    from decide import Check
    from items import Item
    w = pathlib.Path(tempfile.mkdtemp()); p = w / "item-1.pdf"; p.write_bytes(_pdf_bytes(1, 2, "ID: EEE2109300", "text"))
    it = Item(1, "a.pdf", p, "r" * 64, "pdf", 2, True, ["ID: EEE2109300"] * 2, p)
    c = Check(**dict(CHECK_OK, personal_data="yes",
                     redactions=[{"page": 1, "kind": "text", "text": "EEE2109300", "what": "student_id"}]))
    cache = {"check": {it.sha: {"redactions": c.redactions}}}
    b = intake._redact_one(it, c, cache["check"][it.sha], cache, w / "redacted")
    assert b.status == "ok" and b.pages == [1, 2]                              # page 2 was changed too: say so


NO_COPY = "没有打码副本，`/approve` 会提交原文件 | no redacted copy: `/approve` commits the original"
PARTIAL = "打码没完成，`/approve` 会提交已打码的部分副本 | redaction incomplete: `/approve` commits the partly redacted copy"


def test_the_report_says_what_approve_would_commit():                          # final review, finding 6
    page = "ID: EEE2109300 Final Examination COM107"
    unusable = dict(CHECK_OK, personal_data="yes", redactions=[{"page": 1, "kind": "text", "text": " ", "what": "student_id"}])
    two = [dict(TEXT_ID[0]), {"page": 1, "kind": "text", "text": "Nobody Here", "what": "name"}]   # the name is not there
    for found, said, not_said in ((unusable, NO_COPY, PARTIAL), (dict(CHECK_OK, personal_data="yes", redactions=two), PARTIAL, NO_COPY)):
        git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, pdf_text=page)
        text_ = issue_comment_or_body(gh)
        zh, en = said.split(" | ")
        needs, record = _section(text_, "## 需要你看", "## 打码记录"), _section(text_, "## 打码记录", "## 登记")
        assert f"需要你看：{zh} | needs a look: {en}" in needs and said in record, said
        assert not_said not in text_ and committed_pdfs(tmp) == [], said
    import decide
    assert intake.BLACKOUT_TEXT["no_copy"] == decide.REDACTION_PROBLEMS["no_copy"] == NO_COPY
    assert intake.BLACKOUT_TEXT["failed"] == decide.REDACTION_PROBLEMS["failed"] == PARTIAL


def test_a_failed_redaction_is_held_and_approve_commits_the_redacted_copy():
    two = [dict(TEXT_ID[0]), {"page": 1, "kind": "text", "text": "Nobody Here", "what": "姓名"}]   # the name is not on the page
    found = dict(CHECK_OK, personal_data="yes", redactions=two)
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, pdf_text="ID: EEE2109300 Final Examination COM107")
    assert committed_pdfs(tmp) == [] and "打码没完成" in _section(issue_comment_or_body(gh), "## 需要你看", "## 打码记录")
    sha = _sha_of(gh, 1)
    assert gh.cache["check"][sha]["redactions_ok"] is False                    # kept, so /approve can use the redacted copy
    gh.state.files[CACHE_PATH][0]["overrides"] = {sha: {"approved_by": "m-1"}}
    git, gh, tmp = run_with_fakes({}, {}, gh=gh, pdf_text="ID: EEE2109300 Final Examination COM107")
    assert committed_pdfs(tmp) == [FINAL] and "EEE2109300" not in text(tmp / FINAL, 1)   # the redacted copy, not the original
    body = pr_body_of(gh)
    assert "`m-1`" in body and PARTIAL in _section(body, "## 打码记录", "## 登记")


def test_approve_commits_the_redacted_copy_after_a_failed_re_check():
    found = dict(CHECK_OK, personal_data="yes", redactions=TEXT_ID)
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, pdf_text="ID: EEE2109300 Final Examination COM107", image=True,
                                  verify="unsure")
    assert committed_pdfs(tmp) == []
    gh.state.files[CACHE_PATH][0]["overrides"] = {_sha_of(gh, 1): {"approved_by": "m-1"}}
    git, gh, tmp = run_with_fakes({}, {}, gh=gh, pdf_text="ID: EEE2109300 Final Examination COM107", image=True, verify="unsure")
    assert committed_pdfs(tmp) == [FINAL] and "EEE2109300" not in text(tmp / FINAL, 1)


def test_approve_without_a_redacted_copy_commits_the_original():
    sha = __import__("hashlib").sha256(_pdf_bytes(1, 1, "Final Examination COM107", "text")).hexdigest()
    found = dict(CHECK_OK, personal_data="yes", redactions=[{"page": 1, "kind": "text", "text": " ", "what": "学号"}])   # unusable
    gh = FakeGH(cache={"overrides": {sha: {"approved_by": "m-1"}}})
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, gh=gh, pdf_text="Final Examination COM107")
    assert committed_pdfs(tmp) == [FINAL] and (tmp / FINAL).is_file()
    assert NO_COPY in _section(pr_body_of(gh), "## 打码记录", "## 登记") and gh.cache["check"][sha]["redactions_ok"] is False


def test_office_files_with_personal_data_are_held_back_unredacted():
    from decide import Decision
    import redact
    calls, real = [], redact.apply
    redact.apply = lambda *a, **kw: calls.append(a) or real(*a, **kw)
    try:
        w = pathlib.Path(tempfile.mkdtemp()); _docx(w / "sheet.docx", body="Question 1 " * 20)
        gh = FakeGH(); git = FakeGit()
        asks = fake_claude(gh, {1: SORT_OK}, {1: dict(CHECK_OK, personal_data="yes", redactions=TEXT_ID)})
        realdl, realpdf = intake.download, __import__("items").to_pdf
        view = w / "view.pdf"; view.write_bytes(_pdf_bytes(1, 1, "ID: EEE2109300", "text"))
        __import__("items").to_pdf = lambda path, out_dir, soffice=None: view
        intake.download = lambda links, work: ([("sheet.docx", w / "sheet.docx")], [])
        try:
            gh.body = FORM_BODY
            intake.run_issue(5, _repo_copy(), gh, git, None, w / "work", asks=asks, budget=FakeBudget())
        finally:
            intake.download, __import__("items").to_pdf = realdl, realpdf
    finally:
        redact.apply = real
    assert calls == [] and git.calls == [("reset", "upload/issue-5")]
    assert "Office" in _section(issue_comment_or_body(gh), "## 需要你看", "## 打码记录")


LONG_A = ("COM107 Circuits and Devices Final Examination April 2022 Question 1 find the current through resistor R1 in the "
          "circuit shown Question 2 find the voltage across capacitor C1 Question 3 compute the power in R2")
LONG_B = ("COM107 Circuits and Devices Final Examination April 2022 Question 1 find the voltage over inductor L1 in the "
          "circuit shown Question 2 derive the gain of amplifier A2 Question 3 compute the power in R2")
OLD = "EEE/COM107_Circuits_and_Devices/2022-04/Past-Papers/COM107_202204_Final_Question.pdf"


def _old_paper(text_):
    def prepare(repo):
        import fitz
        (repo / OLD).parent.mkdir(parents=True, exist_ok=True)
        d = fitz.open()
        for part in text_.split(""):                                   # "" starts a new page
            d.new_page().insert_text((72, 72), _wrapped(part))
        d.set_metadata({"title": "old"}); d.save(repo / OLD); d.close()
    return prepare


def test_a_near_duplicate_of_a_repo_file_is_not_taken():
    import dedupe
    assert dedupe.similarity(LONG_A, LONG_A) >= dedupe.SAME_AT and dedupe.ASK_AT <= dedupe.similarity(LONG_A, LONG_B) < dedupe.SAME_AT
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: CHECK_OK}, pdf_text=LONG_A, prepare=_old_paper(LONG_A))
    assert committed_pdfs(tmp) == [] and not any(k == "compare" for k, _ in gh.ai_calls)
    text_ = issue_comment_or_body(gh)
    assert f"duplicate of {OLD}" in _section(text_, "## 不收", "## 可能误判")
    gh = FakeGH(cache={"overrides": {_sha_of(gh, 1): {"keep": True}}})                  # /keep takes it back
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: CHECK_OK}, gh=gh, pdf_text=LONG_A, prepare=_old_paper(LONG_A))
    assert committed_pdfs(tmp) == [OLD.replace("_Question.pdf", "_Question_2.pdf")]


def test_a_comparison_that_gave_no_answer_flags_a_possible_duplicate():      # final review, finding 11
    for answer in ("nonsense", False):                      # an answer outside the list; no comparison possible at all
        git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: CHECK_OK}, pdf_text=LONG_B + "Question 4 upload second page",
                                      prepare=_old_paper(LONG_A + "Question 4 archive second page"), compare=answer)
        assert committed_pdfs(tmp) == ["EEE/COM107_Circuits_and_Devices/2022-04/Past-Papers/COM107_202204_Final_Question_2.pdf"]
        body = pr_body_of(gh)
        worth = body.split("不挡合并，但请留意")[1].split("## 打码记录")[0]
        assert "可能重复，未比较 | possible duplicate, not compared" in worth, answer
        assert ("draft", 1, False) in gh.calls and ("status", "HEAD", True) in gh.calls, answer       # not blocking
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: CHECK_OK}, pdf_text=LONG_B, prepare=_old_paper(LONG_A), compare="different")
    assert "可能重复" not in pr_body_of(gh)                                        # a real "different" is just committed


def test_a_similar_repo_file_is_compared_and_the_answer_names_the_file():
    for relation, expect in (("different_set", "COM107_202204_Final_SetB_Question.pdf"),
                             ("different_version", "COM107_202204_Final_v2_Question.pdf"),
                             ("different", "COM107_202204_Final_Question_2.pdf"), ("same", None)):
        git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: CHECK_OK}, pdf_text=LONG_B + "Question 4 upload second page",
                                      prepare=_old_paper(LONG_A + "Question 4 archive second page"), compare=relation)
        assert [c for c in gh.ai_calls if c[0] == "compare"] == [("compare", 0)], relation
        asked = next(t for t in gh.ai_text if "<file_a>" in t)
        mine, theirs = asked.split("<file_b>")
        assert "inductor" in mine and "upload second page" in mine                 # the first two pages of each file
        assert "capacitor" in theirs and "archive second page" in theirs
        assert committed_pdfs(tmp) == ([f"EEE/COM107_Circuits_and_Devices/2022-04/Past-Papers/{expect}"] if expect else []), relation
        body = issue_comment_or_body(gh)
        assert "查重比较 | Duplicate comparisons: $0.0030" in body, relation
        if relation == "different_set":
            assert "另一套" in body


def test_scanned_files_skip_the_duplicate_check_and_say_so():
    import dedupe
    calls, real = [], dedupe.candidates
    dedupe.candidates = lambda *a, **kw: calls.append(a) or real(*a, **kw)
    try:
        git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: CHECK_OK}, scanned=True, prepare=_old_paper(LONG_A))
    finally:
        dedupe.candidates = real
    assert calls == [] and committed_pdfs(tmp) == [OLD.replace("_Question.pdf", "_Question_2.pdf")]
    row = next(l for l in pr_body_of(gh).splitlines() if l.startswith("| #1 "))
    assert "扫描件，没有查近似重复" in row


def test_spend_covers_the_re_check_and_the_comparison():
    found = dict(CHECK_OK, personal_data="yes", redactions=BOX_SIG)
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, scanned=True, verify="no")
    line = next(l for l in _section(pr_body_of(gh), "## 本次 AI 花费", "## 维护者清单").splitlines() if "Sorting" in l)
    assert "打码复查 | Redaction re-checks: $0.0100" in line and "合计 | Total: $0.0310" in line
    assert abs(gh.cache["spent_usd"] - 0.031) < 1e-9


# ---- fix round 1: label filter by value, /retry for failed redactions, fresh answers, copies in the waiting folder

PROMPT_LABELS = ["学号 | student ID", "Moodle 密钥 | enrolment key", "微信 | WeChat", "姓名 | name", "二维码 | QR code"]
VALUE_LABELS = ["学号 2109300123", "身份证 990101-14-5678", "电话 012-345 6789"]


def test_value_re_matches_values_not_words():
    for v in ("2109300123", "990101-14-5678", "012-345 6789", "EEE2109300", "eee2109300", "a.b@gmail.com", "+6012-3456789",
              "13812345678", "123 45 67", "1234567", "学号2109300123", "AB12345678", "No.0123456789", "COM107 2109300123"):
        assert intake.VALUE_RE.search(v), v
    for v in PROMPT_LABELS + ["签名 | signature", "COM107", "Final 60%, Midterm 40%", "tan@xmu.edu.my", "第 12 页", "12345",
                              "2022-04", "Final 50%", "Quiz 1 10%", "123456", "COM107 2022-04 期末 | COM107 final, 2022-04",
                              "MPU3.1 2022-04", "60.5 2022-04"]:
        assert not intake.VALUE_RE.search(v), v
    from cache import scrub
    kept = scrub({"check": {"s": {"summary": "COM107 2022-04 期末试卷 | COM107 final, 2022-04", "weighting": "Quiz 1 10%, Final 50%",
                                  "notes": "身份证 990101-14-5678", "redactions": [{"page": 1, "what": "学号 2109300123"},
                                                                                   {"page": 1, "what": "电话 012-345 6789"}]}}})
    assert kept["check"]["s"]["summary"].startswith("COM107 2022-04") and kept["check"]["s"]["weighting"] == "Quiz 1 10%, Final 50%"
    assert kept["check"]["s"]["notes"] == "" and [r["what"] for r in kept["check"]["s"]["redactions"]] == ["", ""]


KINDS = {"name": "姓名 | name", "student_id": "学号 | student ID", "signature": "签名 | signature", "photo": "照片 | photo",
         "qr_code": "二维码 | QR code", "phone": "电话 | phone", "email": "邮箱 | email", "address": "地址 | address",
         "key": "密钥 | key"}


def test_redaction_kinds_are_shown_with_fixed_labels_and_anything_else_is_other():   # final review, fold-in 8
    assert intake.WHAT_TEXT == dict(KINDS, other="其他 | other")
    reds = [{"page": 1, "kind": "box", "box": [0, 0, 300, 50 + 10 * i], "what": w}
            for i, w in enumerate(list(KINDS) + PROMPT_LABELS + VALUE_LABELS + ["other", 3])]
    found = dict(CHECK_OK, personal_data="yes", redactions=reds, summary="身份证 990101-14-5678 | ID 990101-14-5678",
                 notes="电话 012-345 6789")
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, scanned=True, verify="no")
    body = pr_body_of(gh)
    line = next(l for l in _section(body, "## 打码记录", "## 登记").splitlines() if l.startswith("- #1"))
    for label in KINDS.values():
        assert f"{label} ×1" in line, label
    assert f"其他 | other ×{len(PROMPT_LABELS + VALUE_LABELS) + 2}" in line and "`" not in line   # our labels, not Claude's
    for label in PROMPT_LABELS[1:3]:
        assert label not in line, label
    saved = gh.cache_writes[-1]["check"][_sha_of(gh, 1)]
    assert [r["what"] for r in saved["redactions"]] == list(KINDS) + [""] * len(PROMPT_LABELS + VALUE_LABELS) + ["other", ""]
    assert saved["summary"] == "" and saved["notes"] == ""                         # digit runs in free text too
    for v in ("2109300123", "990101-14-5678", "012-345 6789"):
        for where in (gh.calls, gh.cache_writes, git.calls):
            assert v not in json.dumps(where, ensure_ascii=False), v


def test_a_failed_redaction_waits_for_retry_and_retry_checks_it_again():
    two = [dict(TEXT_ID[0]), {"page": 1, "kind": "text", "text": "Nobody Here", "what": "姓名"}]
    found = dict(CHECK_OK, personal_data="yes", redactions=two)
    page = "ID: EEE2109300 Final Examination COM107"
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, pdf_text=page)
    assert gh.ai_calls == [("sort", 1), ("check", 1)] and committed_pdfs(tmp) == []
    git, gh, tmp = run_with_fakes({}, {1: found}, gh=gh, pdf_text=page)              # an ordinary rebuild
    assert gh.ai_calls == [("sort", 1), ("check", 1)] and committed_pdfs(tmp) == []   # no new check, still held
    assert "打码没完成" in _section(issue_comment_or_body(gh), "## 需要你看", "## 打码记录")
    assert intake.store_commands(_event("/retry"), gh) == 5 and gh.cache["overrides"]["_retry"] == {"seq": 1}
    git, gh, tmp = run_with_fakes({}, {1: dict(CHECK_OK, personal_data="yes", redactions=TEXT_ID)}, gh=gh, pdf_text=page)
    assert gh.ai_calls[-1] == ("check", 1) and committed_pdfs(tmp) == [FINAL] and "EEE2109300" not in text(tmp / FINAL, 1)


def test_a_re_check_that_did_not_pass_stays_held_without_paying_again_until_retry():
    found = dict(CHECK_OK, personal_data="yes", redactions=BOX_SIG)
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, scanned=True, verify="unsure")
    assert committed_pdfs(tmp) == [] and gh.cache["verify"][_sha_of(gh, 1)] == "unsure"
    git, gh, tmp = run_with_fakes({}, {}, gh=gh, scanned=True, verify="no")            # rebuilt without /approve
    assert [c for c in gh.ai_calls if c[0] == "verify"] == [("verify", 1)] and committed_pdfs(tmp) == []
    assert "打码后复查没通过" in _section(issue_comment_or_body(gh), "## 需要你看", "## 打码记录")
    assert intake.store_commands(_event("/retry"), gh) == 5
    git, gh, tmp = run_with_fakes({}, {1: found}, gh=gh, scanned=True, verify="no")     # checked and re-checked again
    assert gh.ai_calls[-2:] == [("check", 1), ("verify", 1)] and committed_pdfs(tmp) == [FINAL]
    assert gh.cache["retry_done"] == 1 and gh.cache["verify"][_sha_of(gh, 1)] == "no"


def test_a_re_check_that_could_not_run_is_tried_by_the_next_rebuild():
    found = dict(CHECK_OK, personal_data="yes", redactions=BOX_SIG)
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: found}, scanned=True, verify=["fail"])
    assert committed_pdfs(tmp) == [] and _sha_of(gh, 1) not in gh.cache.get("verify", {})   # no answer: nothing kept
    line = next(l for l in _section(issue_comment_or_body(gh), "## 打码记录", "## 登记").splitlines() if l.startswith("- #1"))
    assert "之后的重建会再试" in line
    git, gh, tmp = run_with_fakes({}, {}, gh=gh, scanned=True, verify="no")
    assert [c for c in gh.ai_calls if c[0] == "verify"] == [("verify", 1), ("verify", 1)] and committed_pdfs(tmp) == [FINAL]


def test_retry_is_a_number_in_the_overrides_and_the_run_drops_only_failed_results():
    good = dict(CHECK_OK, redactions_rects=[{"page": 1, "rect": [0, 0, 9, 9]}], redactions_ok=True)
    checks = {"ok": good, "plain": CHECK_OK, "bad": dict(good, redactions_ok=False), "unsure": good}
    gh = FakeGH(cache={"check": checks, "verify": {"ok": "no", "unsure": "unsure"}, "items": [], "overrides": {"s": {"skip": True}}})
    assert intake.store_commands(_event("/retry"), gh) == 5
    assert gh.cache["overrides"] == {"s": {"skip": True}, "_retry": {"seq": 1}} and gh.cache["check"] == checks   # nothing dropped yet
    data = json.loads(json.dumps(gh.cache))
    assert intake.apply_retry(data) and sorted(data["check"]) == ["ok", "plain"] and data["verify"] == {"ok": "no"}
    assert data["retry_done"] == 1
    again = json.loads(json.dumps(dict(data, check=checks, verify={"ok": "no", "unsure": "unsure"})))
    assert not intake.apply_retry(again) and again["check"] == checks           # no new number: nothing is dropped
    assert intake.store_commands(_event("/retry"), gh) == 5 and gh.cache["overrides"]["_retry"] == {"seq": 2}
    for broken in ({"_retry": "x"}, {"_retry": {"seq": "2"}}, {}):
        assert not intake.apply_retry({"overrides": broken, "check": dict(checks)})


def test_a_retry_stored_while_a_rebuild_runs_still_re_checks_on_the_next_run():
    two = [dict(TEXT_ID[0]), {"page": 1, "kind": "text", "text": "Nobody Here", "what": "姓名"}]
    page = "ID: EEE2109300 Final Examination COM107"
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: dict(CHECK_OK, personal_data="yes", redactions=two)}, pdf_text=page)
    sha = _sha_of(gh, 1)
    assert gh.cache["check"][sha]["redactions_ok"] is False
    stored = []                                  # file 2 is new, so this rebuild asks Claude: /retry lands meanwhile
    git, gh, tmp = run_with_fakes({2: dict(SORT_OK, label="Midterm", assessment="Midterm")}, {2: CHECK_OK}, gh=gh,
                                  pdf_text=page, during=lambda: stored.append(intake.store_commands(_event("/retry"), gh)))
    assert stored == [5] and gh.ai_calls.count(("check", 1)) == 1              # this run did not act on it
    assert gh.cache["check"][sha]["redactions_ok"] is False and gh.cache["overrides"]["_retry"] == {"seq": 1}
    assert gh.cache.get("retry_done", 0) == 0                                  # its save kept the number, not acted on
    git, gh, tmp = run_with_fakes({}, {1: dict(CHECK_OK, personal_data="yes", redactions=TEXT_ID), 2: CHECK_OK}, gh=gh, pdf_text=page)
    assert gh.ai_calls.count(("check", 1)) == 2 and gh.cache["retry_done"] == 1 and FINAL in committed_pdfs(tmp)
    git, gh, tmp = run_with_fakes({}, {}, gh=gh, pdf_text=page)                 # no new number: nothing asked again
    assert gh.ai_calls.count(("check", 1)) == 2 and gh.ai_calls.count(("check", 2)) == 1


def test_overrides_keys_that_are_not_files_never_reach_decide():
    import decide
    seen, real = [], decide.decide
    decide.decide = lambda *a, **k: seen.append(a[4]) or real(*a, **k)
    try:
        gh = FakeGH(cache={"overrides": {"_retry": {"seq": 3, "skip": True, "approved_by": "x"}}})
        git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: CHECK_OK}, gh=gh)
    finally:
        decide.decide = real
    assert seen and all(o is None for o in seen) and committed_pdfs(tmp) == [FINAL]


def test_bookkeeping_keys_in_a_fresh_answer_are_never_trusted():
    page = "ID: EEE2109300 Final Examination COM107"
    no_text = dict(CHECK_OK, personal_data="yes", redactions=[{"page": 1, "kind": "text", "box": [0, 0, 0, 0], "what": "学号"}],
                   redactions_rects=[], redactions_ok=True)
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: no_text}, pdf_text=page)
    assert committed_pdfs(tmp) == [] and "没有打码副本" in _section(issue_comment_or_body(gh), "## 需要你看", "## 打码记录")
    bogus = dict(CHECK_OK, personal_data="yes", redactions=TEXT_ID, redactions_rects=[{"page": 1, "rect": [0, 0, 1, 1]}],
                 redactions_ok=True, verify="no")
    git, gh, tmp = run_with_fakes({1: SORT_OK}, {1: bogus}, pdf_text=page)
    assert committed_pdfs(tmp) == [FINAL] and "EEE2109300" not in text(tmp / FINAL, 1)        # the text was blacked out
    sha = _sha_of(gh, 1)
    assert gh.cache["check"][sha]["redactions_rects"] != [{"page": 1, "rect": [0, 0, 1, 1]}] and "verify" not in gh.cache["check"][sha]
    empty = dict(CHECK_OK, personal_data="yes", redactions=[{"page": 1, "kind": "text", "box": [0, 0, 0, 0], "what": "学号"}],
                 redactions_rects=[], redactions_ok=True)                                    # cached, but no rectangles
    git, gh, tmp = run_with_fakes({}, {}, gh=FakeGH(cache={"sort": {sha: SORT_OK}, "check": {sha: empty}}), pdf_text=page)
    assert committed_pdfs(tmp) == [] and gh.ai_calls == []


def test_the_waiting_folder_gets_the_redacted_copy_too():
    page = "ID: EEE2109300 Final Examination COM107"
    found = dict(CHECK_OK, personal_data="yes", redactions=TEXT_ID)
    unsure = dict(SORT_OK, course_code="", course_name="", confident=False)                 # inbox: course unknown
    git, gh, tmp = run_with_fakes({1: unsure}, {1: found}, pdf_text=page)
    assert committed_pdfs(tmp) == ["_待整理/issue-5/item-1.pdf"] and "EEE2109300" not in text(tmp / "_待整理/issue-5/item-1.pdf", 1)
    new = dict(SORT_OK, course_code="EEE999", course_name="")                                # parked: no folder, no name
    git, gh, tmp = run_with_fakes({1: new}, {1: found}, pdf_text=page)
    assert committed_pdfs(tmp) == ["_待整理/issue-5/item-1.pdf"] and "EEE2109300" not in text(tmp / "_待整理/issue-5/item-1.pdf", 1)
    assert "Final Examination" in text(tmp / "_待整理/issue-5/item-1.pdf", 1)


def test_comment_event_applies_a_command():
    event = {"comment": {"id": 9, "body": "/skip #2", "author_association": "OWNER", "user": {"login": "LucasXQY"}},
             "issue": {"number": 5, "labels": [{"name": "upload"}], "pull_request": None}}
    gh = FakeGH(cache={"items": [{"k": 2, "sha": "s2", "source": "b.pdf"}]})
    intake.handle_comment(event, gh, FakeGit(), tmp_repo(), run=lambda n: None)
    assert gh.cache["overrides"]["s2"] == {"skip": True} and ("react", 9, "+1") in gh.calls
    gh2 = FakeGH(); event["comment"]["author_association"] = "NONE"
    intake.handle_comment(event, gh2, FakeGit(), tmp_repo(), run=lambda n: None)
    assert gh2.cache is None and any(c[0] == "comment" for c in gh2.calls)


def test_comment_event_on_the_pr_retry_and_mistakes():
    items = {"items": [{"k": 2, "sha": "s2", "source": "b.pdf"}]}
    def event(body, number=40, pr=True, labels=(), who="MEMBER"):
        return {"comment": {"id": 3, "body": body, "author_association": who, "user": {"login": "m-1"}},
                "issue": {"number": number, "labels": [{"name": l} for l in labels], "pull_request": {"url": "u"} if pr else None}}
    ran, gh = [], FakeGH(cache=dict(items))
    intake.handle_comment(event("/place #2 com107 2022-04 Past-Papers Final\n/approve #2"), gh, FakeGit(), tmp_repo(), run=ran.append)
    assert ran == [5] and gh.cache["overrides"]["s2"] == {"place": {"code": "COM107", "session": "2022-04", "type": "Past-Papers", "label": "Final"},
                                                         "approved_by": "m-1"}
    ran, gh = [], FakeGH(cache=dict(items))
    intake.handle_comment(event("/keep #7"), gh, FakeGit(), tmp_repo(), run=ran.append)
    reply = next(c[1] for c in gh.calls if c[0] == "comment")
    assert "#7" in reply and " | " in reply and not gh.cache_writes and ("react", 3, "+1") not in gh.calls and ran == []
    ran, gh = [], FakeGH(cache=dict(items))
    intake.handle_comment(event("/retry", number=5, pr=False, labels=("upload",)), gh, FakeGit(), tmp_repo(), run=ran.append)
    assert ran == [5] and gh.cache["overrides"] == {"_retry": {"seq": 1}} and ("react", 3, "+1") in gh.calls
    for ev, head in ((event("/skip #2", number=5, pr=False), "upload/issue-5"),               # not an upload issue
                     (event("/skip #2"), "feature/x"),                                         # PR from another branch
                     (event("please /skip #2", number=5, pr=False, labels=("upload",)), "upload/issue-5"),
                     (event("/lgtm", number=5, pr=False, labels=("upload",), who="NONE"), "upload/issue-5")):
        ran, gh = [], FakeGH(cache=dict(items), head=head)
        intake.handle_comment(ev, gh, FakeGit(), tmp_repo(), run=ran.append)
        assert ran == [] and gh.calls == [] and not gh.cache_writes, ev


def _event(body, number=5, who="OWNER", pr=False, labels=("upload",)):
    return {"comment": {"id": 4, "body": body, "author_association": who, "user": {"login": "m"}},
            "issue": {"number": number, "labels": [{"name": l} for l in labels], "pull_request": {"url": "u"} if pr else None}}


def _sha(k):
    return __import__("hashlib").sha256(_pdf_bytes(k)).hexdigest()


def _sha_of(gh, k):
    """File k's hash as the run recorded it."""
    return next(i["sha"] for i in gh.cache["items"] if i["k"] == k)


def test_commands_are_stored_and_answered_before_any_rebuild():
    """The ungrouped half of a command: store, answer, and name the issue to rebuild (the grouped job rebuilds it)."""
    import os
    gh = FakeGH(cache={"items": [{"k": 2, "sha": "s2", "source": "b.pdf"}], "check": {"s2": CHECK_OK}})
    assert intake.store_commands(_event("/skip #2"), gh) == 5
    assert gh.cache["overrides"] == {"s2": {"skip": True}} and gh.cache["check"] == {"s2": CHECK_OK} and ("react", 4, "+1") in gh.calls
    assert intake.store_commands(_event("/retry"), gh) == 5 and len(gh.cache_writes) == 2        # a number in the overrides
    assert gh.cache["overrides"] == {"s2": {"skip": True}, "_retry": {"seq": 1}} and gh.cache["check"] == {"s2": CHECK_OK}
    assert intake.store_commands(_event("/skip #9"), gh) is None and len(gh.cache_writes) == 2   # nothing applied
    assert intake.store_commands(_event("/skip #2", who="NONE"), gh) is None and len(gh.cache_writes) == 2
    assert intake.store_commands(_event("hello"), gh) is None
    out = pathlib.Path(tempfile.mkdtemp()) / "out"
    os.environ["GITHUB_OUTPUT"] = str(out)
    try:
        intake.set_output("issue", 5); intake.set_output("issue", None)
    finally:
        del os.environ["GITHUB_OUTPUT"]
    assert out.read_text(encoding="utf-8") == "issue=5\nissue=\n"


def test_a_command_stored_while_a_rebuild_runs_survives_it():
    gh = FakeGH()
    run_with_fakes(SORTS, {1: CHECK_OK, 2: CHECK_OK}, gh=gh)                   # #3's check failed: asked again next time
    stored = []
    run_with_fakes(SORTS, CHECKS, gh=gh, during=lambda: stored.append(intake.store_commands(_event("/keep #4"), gh)))
    assert stored == [5] and gh.ai_calls[-1] == ("check", 3)
    assert gh.cache["overrides"] == {_sha(4): {"keep": True}}                 # the rebuild's save kept the command
    assert gh.cache["check"][_sha(3)]["personal_data"] == "unsure"            # and the command kept the rebuild's verdicts


def test_the_push_sweep_only_dispatches_one_run_per_issue():
    gh = FakeGH(prs=[5, 9], issues=[9, 12])
    assert intake.dispatch_open(gh) == ([5, 9, 12], []) and gh.calls == [("dispatch", 5), ("dispatch", 9), ("dispatch", 12)]
    yml = (ROOT / ".github/workflows/intake.yml").read_text(encoding="utf-8")
    target, rest = yml.split("\n  command-target:")[1].split("\n  command:")
    assert "--dispatch-open" in yml and "actions: write" in yml and "--all-open" not in yml
    assert '--comment-event "$GITHUB_EVENT_PATH"' in target and "concurrency" not in target     # stored ungrouped
    command = rest.split("\n  cleanup:")[0]
    assert "group: intake-${{ needs.command-target.outputs.issue }}" in command and '--issue "$ISSUE"' in command


def test_a_missing_libreoffice_does_not_fail_the_run():                        # final review, finding 7
    yml = (ROOT / ".github/workflows/intake.yml").read_text(encoding="utf-8")
    steps = [l for l in yml.splitlines() if "apt-get install" in l]
    assert len(steps) == 2 and yml.count("Install LibreOffice") == 2
    for line in steps:
        assert line.rstrip().endswith('|| echo "LibreOffice unavailable; Office files will wait for review"; fi'), line


def test_a_failed_dispatch_does_not_stop_the_sweep():
    class Flaky(FakeGH):
        def dispatch(self, n):
            if n == 9:
                raise RuntimeError("gh workflow run failed (1): HTTP 500")
            super().dispatch(n)
    gh = Flaky(prs=[5, 9], issues=[12])
    assert intake.dispatch_open(gh) == ([5, 12], [9]) and gh.calls == [("dispatch", 5), ("dispatch", 12)]
    real = intake.GH
    intake.GH = lambda: Flaky(prs=[9])
    try:
        assert intake.main(["--dispatch-open"]) == 1                          # the sweep job fails visibly
        intake.GH = lambda: Flaky(prs=[5])
        assert intake.main(["--dispatch-open"]) == 0
    finally:
        intake.GH = real


def test_fork_prs_never_name_an_upload():
    """Anyone can open a PR from a fork whose branch is called upload/issue-<n>: only this repository's PRs count."""
    seen = []

    class Listing(intake.GH):
        def _run(self, *args, input=None):
            seen.append(args)
            return json.dumps([{"headRefName": "upload/issue-5", "isCrossRepository": False},
                               {"headRefName": "upload/issue-6", "isCrossRepository": True},
                               {"headRefName": "upload/issue-7"},                       # field missing: not trusted
                               {"headRefName": "feature/x", "isCrossRepository": False}])
    assert Listing("o/r").open_upload_prs() == [5]
    assert "isCrossRepository" in seen[0][seen[0].index("--json") + 1]


def test_run_issue_leaves_issues_without_the_upload_label_alone():
    gh = FakeGH(_body(), labels=("question",)); git = FakeGit()
    asks = fake_claude(gh, {1: SORT_OK}, {1: CHECK_OK})
    intake.run_issue(5, _repo_copy(), gh, git, None, pathlib.Path(tempfile.mkdtemp()), asks=asks, budget=FakeBudget())
    assert gh.calls == [] and git.calls == [] and gh.cache_writes == [] and gh.cache is None and gh.ai_calls == []
    bad = FakeGH(_body().replace("2022-04", "soon"), labels=())                # an invalid "form" on a stray issue: still silent
    intake.run_issue(6, _repo_copy(), bad, FakeGit(), None, pathlib.Path(tempfile.mkdtemp()), asks={})
    assert bad.calls == []


def test_claude_text_cannot_close_issues_or_ping():
    git, gh, _ = run_with_fakes({1: SORT_OK}, {1: dict(CHECK_OK, summary="fixes #12 @someone")})
    body = pr_body_of(gh)
    assert re.search(r"`[^`\n]*fixes #12 @someone[^`\n]*`", body) and body.count("@someone") == 1
    git, gh, _ = run_with_fakes({1: dict(SORT_OK, note="closes #3 @zed")}, {1: dict(CHECK_OK, summary="")})
    body = pr_body_of(gh)                                                   # the sort note stands in for a summary
    assert re.search(r"`[^`\n]*closes #3 @zed[^`\n]*`", body) and body.count("@zed") == 1


def test_nothing_committable_with_an_older_pr_updates_and_blocks_it():
    gh = FakeGH(pr=7)
    git, gh, _ = run_with_fakes({1: SORT_OK}, {1: dict(CHECK_OK, personal_data="unsure")}, gh=gh)
    assert git.calls == [("reset", "upload/issue-5")]                       # nothing committed, nothing pushed
    body = pr_body_of(gh)
    assert _in_order(body, SECTIONS + ("Refs #5",)) and "Closes" not in body and "#1" in body.split("## 需要你看")[1]
    assert ("draft", 7, True) in gh.calls and ("status", "PRHEAD7", False) in gh.calls
    assert [c[0] for c in gh.calls].count("upsert_comment") == 1


def test_run_issue_glue_with_fakes():
    tmp = _repo_copy(); w = pathlib.Path(tempfile.mkdtemp()); _pdf(w / "paper.pdf")
    real = intake.download
    intake.download = lambda links, work: ([("paper.pdf", w / "paper.pdf")], [("dead.pdf", "下载失败 | download failed: 404")])
    try:
        gh = FakeGH(_body().replace("EEE\n", "AIT\n").replace("com107", "MPU3.1").replace(intake.CREDIT_NICK, intake.CREDIT_ANON)); git = FakeGit()
        asks = fake_claude(gh, {1: dict(SORT_OK, course_code="MPU3.1", course_name="")}, {1: CHECK_OK})
        intake.run_issue(5, tmp, gh, git, "https://run", w / "work", asks=asks, budget=FakeBudget())
        assert git.calls[0] == ("reset", "upload/issue-5") and git.calls[-1] == ("push", "upload/issue-5")
        assert git.calls[1][2] == intake.BOT_AUTHOR and git.calls[1][1].startswith("content: add files from #5\n")
        assert "GE/MPU3.1_Introduction_to_the_Constitution_of_Malaysia" in git.calls[1][1]
        pr = next(c for c in gh.calls if c[0] == "pr")
        assert pr[1] == "[上传 | Upload] #5：1 个文件 | files（MPU3.1）" and "Closes #5" in pr[2]
        assert "GE/MPU3.1_Introduction" in pr[2] and "CONTRIBUTORS.md" in pr[2] and "dead.pdf" in pr[2]
        first = next(c for c in gh.calls if c[0] == "comment")
        assert "https://example/pr/1" in first[1] and "dead.pdf" in first[1]
        assert ("label", ("pr-opened",), ()) in gh.calls and "comment_pr" not in [c[0] for c in gh.calls]
        n_ai, n_comments = len(gh.ai_calls), [c[0] for c in gh.calls].count("comment")
        intake.run_issue(5, _repo_copy(), gh, FakeGit(), "https://run", w / "work2", asks=asks, budget=FakeBudget())
        assert len(gh.ai_calls) == n_ai and [c[0] for c in gh.calls].count("comment") == n_comments   # a rebuild: quiet, free
        unticked = _body().replace("- [x] " + intake.CHECKS[0], "- [ ] " + intake.CHECKS[0])
        gh2 = FakeGH(unticked); git2 = FakeGit()                                               # invalid form
        intake.run_issue(6, tmp, gh2, git2, None, w / "work", asks={})
        assert git2.calls == [] and [c[0] for c in gh2.calls] == ["comment", "label"] and "Unticked" in gh2.calls[0][1]
        gh3 = FakeGH(unticked, labels=("upload", "needs-info"))        # sweep: already told, stay quiet
        intake.run_issue(6, tmp, gh3, FakeGit(), None, w / "work", sweep=True, asks={})
        assert gh3.calls == []
    finally:
        intake.download = real


def _decide_bits():
    from decide import Decision, Check
    from items import Item
    return Decision, Check, Item


def _mk_items(w, specs):
    Decision, Check, Item = _decide_bits()
    items = {}
    for k, text in specs:
        p = _pdf(w / f"orig_{k}_张三.pdf", text=text) or (w / f"orig_{k}_张三.pdf")
        items[k] = Item(k, f"x{k}.pdf", p, "0" * 64, "pdf", 1, False, [text])
    return items


def _with_course(root, folder):
    import shutil
    (root / "EEE" / folder).mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "EEE" / folder / "README.md", root / "EEE" / folder / "README.md")


def test_apply_decisions_files_each_item_and_registers_each_course():
    Decision, Check, Item = _decide_bits()
    root = _repo_copy(); w = pathlib.Path(tempfile.mkdtemp())
    _with_course(root, "COM103_Electronic_Engineering")
    items = _mk_items(w, [(1, "Q1"), (2, "Lab 2"), (3, "?")])
    f = intake.parse_form(_body())
    ds = [Decision(1, "commit", "COM107", "", "2022-04", "Past-Papers", "Final"),
          Decision(2, "commit", "COM103", "", "unknown", "Labs", "Lab2_Manual"),
          Decision(3, "inbox"), Decision(4, "skip")]
    pl = intake.apply_decisions(root, f, ds, items, "someone", 12)
    paths = dict(pl.placed)
    assert paths[1].endswith("/2022-04/Past-Papers/COM107_202204_Final_Question.pdf")
    assert paths[2].endswith("/unknown/Labs/COM103_unknown_Lab2_Manual.pdf")
    assert pl.inbox == ["_待整理/issue-12/item-3.pdf"] and (root / pl.inbox[0]).exists() and not pl.parked
    assert len(pl.courses) == 2 and "2022-04" in (root / (paths[1].split("/2022-04/")[0] + "/README.md")).read_text(encoding="utf-8")
    assert not any("张三" in str(p) for p in root.rglob("*")) and set(pl.meta) == {paths[1], paths[2], pl.inbox[0]}
    g = intake.parse_form(_body().replace(intake.CREDIT_NICK, intake.CREDIT_LOGIN))
    root2 = _repo_copy(); _with_course(root2, "COM103_Electronic_Engineering")
    intake.apply_decisions(root2, g, ds, items, "newbie", 12)
    assert "1 份 COM107 2022-04 Past-Papers、1 份 COM103 unknown Labs / 1 Past-Papers files for COM107 2022-04, 1 Labs files for COM103 unknown" in (root2 / "CONTRIBUTORS.md").read_text(encoding="utf-8")   # committed files only, every course described


def test_apply_decisions_no_timeline_row_for_unknown_session_of_other_course():
    Decision, Check, Item = _decide_bits()
    root = _repo_copy(); w = pathlib.Path(tempfile.mkdtemp())
    _with_course(root, "COM103_Electronic_Engineering")
    items = _mk_items(w, [(1, "a"), (2, "b")])
    f = intake.parse_form(_body())
    ds = [Decision(1, "commit", "COM107", "", "2022-04", "Past-Papers", "Final"),
          Decision(2, "commit", "COM103", "", "unknown", "Labs", "Lab")]
    intake.apply_decisions(root, f, ds, items, "someone", 1)
    eee = (root / "EEE/README.md").read_text(encoding="utf-8")
    assert "| 2022-04 | [COM107](COM107_Circuits_and_Devices/) |" in eee
    assert not any(l.startswith("| unknown | [COM103]") for l in eee.splitlines())
    # a known session of another course does get a row, with that session as the taken-term
    items2 = _mk_items(w, [(1, "a")])
    intake.apply_decisions(root, f, [Decision(1, "commit", "COM103", "", "2021-09", "Labs", "Lab")], items2, "someone", 2)
    assert "| 2021-09 | [COM103](COM103_Electronic_Engineering/) |" in (root / "EEE/README.md").read_text(encoding="utf-8")


def test_apply_decisions_new_course_needs_a_name_else_parks():
    Decision, Check, Item = _decide_bits()
    root = _repo_copy(); w = pathlib.Path(tempfile.mkdtemp())
    items = _mk_items(w, [(1, "a"), (2, "b"), (3, "c")])
    f = intake.parse_form(_body())
    ds = [Decision(1, "commit", "EEE777", "", "2022-04", "Labs", "Lab"),
          Decision(2, "commit", "EEE888", "Brand New", "2022-04", "Labs", "Lab"),
          Decision(3, "commit", "../../x", "", "2022-04", "Labs", "Lab")]
    pl = intake.apply_decisions(root, f, ds, items, "someone", 7)
    assert [k for k, _ in pl.parked] == [1, 3] and all(" | " in r for _, r in pl.parked)
    assert (root / "_待整理/issue-7/item-1.pdf").exists() and (root / "_待整理/issue-7/item-3.pdf").exists()
    assert not list((root / "EEE").glob("EEE777*")) and not (root / "x").exists()
    assert pl.created_readme and pl.courses == ["EEE/EEE888_Brand_New"] and pl.inbox == []
    assert (root / "EEE/EEE888_Brand_New/README.md").read_text(encoding="utf-8").startswith("# EEE888 Brand New")
    g = intake.parse_form(_body().replace("EEE\n", "AIT\n"))
    pl2 = intake.apply_decisions(root, g, [Decision(1, "commit", "G0777", "New Elective", "2022-04", "Notes", "")], items, "someone", 8)
    assert pl2.courses == ["GE/G0777_New_Elective"]
    assert "| 2022-04 | [G0777](../GE/G0777_New_Elective/) |" in (root / "AIT/README.md").read_text(encoding="utf-8")


def test_apply_decisions_lecturer_weighting_notes_per_course_and_files_override():
    Decision, Check, Item = _decide_bits()
    root = _repo_copy(); w = pathlib.Path(tempfile.mkdtemp())
    _with_course(root, "COM103_Electronic_Engineering")
    items = _mk_items(w, [(1, "a"), (2, "b")])
    red = _pdf(w / "redacted.pdf", text="REDACTED COPY") or w / "redacted.pdf"
    f = intake.parse_form(_body().replace("### 老师 | Lecturer\n\n_No response_", "### 老师 | Lecturer\n\nDr Form").replace(
        "### 备注 / 小贴士 | Notes\n\n_No response_", "### 备注 / 小贴士 | Notes\n\nForm note"))
    ck = Check("none", "none", "none", "", "", [], ["Dr Ai", "Dr Bee"], "Exam 60%")
    ds = [Decision(1, "commit", "COM107", "", "2020-01", "Past-Papers", "Final"),
          Decision(2, "commit", "COM103", "", "2020-01", "Labs", "Lab")]
    intake.apply_decisions(root, f, ds, items, "someone", 3, files={2: red}, checks={2: ck})
    r107 = (root / "EEE/COM107_Circuits_and_Devices/README.md").read_text(encoding="utf-8")
    r103 = (root / "EEE/COM103_Electronic_Engineering/README.md").read_text(encoding="utf-8")
    row107 = next(l for l in r107.splitlines() if "2020-01" in l and l.startswith("|"))
    row103 = next(l for l in r103.splitlines() if "2020-01" in l and l.startswith("|"))
    assert "Dr Form" in row107 and "Form note" in row107 and "Dr Ai, Dr Bee" not in row107
    assert "Dr Ai, Dr Bee" in row103 and "Exam 60%" in row103 and "Form note" not in row103 and "Dr Form" not in row103
    import fitz
    placed = next((root / "EEE/COM103_Electronic_Engineering/2020-01/Labs").glob("*.pdf"))
    assert "REDACTED COPY" in fitz.open(placed)[0].get_text()


def test_apply_decisions_lists_a_course_once_and_single_group_wording_is_unchanged():
    Decision, Check, Item = _decide_bits()
    root = _repo_copy(); w = pathlib.Path(tempfile.mkdtemp())
    items = _mk_items(w, [(1, "a"), (2, "b"), (3, "c")])
    g = intake.parse_form(_body().replace(intake.CREDIT_NICK, intake.CREDIT_LOGIN))
    ds = [Decision(1, "commit", "COM107", "", "2020-01", "Labs", "A"), Decision(2, "commit", "COM107", "", "2020-09", "Labs", "B"),
          Decision(3, "commit", "COM107", "", "2020-09", "Labs", "C")]
    pl = intake.apply_decisions(root, g, ds, items, "newbie", 5)
    assert pl.courses == ["EEE/COM107_Circuits_and_Devices"] and pl.course_dir == pl.courses[0]
    c = (root / "CONTRIBUTORS.md").read_text(encoding="utf-8")
    assert "1 份 COM107 2020-01 Labs、2 份 COM107 2020-09 Labs / " in c
    root2 = _repo_copy()
    intake.apply_decisions(root2, g, [Decision(1, "commit", "COM107", "", "2020-01", "Labs", "A"), Decision(2, "commit", "COM107", "", "2020-01", "Labs", "B")],
                           items, "newbie", 5)
    assert "2 份 COM107 2020-01 Labs / 2 Labs files for COM107 2020-01 |" in (root2 / "CONTRIBUTORS.md").read_text(encoding="utf-8")
    rows = [(1, "COM107", "2022-04", "Labs"), (2, "COM103", "unknown", "Tutorials"), (1, "COM104", "2021-09", "Notes"), (1, "COM105", "2020-01", "Tips")]
    out = intake.update_contributors((ROOT / "CONTRIBUTORS.md").read_text(encoding="utf-8"), g, "zed", 5, rows)
    row = next(l for l in out.splitlines() if l.startswith("| zed"))
    assert "Notes等共 5 份 / " in row and "5 files in all" in row and "COM105" not in row


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"ok {len(tests)} tests")
