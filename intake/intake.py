"""上传通道机器人：把一个「上传资料」Issue 变成一个待审核的 PR。
Upload-channel bot: turns an "upload material" issue into a pull request for review.

设计见 intake/DESIGN.md；测试 python intake/test_intake.py。
Design in intake/DESIGN.md; tests: python intake/test_intake.py.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

PROGS = ("EEE", "AIT", "ROBO")
SHARED = "GE"   # university-wide courses: G electives and MPU compulsory subjects, whichever programme uploads them
SUBS =("Past-Papers", "Syllabus", "Assignments", "Labs", "Tutorials",
        "Handbooks", "Notes", "Exam-Review", "Tips")
CODE_RE = re.compile(r"^[A-Z]{1,4}(?:\d{3,4}|\d\.\d)$", re.ASCII)     # ASCII digits only: these shape repo paths
SESSION_RE = re.compile(r"^\d{4}-\d{2}$", re.ASCII)

# form field titles are the parse keys; .github/ISSUE_TEMPLATE/upload.yml must use them byte for byte
FIELDS = {
    "prog": "专业 | Programme",
    "code": "课程代码 | Course code",
    "name": "课程名称 | Course name",
    "session": "学期 | Session",
    "type": "资料类型 | Type",
    "files": "文件 | Files",
    "assessment": "考核名称 | Assessment（试卷用）",
    "lecturer": "老师 | Lecturer",
    "weighting": "考核比例 | Assessment weighting",
    "taken": "你修这门课的学期 | Session you took it",
    "credit": "署名方式 | Credit",
    "nickname": "昵称 | Nickname",
    "intake": "入学年份 | Intake year",
    "notes": "备注 / 小贴士 | Notes",
    "checklist": "确认 | Checklist",
}
CREDIT_LOGIN = "用我的 GitHub 用户名 | My GitHub username"
CREDIT_NICK = "用下面填的昵称 | The nickname below"
CREDIT_ANON = "匿名 | Anonymous"
CREDITS = (CREDIT_LOGIN, CREDIT_NICK, CREDIT_ANON)
CHECKS = (
    "没有答案、评分标准、参考解答 | No answers, marking schemes or model solutions",
    "我知道附件任何人都能下载，已尽量涂掉学号、姓名、签名、私人联系方式和 Moodle 密钥（进仓库前机器人还会再查一遍）"
    " | I know attachments can be downloaded by anyone, and have blacked out student IDs, names, signatures, private contacts"
    " and Moodle keys where I could (the bot checks again before anything enters the repo)",
    "这门课是我自己修的，或我有权分享这些文件 | I took this course myself, or I have the right to share these files",
    "我已读 DISCLAIMER，同意这些文件免费公开、非商业使用 | I have read the DISCLAIMER and agree to these files being public, free and non-commercial",
)

# earlier wording of a checklist box, still accepted so an issue opened before the change rebuilds unchanged
OLD_CHECKS = {1: ("没有学生姓名、学号、签名，没有老师的私人联系方式和 Moodle 密钥 | No student names, IDs or signatures; "
                  "no lecturers' private contacts or Moodle keys",)}

NO_RESPONSE = "_No response_"
NO_SELECTION = "None"                                    # what GitHub writes for an optional dropdown left unselected
DROPDOWNS = ("prog", "type", "credit", "intake")         # the form's dropdown fields
HEADING_RE = re.compile(r"^### (.*?)\s*$", re.M)
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+)\)")
# the two attachment URL shapes GitHub produces for files (images live under user-attachments/assets and are not taken)
ATTACH_RE = re.compile(r"^https://github\.com/(?:user-attachments/files|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/files)/\d+/[^/\s]+$")


@dataclass
class Form:
    prog: str = ""
    code: str = ""
    name: str = ""
    session: str = ""
    type: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)
    assessment: str = ""
    lecturer: str = ""
    weighting: str = ""
    taken: str = ""
    credit: str = ""
    nickname: str = ""
    intake: str = ""
    notes: str = ""
    checked: list[bool] = field(default_factory=lambda: [False] * len(CHECKS))


def _clean(value: str, dropdown: bool = False) -> str:
    value = value.strip()
    return "" if value == NO_RESPONSE or (dropdown and value == NO_SELECTION) else value


# month names, English and Malay; any prefix of three letters or more counts (Apr, April, Sept, Ogos)
_MONTH_NAMES = ("january february march april may june july august september october november december",
                "januari februari mac april mei jun julai ogos september oktober november disember")
MONTHS = {name[:n]: i + 1 for line in _MONTH_NAMES for i, name in enumerate(line.split()) for n in range(3, len(name) + 1)}


def normalize_session(text: str) -> str:
    """The month a hint names, as YYYY-MM, from the ways people write one: 2024/04, 2024.4, 202404, 2024-04-15,
    04/2024, Apr 2024, 2024年4月 and so on. "" when it names no single month (Y2S1, 2024, 2024/25, last year): the
    session is only a hint, so one the bot cannot read is left out and the sorter reads the session off the file."""
    t = unicodedata.normalize("NFKC", text or "").strip().lower()
    for pattern, y, m in ((r"(?<!\d)(20\d\d)\s*(?:[-/._ ]|年)?\s*(\d{1,2})(?!\d)", 1, 2),   # 2024-04, 2024/4, 202404, 2024年4月
                          (r"(?<!\d)(\d{1,2})\s*[-/.]\s*(20\d\d)(?!\d)", 2, 1)):           # 04/2024, 4-2024
        hit = re.search(pattern, t)
        if hit and 1 <= int(hit.group(m)) <= 12:
            return f"{hit.group(y)}-{int(hit.group(m)):02d}"
    for pattern, y, m in ((r"(?<![a-z])([a-z]{3,9})\.?[\s,/-]*(20\d\d)(?!\d)", 2, 1),         # Apr 2024, Sept. 2025
                          (r"(?<!\d)(20\d\d)[\s,/-]*([a-z]{3,9})(?![a-z])", 1, 2)):           # 2024 Apr
        hit = re.search(pattern, t)
        if hit and hit.group(m) in MONTHS:
            return f"{hit.group(y)}-{MONTHS[hit.group(m)]:02d}"
    return ""


def normalize_code(text: str) -> str:
    """The course code in what was typed (com 107, swe-404, "SWE404 Software Engineering", full-width letters), or ""
    when there is none; like the session, the code is a hint, and one the bot cannot read is left out."""
    t = unicodedata.normalize("NFKC", text or "").upper()
    hit = re.search(r"(?<![A-Z])([A-Z]{1,4})[\s_-]*(\d{3,4}|\d\.\d)(?!\d)", t)
    code = hit.group(1) + hit.group(2) if hit else ""
    return code if CODE_RE.match(code) else ""


def parse_form(body: str) -> Form:
    """Split the GitHub-rendered issue body on `### <title>` headings; unknown headings are ignored."""
    body = body.replace("\r\n", "\n")
    sections: dict[str, str] = {}
    parts = HEADING_RE.split(body)  # [preamble, title1, text1, title2, text2, ...]
    for title, text in zip(parts[1::2], parts[2::2]):
        sections[title.strip()] = text
    by_key = {k: _clean(sections.get(t, ""), k in DROPDOWNS) for k, t in FIELDS.items()}
    checklist = sections.get(FIELDS["checklist"], "")
    checked = [any(re.search(r"^\s*- \[[xX]\] " + re.escape(s) + r"\s*$", checklist, re.M) for s in (c, *OLD_CHECKS.get(i, ())))
               for i, c in enumerate(CHECKS)]
    f = Form(prog=by_key["prog"], code=normalize_code(by_key["code"]), name=by_key["name"],
             session=normalize_session(by_key["session"]),
             type=by_key["type"], links=attachment_links(sections.get(FIELDS["files"], "")),
             assessment=by_key["assessment"], lecturer=by_key["lecturer"], weighting=by_key["weighting"],
             taken=normalize_session(by_key["taken"]), credit=by_key["credit"], nickname=by_key["nickname"],
             intake=by_key["intake"], notes=by_key["notes"], checked=checked)
    if not f.taken:
        f.taken = f.session
    return f


def attachment_links(text: str) -> list[tuple[str, str]]:
    """(display name, url) for every markdown link to a GitHub file attachment; duplicates dropped, order kept."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, url in MD_LINK_RE.findall(text):
        if ATTACH_RE.match(url) and url not in seen:
            seen.add(url)
            out.append((unquote(url.rsplit("/", 1)[-1]), url))
    return out


def _short(value: str) -> str:
    value = value.replace("\n", " ")
    return value if len(value) <= 40 else value[:37] + "..."


def validate(f: Form) -> list[str]:
    """Bilingual problem lines; empty when the form can be processed. Course code, name, session and type are
    optional hints for the sorter and never send an upload back: parse_form tidies the code and sessions it can read
    and leaves out the ones it cannot (issue #67)."""
    p: list[str] = []
    if f.prog not in PROGS:
        p.append(f"{FIELDS['prog']}：只能是 {' / '.join(PROGS)} | must be one of {' / '.join(PROGS)}")
    if f.type and f.type not in SUBS:
        p.append(f"{FIELDS['type']}：只能是 {' / '.join(SUBS)} 之一 | must be one of {' / '.join(SUBS)}")
    if not f.links:
        p.append(f"{FIELDS['files']}：没找到附件。把文件拖进「文件」框再提交；照片请先合成一个 PDF，单独的图片不收 "
                 f"| no attachment found. Drag the files into the Files box; photos must be combined into one PDF, "
                 f"images on their own are not accepted")
    if f.credit not in CREDITS:
        p.append(f"{FIELDS['credit']}：请从三个选项里选一个 | pick one of the three options")
    if f.credit == CREDIT_NICK and not f.nickname:
        p.append(f"{FIELDS['nickname']}：选了「用昵称」就要填昵称 | you chose the nickname option, so a nickname is required")
    for sentence, ok in zip(CHECKS, f.checked):
        if not ok:
            p.append(f"未勾选 | Unticked: {sentence}")
    return p


# ---------------------------------------------------------------- files: names, unpacking, limits

ALLOWED = {"pdf", "docx", "pptx", "xlsx", "doc", "ppt", "xls", "txt", "md", "csv"}
MAX_FILES = 60
MAX_BYTES = 300 * 2**20


class IntakeLimit(Exception):
    """Too many files or too many bytes after unpacking; the whole submission is refused."""


def clean_label(stem: str) -> str:
    """Keep [A-Za-z0-9._-]; everything else becomes `_`; runs collapsed; `_` and `.` trimmed at both ends."""
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9._-]", "_", stem)).strip("_.")


def _split_ext(name: str) -> tuple[str, str]:
    stem, dot, ext = name.rpartition(".")
    return (stem, ext.lower()) if dot and stem else (name, "")


def _parts(name: str) -> list[str]:
    """Path components, however the archiver separated them."""
    return re.split(r"[\\/]", name)


def _basename(name: str) -> str:
    return _parts(name)[-1]


def _reject_reason(name: str, ext: str) -> str | None:
    parts = _parts(name)
    if ".." in parts or ".." in parts[-1]:
        return "文件名含 ..，不收 | name contains .., refused"
    if ext == "zip":
        return "zip 里的 zip 不解包，请把文件直接放进一个 zip | nested zip not unpacked, put the files in one zip"
    if ext not in ALLOWED:
        return (f"不收 .{ext or '?'} 文件，只收 {' '.join(sorted(ALLOWED))}；照片请合成一个 PDF "
                f"| .{ext or '?'} files are not accepted, only {' '.join(sorted(ALLOWED))}; combine photos into one PDF")
    return None


def _skip(name: str) -> bool:
    """Entries nobody meant to upload: directories, macOS resource forks, dotfiles."""
    parts = _parts(name)
    return not parts[-1] or "__MACOSX" in parts or parts[-1].startswith(".")


ORIGIN_SEP = " › "   # "<attachment name> › <n>": how a zip member is named outside the zip


def unpack(downloads: list[tuple[str, Path]], work: Path
           ) -> tuple[list[tuple[str, Path]], list[tuple[str, str]], list[str]]:
    """Expand zips, apply the whitelist and the limits.
    Returns (accepted (name, path), rejected (name, reason), origins). `origins[i]` is where accepted file i came from:
    the attachment's own name, or "<zip name> › <n>" for a zip member (n = 1-based position among the zip's
    non-directory, non-skipped entries, shared by accepted and rejected members). Rejected zip members are named
    that way too, so a name chosen by the uploader inside a zip never leaves this function."""
    ok: list[tuple[str, Path]] = []
    bad: list[tuple[str, str]] = []
    origins: list[str] = []
    total = 0

    def accept(name: str, path: Path, origin: str) -> None:
        nonlocal total
        total += path.stat().st_size
        ok.append((name, path))
        origins.append(origin)
        if len(ok) > MAX_FILES:
            raise IntakeLimit(f"文件超过 {MAX_FILES} 份，请分几次提交 | more than {MAX_FILES} files, please split the submission")
        if total > MAX_BYTES:
            raise IntakeLimit(f"解包后超过 {MAX_BYTES // 2**20} MB，请分几次提交 | more than {MAX_BYTES // 2**20} MB after unpacking, please split the submission")

    for i, (name, path) in enumerate(downloads):
        base = _basename(name)
        stem, ext = _split_ext(base)
        if ext == "zip":
            out = work / "unzipped" / str(i)
            out.mkdir(parents=True, exist_ok=True)
            try:
                zf = zipfile.ZipFile(path)
            except zipfile.BadZipFile:
                bad.append((name, "不是有效的 zip | not a valid zip file"))
                continue
            with zf:
                n = 0
                for info in zf.infolist():
                    if info.is_dir() or _skip(info.filename):
                        continue
                    n += 1
                    label = f"{name}{ORIGIN_SEP}{n}"
                    inner = _basename(info.filename)
                    reason = _reject_reason(info.filename, _split_ext(inner)[1])
                    if reason:
                        bad.append((label, reason))
                        continue
                    if info.file_size > MAX_BYTES:
                        raise IntakeLimit(f"zip 里有单个文件超过 {MAX_BYTES // 2**20} MB | a file inside the zip exceeds {MAX_BYTES // 2**20} MB")
                    dest = out / inner
                    k = 1
                    while dest.exists():  # same basename in two folders of the zip
                        k += 1
                        dest = out / f"{k}_{inner}"
                    try:
                        with zf.open(info) as src, open(dest, "wb") as dst:
                            dst.write(src.read())
                    except Exception as e:  # encrypted, corrupt, unsupported compression: reported, not fatal
                        dest.unlink(missing_ok=True)
                        bad.append((label, f"zip 里这一项读不出来，可能加密或损坏（{type(e).__name__}） "
                                                   f"| this zip member could not be read, encrypted or corrupt ({type(e).__name__})"))
                        continue
                    accept(inner, dest, label)
            continue
        if _skip(base):
            continue
        reason = _reject_reason(name, ext)
        if reason:
            bad.append((name, reason))
        else:
            accept(base, path, name)
    return ok, bad, origins


# ---------------------------------------------------------------- metadata, content checks, thumbnails

OFFICE = {"docx", "pptx", "xlsx"}
LEGACY = {"doc", "ppt", "xls"}
CORE_TAGS = ("dc:creator", "cp:lastModifiedBy", "dc:title", "dc:subject", "cp:keywords", "dc:description")
APP_TAGS = ("Company", "Manager")
PDF_META_KEYS_IGNORED = ("format", "encryption")  # PyMuPDF always reports these; they carry no personal data

PII_RE = re.compile(
    r"\b[A-Z]{3}\d{7,10}\b"                                  # student id
    r"|student\s*(?:id|no)\b"
    r"|[\w.+-]+@(?!xmu\.edu\.my\b)[\w-]+(?:\.[\w-]+)+"       # any email except the university's
    r"|\b(?:\+?6?01\d[- ]?\d{7,8}|1[3-9]\d{9})\b"            # Malaysian / Chinese mobile numbers
    r"|enrolment key|passcode|password|zoom|meeting id|wechat|whatsapp",
    re.I)
# Value-shaped personal data only, no keywords. Claude's free text (summaries, notes, lecturers, weighting) and the
# sort's names (label, assessment, set, course name) are held to both: see personal(). Its digit classes are ASCII
# ([0-9]), as in CODE_RE and SESSION_RE; \w stays Unicode, so an email is caught whatever its letters.
VALUE_RE = re.compile(
    r"\b[A-Z]{3}[0-9]{7,10}\b"                               # student id
    r"|[\w.+-]+@(?!xmu\.edu\.my\b)[\w-]+(?:\.[\w-]+)+"       # any email except the university's
    r"|\b(?:\+?6?01[0-9][- ]?[0-9]{7,8}|1[3-9][0-9]{9})\b"     # Malaysian / Chinese mobile numbers
    r"|(?<![A-Za-z0-9])(?<![0-9]\.)[0-9](?:[ -]?[0-9]){6,}"  # 7+ digits, single spaces or hyphens between groups (an
                                                             # IC or phone number; not "2022-04", "Quiz 1 10%"), never
                                                             # starting inside a word ("COM107 2022-04", "MPU3.1 2022-04")
    r"|(?<=[A-Za-z])[0-9]{7,}",                              # 7+ digits glued to letters ("ID2109300123")
    re.I)


def personal(text: str) -> bool:
    """Does Claude's free text look personal: a keyword (PII_RE) or a value (VALUE_RE)?"""
    return bool(PII_RE.search(text) or VALUE_RE.search(text))


# what a redaction is (the check's `what`, a closed list: ai.CHECK_SCHEMA), and how the PR names it
WHAT_TEXT = {"name": "姓名 | name", "student_id": "学号 | student ID", "signature": "签名 | signature", "photo": "照片 | photo",
             "qr_code": "二维码 | QR code", "phone": "电话 | phone", "email": "邮箱 | email", "address": "地址 | address",
             "other": "其他 | other"}
ANS_RE = re.compile(r"marking scheme|answer scheme|model answer|suggested answer|solution|answer key|答案|评分标准|参考解答", re.I)
OFFICE_TEXT_RE = re.compile(r"<(?:w:t|a:t|t)(?:\s[^>]*)?>([^<]*)</(?:w:t|a:t|t)>")


def _blank_tags(xml: bytes, tags: tuple[str, ...]) -> bytes:
    pat = re.compile(rb"<(" + b"|".join(t.encode() for t in tags) + rb")(\s[^>]*)?>.*?</\1>", re.S)
    return pat.sub(lambda m: b"<" + m.group(1) + (m.group(2) or b"") + b"></" + m.group(1) + b">", xml)


def clear_metadata(path: Path) -> str:
    """Strip author-like metadata in place. Returns a short status for the report. A PDF goes through
    redact.scrub_pdf, which every committed PDF gets, redacted or not: annotation authors and contents, pop-ups, file
    attachments, embedded files, piece info, thumbnails, the info dictionary and XMP go; what is drawn stays."""
    ext = _split_ext(path.name)[1]
    tmp = path.with_name(path.name + ".tmp")
    if ext == "pdf":
        try:
            import fitz
            import redact
            with fitz.open(path) as doc:
                redact.scrub_pdf(doc)
                doc.save(tmp, garbage=3, deflate=True)
        except Exception as e:  # corrupt or encrypted PDF: keep the file, tell the maintainer
            tmp.unlink(missing_ok=True)
            return f"PDF 打不开，请手动检查 | PDF could not be processed, check manually ({type(e).__name__})"
        os.replace(tmp, path)
        return "cleared"
    if ext in OFFICE:
        try:
            with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w") as dst:
                for info in src.infolist():
                    data = src.read(info)
                    if info.filename == "docProps/core.xml":
                        data = _blank_tags(data, CORE_TAGS)
                    elif info.filename == "docProps/app.xml":
                        data = _blank_tags(data, APP_TAGS)
                    dst.writestr(info, data, compress_type=info.compress_type)
        except zipfile.BadZipFile:
            tmp.unlink(missing_ok=True)
            return "不是有效的 Office 文件，请手动检查 | not a valid Office file, check manually"
        os.replace(tmp, path)
        return "cleared"
    if ext in LEGACY:
        return "旧格式，请手动检查 | legacy format, check manually"
    return "n/a"


def _matches(rx: re.Pattern, text: str, limit: int = 10) -> list[str]:
    out: list[str] = []
    for m in rx.finditer(text):
        hit = m.group(0).strip()
        if hit and hit not in out:
            out.append(hit)
            if len(out) >= limit:
                break
    return out


def inspect_file(path: Path) -> dict:
    """What the maintainer wants to know per file; nothing here blocks the PR."""
    ext = _split_ext(path.name)[1]
    info = {"size": path.stat().st_size, "pages": None, "meta_empty": True, "pii": [], "answers": [], "low_text": False}
    text = ""
    if ext == "pdf":
        try:
            import fitz
            with fitz.open(path) as doc:
                info["pages"] = len(doc)
                info["meta_empty"] = not any(v for k, v in doc.metadata.items() if k not in PDF_META_KEYS_IGNORED)
                text = "\n".join(page.get_text() for page in doc)
                info["low_text"] = len(doc) > 0 and len(text.strip()) / len(doc) < 50
        except Exception:
            info["meta_empty"] = False  # unreadable: make the maintainer look
    elif ext in OFFICE:
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                if "docProps/core.xml" in names:
                    core = zf.read("docProps/core.xml")
                    info["meta_empty"] = not any(m.group(1).strip() for m in re.finditer(
                        rb"<(?:dc:creator|cp:lastModifiedBy)(?:\s[^>]*)?>(.*?)</", core, re.S))
                text = "\n".join(" ".join(OFFICE_TEXT_RE.findall(zf.read(n).decode("utf-8", "ignore")))
                                 for n in names if n.endswith(".xml"))
        except zipfile.BadZipFile:
            info["meta_empty"] = False
    elif ext in LEGACY:
        info["meta_empty"] = False  # cannot be checked here
        text = path.read_bytes().decode("latin-1", "ignore")
    else:
        text = path.read_text(encoding="utf-8", errors="ignore")
    info["pii"] = _matches(PII_RE, text)
    info["answers"] = _matches(ANS_RE, text)
    return info


# ---------------------------------------------------------------- why a kept file has no check (the "Needs you" list)

NOT_CHECKED = {
    "off": "没有配置 ANTHROPIC_API_KEY | no ANTHROPIC_API_KEY",
    "unchecked": "没检查成（额度用完或调用失败），之后的重建会再试，也可以 /retry "
                 "| not checked (budget used up or a failed call); a later rebuild tries again, or /retry",
    "too_long": "超过 100 页，没有检查 | over 100 pages, not checked",
    "no_view": "没能转成 PDF 给 Claude 看，没有检查 | could not be turned into a PDF for Claude, not checked",
    "unreadable": "PDF 打不开，没有检查 | the PDF cannot be opened, not checked",
    "plain_text": "纯文本文件暂不自动检查 | plain-text files are not checked automatically yet",
    "unsorted": "没分拣成（额度或调用失败），之后的重建会再试，也可以 /retry | not sorted (budget or a failed call); a later rebuild tries again, or /retry",
    "failed": "检查失败 {n} 次，用 `/retry` 再试 | the check failed {n} times; use `/retry` to try again",   # ai.CHECK_TRIES
}


def why_text(key: str, tries: int = 0) -> str:
    """The Needs-you line for a NOT_CHECKED key; "failed" says how many times the check failed."""
    return NOT_CHECKED[key].format(n=tries) if key == "failed" else NOT_CHECKED[key]


def _plain_text(path: Path) -> str:
    """Text of an Office or plain-text file (PDFs go to Claude whole)."""
    ext = _split_ext(path.name)[1]
    if ext in OFFICE:
        try:
            with zipfile.ZipFile(path) as zf:
                return "\n".join(" ".join(OFFICE_TEXT_RE.findall(zf.read(n).decode("utf-8", "ignore")))
                                 for n in zf.namelist() if n.endswith(".xml"))
        except zipfile.BadZipFile:
            return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def thumbnails(pdfs: list[Path], out_png: Path, cols: int = 5, cell: tuple[int, int] = (120, 160)) -> int:
    """First two pages of each PDF as a contact sheet with red file-name labels. Returns the number of cells."""
    import fitz
    cells: list[tuple[str, fitz.Pixmap]] = []
    for pdf in pdfs:
        try:
            with fitz.open(pdf) as doc:
                for i in range(min(2, len(doc))):
                    cells.append((f"{pdf.name} p{i + 1}", doc[i].get_pixmap(dpi=40)))
        except Exception:
            continue
    if not cells:
        return 0
    w, h = cell
    rows = math.ceil(len(cells) / cols)
    sheet = fitz.open()
    page = sheet.new_page(width=cols * w, height=rows * h)
    for k, (label, pix) in enumerate(cells):
        x, y = (k % cols) * w, (k // cols) * h
        page.insert_image(fitz.Rect(x + 4, y + 12, x + w - 4, y + h - 4), pixmap=pix, keep_proportion=True)
        page.insert_text(fitz.Point(x + 4, y + 9), label[:34], fontsize=6, color=(1, 0, 0))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    page.get_pixmap(dpi=96).save(out_png)
    sheet.close()
    return len(cells)


# ---------------------------------------------------------------- registry: course README, timeline, CONTRIBUTORS

ANON = "匿名 / anonymous"
COURSE_TABLE_HEADER = "| 学期 / Session | 老师 / Lecturer | 考核 / Assessment | 资料来自 / Contributed by | 备注 / Notes |"
TIMELINE_HEADING = "### 其他同学 | Other contributors"
TIMELINE_HEADER = "| 学期 / Session | 代码 / Code | 课程 / Course | 贡献者 / Contributor |"
PLACEHOLDER = "| [Name] |"


def _cell(text: str) -> str:
    """User text that goes into a markdown table cell: one line, pipes escaped, no raw HTML."""
    text = re.sub(r"\s*\n\s*", " ", text.strip()).replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("|", "\\|")


def _code(text: str) -> str:
    """User text shown in a PR body or comment: a code span, so it cannot ping, link, close issues or render HTML."""
    text = re.sub(r"\s+", " ", text.strip()).replace("`", "'").replace("<", "\u2039").replace(">", "\u203a")
    return f"`{text or ' '}`"


def credit_name(f: Form, login: str) -> str:
    if f.credit == CREDIT_ANON:
        return ANON
    if f.credit == CREDIT_NICK:
        return _cell(f.nickname)
    return f"[{login}](https://github.com/{login})"


def is_shared(code: str) -> bool:
    """G electives and MPU subjects are taken by every programme, so they live under GE/."""
    return code.upper().startswith(("G", "MPU"))


def find_course_dir(root: Path, code: str) -> Path | None:
    """The existing course folder for a code, whichever programme (or GE/) it sits under."""
    for prog in (*PROGS, SHARED):
        hits = sorted(d for d in (root / prog).glob(f"{code}_*") if d.is_dir())
        if hits:
            return hits[0]
    return None


def course_dir_name(code: str, name: str) -> str:
    return f"{code}_" + re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_-]", "", name.strip().replace(" ", "_"))).strip("_")


def new_course_readme(code: str, name: str) -> str:
    return (f"# {code} {_cell(name)}\n\n"
            "每个修读学期一行；有新学期的资料就加一行。One row per session; add a row when you contribute material from a new session.\n\n"
            f"{COURSE_TABLE_HEADER}\n|---|---|---|---|---|\n\n- 小贴士 / Tips：\n")


def _split_lines(text: str) -> tuple[list[str], str]:
    eol = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n").split("\n"), eol


def _cells(line: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]]


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _table(lines: list[str], header_idx: int) -> tuple[int, int]:
    """(first data row, one past the last data row) of the table whose header is at header_idx."""
    start = header_idx + 2
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    return start, end


def _plain(token: str) -> str:
    """`[name](url)` and `name` are the same person."""
    m = re.fullmatch(r"\[([^\]]+)\]\([^)]*\)", token.strip())
    return (m.group(1) if m else token.strip()).lower()


def _add_token(cell: str, token: str) -> str:
    parts = [t.strip() for t in cell.split(" · ") if t.strip()]
    if _plain(token) not in {_plain(p) for p in parts}:
        parts.append(token)
    return " · ".join(parts)


def update_course_readme(text: str, f: Form, credit: str) -> str:
    lines, eol = _split_lines(text)
    type_link = f"[{f.type}]({f.session}/{f.type}/)"
    notes = _cell(f.notes)
    try:
        header = next(i for i, l in enumerate(lines) if l.startswith("| 学期"))
    except StopIteration:
        lines += ["", COURSE_TABLE_HEADER, "|---|---|---|---|---|"]
        header = len(lines) - 2
    start, end = _table(lines, header)
    insert_at = end
    for i in range(start, end):
        cells = _cells(lines[i])
        if len(cells) < 5:
            cells += [""] * (5 - len(cells))
        m = re.search(r"\d{4}-\d{2}", cells[0])
        placeholder = cells[1] == "[Name]" or "YYYY" in cells[0] or ("[" in cells[0] and "](" not in cells[0])
        if placeholder:
            insert_at = min(insert_at, i)
            continue
        if not m:  # an `unknown` session row: neither a match nor a place to insert before
            continue
        if m.group(0) == f.session:
            cells[0] = f"[{f.session}]({f.session}/)"
            if not cells[1] and f.lecturer:
                cells[1] = _cell(f.lecturer)
            if not cells[2] and f.weighting:
                cells[2] = _cell(f.weighting)
            cells[3] = _add_token(cells[3], credit)
            cells[4] = _add_token(cells[4], type_link)
            if notes:
                cells[4] = _add_token(cells[4], notes)
            lines[i] = _row(cells)
            return eol.join(lines)
        if m.group(0) > f.session:
            insert_at = min(insert_at, i)
    new_row = _row([f"[{f.session}]({f.session}/)", _cell(f.lecturer), _cell(f.weighting), credit,
                    type_link + (f" · {notes}" if notes else "")])
    lines.insert(insert_at, new_row)
    return eol.join(lines)


def update_timeline(text: str, f: Form, credit: str, dir_name: str, link: str | None = None) -> str:
    """Add the uploader's row to a programme's timeline; `link` is the folder relative to that README (../GE/… for GE courses)."""
    lines, eol = _split_lines(text)
    name = dir_name.split("_", 1)[1].replace("_", " ") if "_" in dir_name else dir_name
    link = link or dir_name
    row = f"| {f.taken} | [{f.code}]({link}/) | [{name}]({link}/) | {credit} |"
    if row in lines:
        return text
    try:
        heading = next(i for i, l in enumerate(lines) if l.strip() == TIMELINE_HEADING)
        header = next(i for i in range(heading, len(lines)) if lines[i].startswith("|"))
        _, end = _table(lines, header)
        lines.insert(end, row)
    except StopIteration:
        block = ["", TIMELINE_HEADING, "", TIMELINE_HEADER, "|---|---|---|---|", row, ""]
        at = next((i for i, l in enumerate(lines) if l.startswith("## 文件命名")), None)
        if at is None:
            while lines and lines[-1] == "":
                lines.pop()
            lines += block
        else:
            lines[at:at] = block
    return eol.join(lines)


def update_contributors(text: str, f: Form, credit: str, n: int, groups: list | None = None) -> str:
    """`groups`: optional [(count, code, session, type)] for uploads spanning several courses (at most 3 are spelled out)."""
    if f.credit == CREDIT_ANON:
        return text
    lines, eol = _split_lines(text)
    listed = {_plain(c[0]) for c in (_cells(l) for l in lines if l.startswith("|")) if c and c[0].strip("-: ")}
    if _plain(credit) in listed:
        return text
    if f.intake.isdigit():
        heading, intake_cell = f"## {f.intake} 入学", f"{f.intake} 入学（{int(f.intake) + 4} 届）"
    else:
        heading, intake_cell = "## 其他入学年份", "待补充 / tbc"
    if groups and len(groups) > 1:
        shown = groups[:3]
        zh = "、".join(f"{c} 份 {code} {ses} {typ}" for c, code, ses, typ in shown)
        en = ", ".join(f"{c} {typ} files for {code} {ses}" for c, code, ses, typ in shown)
        if len(groups) > 3:
            zh += f"等共 {n} 份"
            en += f", {n} files in all"
        what = f"{zh} / {en}"
    else:
        what = f"{n} 份 {f.code} {f.session} {f.type} / {n} {f.type} files for {f.code} {f.session}"
    row = f"| {credit} | {intake_cell}| {what} | |"
    start = next((i for i, l in enumerate(lines) if l.startswith(heading)),
                 next((i for i, l in enumerate(lines) if l.startswith("## 其他入学年份")), None))
    if start is None:
        return text
    at = next((i for i in range(start, len(lines)) if lines[i].startswith(PLACEHOLDER)), None)
    if at is None:  # section without a placeholder row: append to its table
        header = next(i for i in range(start, len(lines)) if lines[i].startswith("|"))
        at = _table(lines, header)[1]
    lines.insert(at, row)
    return eol.join(lines)


@dataclass
class Placement:
    course_dir: str
    created_readme: bool
    placed: list[tuple[int, str]] # (k, repo path) of every committed item
    notes: list[str]
    meta: dict[str, str]
    registered: list[str] = field(default_factory=list)
    courses: list[str] = field(default_factory=list)              # repo-relative course folders touched
    inbox: list[str] = field(default_factory=list)                # repo paths under _待整理/ (inbox outcome)
    parked: list[tuple[int, str]] = field(default_factory=list)   # (k, reason): committed but could not be filed


WAITING = "_待整理"


def wait_path(issue: int, k: int, ext: str) -> str:
    """Repo path of an item held in the waiting folder: _待整理/issue-<n>/item-<k>.<ext>."""
    return f"{WAITING}/issue-{int(issue)}/item-{int(k)}.{ext}"


def _edit(path: Path, fn) -> bool:
    text = path.read_bytes().decode("utf-8")
    new = fn(text)
    if new != text:
        path.write_bytes(new.encode("utf-8"))
    return new != text


def apply_decisions(root: Path, form: Form, decisions: list, items: dict, login: str, issue: int,
                    files: dict | None = None, checks: dict | None = None) -> Placement:
    """File every decided item in its own course, register each (course, session) group, regenerate the indexes.
    commit -> <course>/<session>/<type>/<file_name>; inbox -> _待整理/issue-<n>/item-<k>.<ext>; the rest is not copied.
    A committed item that cannot be filed (bad fields, new course without a name) is parked in the same waiting folder
    and listed in Placement.parked (not in Placement.inbox). Repo names come only from decide.file_name and item-<k>;
    an original file name never reaches the tree."""
    from dataclasses import replace
    from decide import file_name
    files, checks = files or {}, checks or {}
    credit = credit_name(form, login)
    notes: list[str] = []
    meta: dict[str, str] = {}
    placed: list[tuple[int, str]] = []
    inbox: list[str] = []
    parked: list[tuple[int, str]] = []
    registered: list[str] = []
    courses: dict[str, Path] = {}          # code -> course folder
    created_any = False
    groups: dict[tuple[str, str], list] = {}

    def to_wait(k: int) -> str:
        dest = root / wait_path(issue, k, items[k].ext)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(files.get(k) or items[k].path, dest)
        rel = dest.relative_to(root).as_posix()
        meta[rel] = clear_metadata(dest)
        return rel

    for d in sorted(decisions, key=lambda d: d.k):
        if d.k not in items:
            continue
        if d.outcome == "inbox":
            inbox.append(to_wait(d.k))
            continue
        if d.outcome != "commit":
            continue
        item = items[d.k]
        try:
            file_name(d.code, d.session, d.type, d.label, d.set, item.ext, set())   # validate before any path is built
        except ValueError as e:
            to_wait(d.k)
            parked.append((d.k, f"课程、学期或类型不可用（{e}），放进待整理 "
                                f"| course, session or type unusable ({e}); moved to the waiting folder"))
            continue
        course = courses.get(d.code) or find_course_dir(root, d.code)
        if course is None:
            if not d.name.strip() or course_dir_name(d.code, d.name) == f"{d.code}_":
                to_wait(d.k)
                parked.append((d.k, f"{d.code} 还没有课程目录，也没有课程名，放进待整理 "
                                    f"| {d.code} has no course folder yet and no course name; moved to the waiting folder"))
                continue
            course = root / (SHARED if is_shared(d.code) else form.prog) / course_dir_name(d.code, d.name)
            course.mkdir(parents=True, exist_ok=True)
        if d.code not in courses:
            courses[d.code] = course
            cprog = course.parent.name
            if not (course / "README.md").exists():
                (course / "README.md").write_bytes(new_course_readme(d.code, d.name or course.name.split("_", 1)[-1].replace("_", " ")).encode("utf-8"))
                created_any = True
                notes.append(f"新建了课程目录和 README：{cprog}/{course.name}/ "
                             f"| created the course folder and README: {cprog}/{course.name}/")
            if cprog != form.prog and cprog != SHARED:
                notes.append(f"{d.code} 的课程目录已在 {cprog}/ 下，文件归入那里而不是 {form.prog}/ "
                             f"| the {d.code} folder already lives under {cprog}/, so the files went there rather than {form.prog}/")
            if cprog == SHARED:
                notes.append(f"{d.code} 是全校公共课（G 选修或 MPU），放在 {SHARED}/ 下，修课记录写进 {form.prog} 的时间表 "
                             f"| {d.code} is a university-wide course (G elective or MPU): filed under {SHARED}/, timeline row in {form.prog}")
        target = course / d.session / d.type
        target.mkdir(parents=True, exist_ok=True)
        taken = {p.name for p in target.iterdir()}
        dest = target / file_name(d.code, d.session, d.type, d.label, d.set, item.ext, taken)
        shutil.copyfile(files.get(d.k) or item.path, dest)
        rel = dest.relative_to(root).as_posix()
        meta[rel] = clear_metadata(dest)
        placed.append((d.k, rel))
        groups.setdefault((d.code, d.session), []).append(d)

    touched: list[str] = []
    progs: list[str] = []
    first_fm: Form | None = None
    for (code, session), ds in groups.items():
        course = courses[code]
        own = code == form.code
        cks = [checks[d.k] for d in ds if d.k in checks]
        lecturers = list(dict.fromkeys(l for c in cks for l in c.lecturers if l))
        weighting = next((c.weighting for c in cks if c.weighting), "")
        name = next((d.name for d in ds if d.name), "") or (form.name if own else "")
        fm = replace(form, code=code, name=name, session=session, type=ds[0].type,
                     lecturer=", ".join(lecturers) if lecturers else (form.lecturer if own else ""),
                     weighting=weighting or (form.weighting if own else ""),
                     notes=form.notes if own else "")
        first_fm = first_fm or fm
        cprog = course.parent.name
        rel_course = f"{cprog}/{course.name}"
        if rel_course not in touched:
            touched.append(rel_course)
        if cprog not in progs:
            progs.append(cprog)
        changed = _edit(course / "README.md", lambda t: update_course_readme(t, fm, credit))
        registered.append(f"`{rel_course}` 学期表 {session}：" + ("已更新 | session table: updated" if changed
                                                              else "无需改动 | nothing to change"))
        when = form.taken if own else session
        if when and when != "unknown":
            tprog, link = (form.prog, f"../{SHARED}/{course.name}") if cprog == SHARED else (cprog, course.name)
            tm = replace(fm, taken=when)
            if _edit(root / tprog / "README.md", lambda t: update_timeline(t, tm, credit, course.name, link)):
                registered.append(f"`{tprog}/README.md` 修课时间表：加了 {code} | timeline: {code} row added")
            else:
                registered.append(f"`{tprog}/README.md` 修课时间表：{code} 已有相同行 | timeline: identical {code} row already there")
    summary = [(len(ds), code, session, ds[0].type) for (code, session), ds in groups.items()]
    if placed and first_fm is not None:
        if _edit(root / "CONTRIBUTORS.md", lambda t: update_contributors(t, first_fm, credit, len(placed), summary)):
            registered.append("`CONTRIBUTORS.md`：加了一行 | row added")
        else:
            registered.append("`CONTRIBUTORS.md`：匿名或署名已在表中，未改 | anonymous or already listed, unchanged")
    for prog in progs:
        subprocess.run([sys.executable, str(root / "scripts" / "update_index.py"), prog],
                       check=True, cwd=root, capture_output=True, text=True)
        registered.append(f"`{prog}/README.md` 课程索引已重跑 | course index regenerated")
    return Placement(touched[0] if touched else "", created_any, placed, notes, meta, registered,
                     touched, inbox, parked)


# ---------------------------------------------------------------- orchestration: download → PR body → git/gh → CLI

REPO = os.environ.get("GITHUB_REPOSITORY", "xmum-sair/xmum-sair.github.io")
BOT_AUTHOR = "github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>"
HERE = Path(__file__).resolve().parent
LABEL_UPLOAD, LABEL_NEEDS_INFO, LABEL_PR = "upload", "needs-info", "pr-opened"
LABELS = ((LABEL_NEEDS_INFO, "FBCA04", "需要补充信息 | Needs more information"),
          (LABEL_PR, "1D76DB", "机器人已开 PR | Bot opened a PR"))


class NothingAccepted(Exception):
    """Every attachment was rejected; the reasons are in .rejected."""

    def __init__(self, rejected: list[tuple[str, str]]):
        super().__init__("没有一份文件能收录 | none of the files could be accepted")
        self.rejected = rejected


def download(links: list[tuple[str, str]], work: Path) -> tuple[list[tuple[str, Path]], list[tuple[str, str]]]:
    """Fetch each attachment (redirects followed, one retry). Returns (downloaded (name, path), failed (name, reason))."""
    import urllib.request
    folder = work / "downloads"
    folder.mkdir(parents=True, exist_ok=True)
    ok: list[tuple[str, Path]] = []
    bad: list[tuple[str, str]] = []
    for i, (name, url) in enumerate(links):
        base = _basename(name) or url.rsplit("/", 1)[-1]
        dest = folder / f"{i}_{clean_label(base) or 'file'}"
        err: Exception | None = None
        for _attempt in range(2):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "xmum-survival-guide-intake"})
                with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
                    shutil.copyfileobj(resp, out)
                ok.append((name, dest))
                err = None
                break
            except Exception as e:  # network or HTTP error: retry once, then report
                err = e
        if err is not None:
            bad.append((name, f"下载失败 | download failed: {type(err).__name__}: {err}"))
    return ok, bad


def human_size(n: int) -> str:
    return f"{n / 2**20:.1f} MB" if n >= 2**20 else f"{max(1, n // 1024)} KB"


def rejected_lines(rejected: list[tuple[str, str]], limit: int = 30) -> str:
    lines = [f"- {_code(n)}：{r}" for n, r in rejected[:limit]]
    if len(rejected) > limit:
        lines.append(f"- … 还有 {len(rejected) - limit} 个未列出 | and {len(rejected) - limit} more not listed")
    return "\n".join(lines) or "- 无 | none"


def problems_comment(problems: list[str]) -> str:
    items = "\n".join(f"- {p}" for p in problems)
    return ("表单还差一点，机器人暂时没法整理 | A few things to fix before the bot can file this:\n\n"
            f"{items}\n\n"
            "改好后直接编辑这个 Issue（右上角 ··· → Edit），机器人会自动重跑。"
            "| Edit this issue (··· → Edit at the top right) and the bot will run again by itself.")


# ---------------------------------------------------------------- the report: the PR body, or one issue comment when nothing can be committed

STATUS_CONTEXT = "intake/待整理"           # the failing commit status that holds a PR back (with draft)
COMMENT_MARKER = "<!-- intake-status -->"   # the issue comment that stands in for the PR, edited in place
FLAG_TEXT = {
    "ai_unsure": "AI 没把握，按表单提示放的 | AI unsure, placed by the form's hint",
    "hint_mismatch": "和表单提示不一致，按文件内容放的 | differs from the form's hint, placed by the file",
    "answers_unsure": "可能含答案，请看一眼 | may contain answers, take a look",
    "marking_unsure": "可能是评分标准，请看一眼 | may be a marking scheme, take a look",
    "placed_by_maintainer": "维护者用 /place 放的 | placed with /place",
    "redacted": "已打码，请看一眼打码后的文件 | redacted: glance at the redacted copy",
    "new_set": "和仓库里的一份很像但题目不同，按另一套收 | like a file in the archive with other questions: filed as another set",
    "new_version": "像仓库里一份文件的新版本，加了版本号 | looks like a newer version of a file in the archive: filed with a version number",
    "dedupe_skipped": "扫描件，没有查近似重复 | scanned: not checked for near-duplicates",
    "possible_duplicate": "可能重复，未比较 | possible duplicate, not compared",
}
FLAGS_NOT_LISTED = ("placed_by_maintainer", "dedupe_skipped")   # shown in the table only, not under "worth a look"
BLACKOUT_TEXT = {     # the redaction record; "failed" and "no_copy" say what /approve commits, as decide's reasons do
    "ok": "已打码 | blacked out",
    "failed": "打码没完成，`/approve` 会提交已打码的部分副本 | redaction incomplete: `/approve` commits the partly redacted copy",
    "no_copy": "没有打码副本，`/approve` 会提交原文件 | no redacted copy: `/approve` commits the original",
    "verify_unsure": "已打码 | blacked out",
}
VERIFIED_TEXT = {
    "passed": "复查通过 | re-check passed",
    "failed": "复查没通过 | re-check did not pass",
    "not_needed": "只涂了文字、页上没有图片：已确认文字抽不到，不用复查 | text only, on pages without pictures: "
                  "confirmed gone, no re-check needed",
    "later": "没复查成（额度用完或调用失败），之后的重建会再试 | not re-checked (budget used up or a failed call); a later "
             "rebuild tries again",
    "not_run": "没有复查 | not re-checked",
}
COMMAND_HELP = (
    "留言指令 | Commands：在这个 PR 或原 Issue 下留言，只认仓库 OWNER / MEMBER / COLLABORATOR；`#k` 是上面的文件编号 "
    "| comment here or on the issue; owners, members and collaborators only; `#k` is a file number above.",
    "",
    "- `/place #k <课程代码 | CODE> <YYYY-MM|unknown> <类型 | Type> [标签 | Label]`：只改位置和名字，不等于批准 "
    "| moves and renames; not an approval",
    "- `/skip #k`：不收 | leave it out",
    "- `/keep #k`：把不收的文件（包括近似重复）捞回来，重新检查 | take a skipped file (near-duplicates too) back and check it",
    "- `/approve #k`：我看过了，提交；风险由批准人承担，PR 里会写明 | I looked at it, commit it; on the approver's "
    "responsibility, named in the PR",
    "- `/retry`：只重跑没检查过或失败的文件；打码没完成或复查没通过的文件从头再查一遍；已有结果的不重复花钱，不提高额度 "
    "| reruns only unchecked or failed files; files whose redaction failed or whose re-check did not pass are checked "
    "again from scratch; earlier results cost nothing, no cap is raised",
    "",
    f"类型 | Types: {' / '.join(SUBS)}",
)
PAGE_RE = re.compile(r"第\s*(\d{1,4})\s*页|\bpages?\s*(\d{1,4})\b|\bp\.\s*(\d{1,4})\b", re.I)


@dataclass
class Report:
    """What one run found, for the PR body or the issue comment. Files are named by number and `Item.source` only."""
    form: Form
    issue: int
    items: list                           # items.Item, numbered 1..n
    decisions: list                       # decide.Decision, one per item
    placement: Placement
    rejected: list[tuple[str, str]]       # (attachment name or "<zip> › <n>", reason) from download and unpack
    verdicts: dict                        # k -> decide.Check from the full check
    sorts: dict                           # k -> decide.Sort | None
    why: dict                             # k -> NOT_CHECKED key, for files without a usable check
    inspections: dict                     # repo path -> inspect_file(), committed files only
    build: tuple[bool, list[str]] | None  # site build (ok, warnings); None when nothing was committed
    spend: list[str]
    run_url: str | None = None
    branch: str = ""
    redactions: dict = field(default_factory=dict)   # k -> Blackout, for the files whose redactions were applied
    tries: dict = field(default_factory=dict)        # k -> failed checks, for the files `why` calls "failed"


@dataclass
class Result:
    placement: Placement
    body: str                             # the PR body (also when nothing is committable: an older PR shows it)
    decisions: list
    rejected: list[tuple[str, str]]
    blocking: bool
    committable: int                      # files in the commit: placed + waiting folder
    held: int = 0                         # files that block the merge
    comment: str = ""                     # the issue comment that stands in for the PR when nothing is committable


def _ai_text(text: str) -> str:
    """Claude's free text for the PR or the issue comment: a code span, so it cannot close an issue (#12), ping
    anyone (@x), link or render HTML; dropped entirely when it looks like personal data (a keyword or a value)."""
    text = str(text or "").strip()
    return "" if not text or personal(text) else _code(text)


def _row_of(*cells: str) -> str:
    """A table row; a pipe inside a cell (bilingual text, a file name in a code span) is escaped so the row holds."""
    return "| " + " | ".join(str(c).replace("|", "\\|") for c in cells) + " |"


def _pages(notes: str, redactions=()) -> list[str]:
    """Page numbers only: those the check's notes name and those its redactions are on. Never what they say."""
    found = {int(n) for m in PAGE_RE.finditer(str(notes or "")) for n in m.groups() if n}
    found |= {r["page"] for r in redactions or () if isinstance(r, dict) and type(r.get("page")) is int}
    return [str(n) for n in sorted(found) if n > 0]


def _kinds(redactions) -> str:
    """What was blacked out, by kind and count ("学号 | student ID ×2"): the check's `what` is one of a closed list
    (WHAT_TEXT's keys, the schema's enum), read through the very filter the bot-state cache applies (cache._what) and
    shown with our own fixed label, never Claude's words; anything else (an old free-text label, a value, not a string)
    counts as "其他 | other". Never the redacted text."""
    from cache import _what
    counts: dict[str, int] = {}
    for x in redactions or ():
        label = WHAT_TEXT[_what(x.get("what") if isinstance(x, dict) else None) or "other"]
        counts[label] = counts.get(label, 0) + 1
    return "、".join(f"{label} ×{n}" for label, n in counts.items())


def committed_paths(pl: Placement, issue: int, ext: dict[int, str]) -> list[str]:
    """Every repo path the commit adds: filed items, then the waiting folder (inbox, then parked)."""
    return [p for _, p in pl.placed] + pl.inbox + [wait_path(issue, k, ext[k]) for k, _ in pl.parked]


def report_sections(r: Report, linked: bool) -> list[str]:
    """The sections both the PR body and the issue comment show, in the spec's order (§7)."""
    pl = r.placement
    by_k = {it.k: it for it in r.items}
    dec = {d.k: d for d in r.decisions}
    links = dict(r.form.links)
    ext = {it.k: it.ext for it in r.items}
    ordered = sorted(r.decisions, key=lambda d: d.k)

    def src(k: int) -> str:
        return _code(by_k[k].source)

    def path(p: str) -> str:
        if not linked:
            return f"`{p}`"
        return f"[`{p}`](https://github.com/{REPO}/blob/{quote(r.branch, safe='/')}/{quote(p, safe='/')})"

    def listed(lines: list[str]) -> list[str]:
        return lines or ["- 无 | none"]

    out = ["## 收录 | Committed", ""]
    if pl.placed:
        out += ["| # | 来源 / Source | 新路径 / New path | 页数 / Pages | 概述 / Summary |", "|---|---|---|---|---|"]
        for k, p in pl.placed:
            c, s, d = r.verdicts.get(k), r.sorts.get(k), dec[k]
            notes = [_ai_text(c.summary if c else "") or _ai_text(s.note if s else "")]
            b = r.redactions.get(k)
            if b is not None and b.out is not None and c is not None:     # the committed file is the redacted copy
                n = len(c.redactions)
                notes.append(f"打码 {n} 处 | {n} redaction{'' if n == 1 else 's'}")
            notes += [FLAG_TEXT[x] for x in d.flags if x in FLAG_TEXT]
            notes += [f"由 {_code(x.split(':', 1)[1])} 批准 | approved by {_code(x.split(':', 1)[1])}"
                      for x in d.flags if x.startswith("approved_by:")]
            pages = r.inspections.get(p, {}).get("pages") or "—"
            out.append(_row_of(f"#{k}", src(k), path(p), pages, " · ".join(n for n in notes if n) or "—"))
    else:
        out.append("- 无 | none")
    waiting = len(pl.inbox) + len(pl.parked)
    if waiting:
        out += ["", f"另有 {waiting} 个文件放进了 `{WAITING}/issue-{r.issue}/`，见「需要你看」"
                    f" | {waiting} more in `{WAITING}/issue-{r.issue}/`, see Needs you."]

    out += ["", "## 不收 | Not included", ""]
    lines = rejected_lines(r.rejected).splitlines() if r.rejected else []
    lines += [f"- #{d.k} {src(d.k)}：{d.reason}" for d in ordered if d.outcome == "skip"]
    out += listed(lines)

    out += ["", "## 可能误判 | Maybe wrong", ""]
    out += listed([f"- #{d.k} {src(d.k)}：{d.reason}" for d in ordered if d.outcome == "maybe_skip"])

    out += ["", "## 需要你看 | Needs you", ""]
    lines = []
    parked = dict(pl.parked)
    for d in ordered:
        if d.outcome == "review":
            extra = [why_text(r.why[d.k], r.tries.get(d.k, 0))] if d.k in r.why else []
            b = r.redactions.get(d.k)
            if b is not None and b.status != "ok":
                extra.append("可用 `/retry` 从头再查 | `/retry` checks it again from scratch")
            if d.code:
                extra.append(f"可能的位置 | likely place: `{d.code} {d.session} {d.type}`")
            source = by_k[d.k].source
            if ORIGIN_SEP not in source and links.get(source):     # straight from an attachment: link the original
                extra.append(f"原附件 | attachment: [{_code(source)}]({links[source]})")
            lines.append(f"- #{d.k} {src(d.k)}：{d.reason}" + "".join(f" · {x}" for x in extra))
        elif d.outcome == "inbox":
            lines.append(f"- #{d.k} {src(d.k)}：{d.reason} → {path(wait_path(r.issue, d.k, ext[d.k]))}"
                         f" · 用 `/place #{d.k} …` 放好位置 | file it with `/place #{d.k} …`")
        elif d.k in parked:
            lines.append(f"- #{d.k} {src(d.k)}：{parked[d.k]} → {path(wait_path(r.issue, d.k, ext[d.k]))}")
    out += listed(lines)
    placed = dict(pl.placed)
    flagged = [(d, [FLAG_TEXT[x] for x in d.flags if x in FLAG_TEXT and x not in FLAGS_NOT_LISTED])
               for d in ordered if d.k in placed]
    flagged = [(d, notes) for d, notes in flagged if notes]
    if flagged:
        out += ["", "不挡合并，但请留意（已提交）| Not blocking, but worth a look (committed):"]
        out += [f"- #{d.k} {src(d.k)} → {path(placed[d.k])}：{'；'.join(notes)}" for d, notes in flagged]

    out += ["", "## 打码记录 | Redactions", "",
            "- PDF 里的个人信息自动涂黑，提交的是涂黑后的副本；这里只写页码和类型，从不写内容；Office 文件不自动打码 "
            "| personal data in PDFs is blacked out and the blacked-out copy is what gets committed; only pages and kinds "
            "are listed here, never the content; Office files are not redacted automatically"]
    for k in sorted(r.verdicts):
        d = dec.get(k)
        if d is None or d.outcome not in ("commit", "inbox", "review"):     # committed or held files only
            continue
        c, b = r.verdicts[k], r.redactions.get(k)
        if b is None and c.personal_data not in ("yes", "unsure"):
            continue
        pages = [str(p) for p in b.pages] if b is not None else _pages(c.notes, c.redactions)
        where = f"第 {'、'.join(pages)} 页 | page {', '.join(pages)}" if pages else "页码不详 | page not given"
        kinds = [_kinds(c.redactions)] if c.redactions else []
        if b is not None:
            done = BLACKOUT_TEXT["no_copy"] if b.out is None else BLACKOUT_TEXT.get(b.status, BLACKOUT_TEXT["failed"])
            parts = [done, where, *kinds, VERIFIED_TEXT[b.verified]]
        else:
            what = "个人信息：有 | personal data: found" if c.personal_data == "yes" else "个人信息：拿不准 | personal data: unsure"
            parts = [what, where, *kinds]
            if ext[k] != "pdf" and c.personal_data == "yes":
                parts.append("Office 文件，没有打码 | an Office file, not redacted")
        out.append(f"- #{k}：" + " · ".join(parts))

    out += ["", "## 登记 | Registered", ""]
    lines = [f"- 课程目录 | Course folders: " + "、".join(f"`{c}/`" for c in pl.courses)] if pl.courses else []
    out += listed(lines + [f"- {n}" for n in pl.registered + pl.notes])

    out += ["", "## 检查 | Checks", ""]
    committed = committed_paths(pl, r.issue, ext)
    if committed:
        out += ["| 文件 / File | 大小 / Size | 元数据已清空 / Metadata cleared | 可疑文本 / Suspicious text "
                "| 答案关键词 / Answer keywords | 文字量低 / Low text |", "|---|---|---|---|---|---|"]
        for p in committed:
            ins, meta = r.inspections.get(p, {}), pl.meta.get(p, "")
            meta_txt = ("✅" if ins.get("meta_empty") else "⚠️") + ("" if meta in ("cleared", "n/a") else f" {meta}")
            pii = f"⚠️ ×{len(ins['pii'])}（内容不显示 | not shown）" if ins.get("pii") else "—"   # never quoted
            ans = ", ".join(_code(m) for m in ins.get("answers", [])) or "—"
            low = "⚠️ 像扫描件，请肉眼看 | looks scanned, eyeball it" if ins.get("low_text") else "—"
            out.append(_row_of(f"`{p.rsplit('/', 1)[-1]}`", human_size(ins.get("size", 0)), meta_txt, pii, ans, low))
        out.append("")
        out.append(f"- 缩略图（只用提交了的文件）| Thumbnails (committed files only): [本次运行的 Artifacts | this run's artifacts]({r.run_url})"
                   if r.run_url else "- 缩略图 | Thumbnails: 本地干跑，无 | dry run, none")
    if r.build is None:
        out.append("- 没有文件提交，网站没有构建 | nothing committed, the site was not built")
    else:
        ok, warnings = r.build
        out.append("- " + ("✅ `python site/build.py` 成功 | succeeded" if ok else "❌ `python site/build.py` 失败 | failed"))
        out += [f"  - `{w}`" for w in warnings[:20]]

    out += ["", "## 本次 AI 花费 | AI spend this run", "", *r.spend]

    out += ["", "## 维护者清单 | Maintainer checklist", "",
            "- [ ] 看过缩略图或文件预览 | Looked at the thumbnails or file previews",
            "- [ ] 没有答案、评分标准 | No answers or marking schemes",
            "- [ ] 没有学号、姓名、私人联系方式 | No student IDs, names or private contacts",
            "- [ ] 「需要你看」里的文件都处理了 | Every file under Needs you is dealt with",
            "", *COMMAND_HELP]
    return out


def _hints(f: Form) -> list[str]:
    given = [v for v in (f.code, f.session, f.type, f.name) if v]
    return [f"表单提示 | Form hints: " + " · ".join(_code(v) for v in given)] if given else []


def pr_body(r: Report, base_sha: str, held: int) -> str:
    import datetime
    n = len(committed_paths(r.placement, r.issue, {it.k: it.ext for it in r.items}))
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    head = [f"来自 | From #{r.issue} · 这个 PR 有 {n} 个文件 | {n} files in this PR", *_hints(r.form), ""]
    if not n:   # an older PR while nothing can be committed: its branch still holds the previous build
        head += ["> ⛔ 现在没有能提交的文件，这个分支还是上一次整理的内容，不要合并；处理完「需要你看」后机器人会重建。"
                 " | Nothing can be committed now and this branch still holds the previous build: do not merge. The bot "
                 "rebuilds it once Needs you is dealt with.", ""]
    elif held:
        head += [f"> ⛔ 还有 {held} 个文件要你处理（见「需要你看」）。处理完之前这个 PR 是草稿，状态 `{STATUS_CONTEXT}` 不通过。"
                 f" | {held} files need you (see Needs you); until then this PR stays a draft and the `{STATUS_CONTEXT}` status fails.", ""]
    return "\n".join([
        *head, *report_sections(r, linked=True),
        "", "---", "",
        f"重建于 {when}，基于 main `{base_sha[:7]}`。要改内容请编辑原 Issue 或用上面的指令，PR 会自动重建；不要直接改这个分支。"
        f" | Rebuilt {when} from main `{base_sha[:7]}`. To change anything edit the issue or use the commands above and "
        f"the PR rebuilds itself; do not push to this branch.",
        "", f"{'Refs' if held or not n else 'Closes'} #{r.issue}", ""])


def issue_comment(r: Report) -> str:
    """Stands in for the PR while no file can be committed (spec §7); the marker lets later runs edit it in place."""
    return "\n".join([
        COMMENT_MARKER,
        "这次上传还没有能提交的文件，所以暂时没开 PR。等有文件可以提交（之后的重建额度够了，或维护者用了下面的指令），"
        "机器人会开 PR；这条评论会原地更新。"
        " | Nothing from this upload can be committed yet, so there is no PR. Once a file can be committed (a later "
        "rebuild with budget left, or a maintainer's command below) the bot opens one; this comment is updated in place.",
        *_hints(r.form), "", *report_sections(r, linked=False), ""])


def _cc():
    sys.path.insert(0, str(HERE.parent / "llm"))
    import claude_check as cc
    return cc


SPEND_STEPS = ("分拣 | Sorting", "检查 | Checks", "打码复查 | Redaction re-checks", "查重比较 | Duplicate comparisons")


def spend_lines(costs: list[float], budget, asked: bool) -> list[str]:
    """`costs`: this run's dollars for each of SPEND_STEPS, in order (all from the run's Meter)."""
    if not asked:
        return ["- 这次没有调用 Claude（没有 ANTHROPIC_API_KEY）| Claude was not called this run (no ANTHROPIC_API_KEY)"]
    steps = " · ".join(f"{name}: ${usd:.4f}" for name, usd in zip(SPEND_STEPS, costs))
    lines = [f"- {steps} · 合计 | Total: ${sum(costs):.4f}（已有结果的文件不再花钱 | files with earlier results cost nothing）"]
    if budget is None:
        return lines + ["- 上传池：干跑，没有记账 | Upload pool: dry run, nothing booked"]
    day, month = budget.spent("upload")
    if math.isinf(day) or math.isinf(month):
        return lines + ["- 上传池：账本读不到 | Upload pool: the ledger could not be read"]
    cc = _cc()
    return lines + [f"- 上传池今天 / 本月已记 | Upload pool booked today / this month: ${day:.2f} / ${month:.2f}"
                    f"（上限 | caps ${cc.usd_cap('upload', 'day'):g} / ${cc.usd_cap('upload', 'month'):g}）"]


def _add_spent(cache: dict, usd: float) -> None:
    try:
        cache["spent_usd"] = round(float(cache.get("spent_usd") or 0) + float(usd or 0), 6)
    except (TypeError, ValueError):
        cache["spent_usd"] = round(float(usd or 0), 6)


def _sort(items: list, f: Form, cat: list[dict], ask, meter, cache: dict) -> dict:
    """{k: raw sort verdict or None}; without an ask or a meter only earlier (cached) verdicts are used."""
    import ai
    if ask is not None and meter is not None:
        return ai.sort_items(items, f, cat, ask, meter, cache)
    store = cache.setdefault("sort", {})
    return {it.k: store.get(it.sha) for it in items}


def _check(items: list, keep: set[int], ask, meter, cache: dict) -> dict:
    """{k: raw check answer or None} for the items numbered in `keep`; without an ask or a meter only earlier
    (cached) answers are used."""
    import ai
    if ask is not None and meter is not None:
        return ai.check_items(items, keep, ask, meter, cache)
    store = cache.setdefault("check", {})
    return {it.k: store.get(it.sha) for it in items if it.k in keep}


LECTURERS_MAX, LECTURER_CHARS, WEIGHTING_CHARS = 5, 80, 200   # Claude's text bound for a course README


def _public(text: str, limit: int) -> str:
    """Claude's text for a README cell: stripped and capped, or "" when it (or what is kept of it) looks personal (a
    keyword or a value, see personal())."""
    text = str(text or "").strip()
    cut = text[:limit].strip()
    return "" if personal(text) or personal(cut) else cut


def _check_of(raw):
    """A check answer as a decide.Check: exactly the schema's keys, each of its type (ai.check_ok), else None.
    Something to black out is personal data, whatever personal_data says. lecturers and weighting go into a course
    README (a commit): an entry that looks personal is dropped, and both are capped."""
    import ai
    from decide import Check
    if not ai.check_ok(raw):
        return None
    fields = {k: raw[k] for k in ai.CHECK_KEYS}
    fields["redactions"] = [dict(r) for r in fields["redactions"]]
    lecturers = [_public(x, LECTURER_CHARS) for x in fields["lecturers"]]
    fields["lecturers"] = [x for x in lecturers if x][:LECTURERS_MAX]
    fields["weighting"] = _public(fields["weighting"], WEIGHTING_CHARS)
    if fields["redactions"] and fields["personal_data"] == "no":
        fields["personal_data"] = "yes"
    return Check(**fields)


def _why_unchecked(it, asked: bool, cache: dict | None = None) -> str:
    """The NOT_CHECKED key for a kept file without a usable check: "failed" once its check failed ai.CHECK_TRIES times
    (it then waits for /retry), "unchecked" before that."""
    import ai
    from items import OFFICE_EXTS
    if it.view is None:
        return "no_view" if it.ext in OFFICE_EXTS else "plain_text"
    if it.pages < 1:
        return "unreadable"
    if it.pages > ai.CHECK_MAX_PAGES:
        return "too_long"
    if ai.failed_checks(cache or {}, it.sha) >= ai.CHECK_TRIES:
        return "failed"
    return "unchecked" if asked else "off"


def _kept(sort, override: dict | None) -> bool:
    """Will decide() keep this file? Only those are checked (spec §5 step 2): a sort that says keep, or /keep or /place."""
    o = override or {}
    return bool(o.get("keep") or o.get("place") or (sort is not None and sort.keep))


# ---------------------------------------------------------------- redaction, its re-check, near-duplicates (§5 steps 3–5)

VERIFY_BATCH = 8                 # page images per re-check call
VERIFY_PNG_MAX = 4_500_000       # bytes per page image; a bigger one is rendered again at a lower resolution
VERIFY_CALL_MAX = 20_000_000     # bytes of page images per call (base64 adds a third; one request may be 32 MB)
VERIFY_DPI, VERIFY_MIN_DPI = 150, 30


@dataclass
class Blackout:
    """What blacking out one PDF's redactions gave. `status` is decide()'s `redaction`: "ok", "failed" or
    "verify_unsure". `out` is the redacted copy, kept when the status is not "ok" too (an /approve commits it, spec §7);
    None when no copy could be made. `pages`: the pages the check named, plus every page blacked out in the copy (where
    redact.apply found a repeat), so the record shows each changed page. `verified`: "passed" (the re-check said nothing
    personal is left), "failed" (it answered something else), "later" (it could not run: budget, a failed call),
    "not_needed" (text only, on pages without pictures: the text check is enough) or "not_run". `look`: the pages the
    re-check has to see (every box page and every redacted page with a picture)."""
    status: str
    out: Path | None
    pages: list[int]
    verified: str = "not_run"
    look: list[int] = field(default_factory=list)


def _store(cache: dict, key: str) -> dict:
    """cache[key] as a dict to write into (a broken one is replaced)."""
    if not isinstance(cache.get(key), dict):
        cache[key] = {}
    return cache[key]


def _found(cache: dict, key: str) -> dict:
    """cache[key] to read from: {} when missing or broken (nothing is added to the cache)."""
    return cache[key] if isinstance(cache.get(key), dict) else {}


def _picture_pages(pdf: Path, pages) -> set[int]:
    """Those of `pages` (from 1) that show a picture of any kind, inline images included; all of them when the PDF
    cannot be read (then every one is re-checked)."""
    import fitz
    try:
        with fitz.open(pdf) as doc:
            return {p for p in pages if 1 <= p <= doc.page_count and doc[p - 1].get_image_info()}
    except Exception:
        return set(pages)


def _redact_one(it, c, raw, cache: dict, out_dir: Path) -> Blackout:
    """Black out check `c`'s redactions in a copy of the PDF Claude read (`it.view`) at out_dir/item-<k>.pdf.
    A fresh answer (its text still in memory; ai.check_items keeps only the schema's keys of it) is applied as given,
    and the rectangles that gave are written into the raw cached answer `raw` (`redactions_rects`, plus
    `redactions_ok`): the bot-state cache keeps them, as they hold no text. A rebuild, whose cached answer lost the text,
    applies exactly those rectangles and keeps the first run's verdict. Fails closed ("failed", no copy) on a redaction
    page outside 1..it.pages (checked here too, as redact.apply does), with neither text nor rectangles, with no
    rectangle at all, or when redact.apply writes nothing; the check stays cached with `redactions_ok` False, so ordinary
    rebuilds pay nothing more and `/retry` checks it again (store_commands). Single-threaded: redact is not thread-safe."""
    import redact
    raw = raw if isinstance(raw, dict) else {}
    pages = sorted({x["page"] for x in c.redactions if isinstance(x, dict) and type(x.get("page")) is int})
    if not all(isinstance(x, dict) and type(x.get("page")) is int and 1 <= x["page"] <= it.pages for x in c.redactions):
        raw["redactions_ok"] = False
        return Blackout("failed", None, pages)
    rects = raw.get("redactions_rects")
    texts = all(isinstance(x.get("text"), str) for x in c.redactions if x.get("kind") == "text")
    out = Path(out_dir) / f"item-{it.k}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    r, ok = None, False
    if isinstance(rects, list):                     # a rebuild: what the first run applied, nothing else
        if rects:
            r = redact.apply(it.view, out, [], cached_rects=rects)
            ok = r.ok and raw.get("redactions_ok") is True
    elif texts:                                     # the check's own answer, this run
        _found(cache, "verify").pop(it.sha, None)   # a verdict belongs to the rectangles it saw
        r = redact.apply(it.view, out, c.redactions)
        ok = r.ok
        if r.out is not None and r.rects:
            raw["redactions_rects"], raw["redactions_ok"] = r.rects, r.ok
    if r is None or r.out is None or not r.rects:   # nothing blacked out: there is no redacted copy
        raw["redactions_ok"] = False
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
        return Blackout("failed", None, pages)
    boxes = {x["page"] for x in c.redactions if x.get("kind") == "box"}
    done = sorted({x["page"] for x in r.rects})
    look = sorted(set(done) & boxes | _picture_pages(r.out, done))
    return Blackout("ok" if ok else "failed", r.out, sorted(set(pages) | set(done)),
                    "not_needed" if ok and not look else "not_run", look)


def _page_png(pdf: Path, page: int) -> bytes | None:
    """One page as a PNG of at most VERIFY_PNG_MAX bytes: at VERIFY_DPI, or rendered again lower until it fits; None
    when it does not fit even at VERIFY_MIN_DPI."""
    import redact
    dpi = VERIFY_DPI
    while True:
        png = redact.render(pdf, [page], dpi)[0]
        if len(png) <= VERIFY_PNG_MAX:
            return png
        if dpi <= VERIFY_MIN_DPI:
            return None
        dpi = max(VERIFY_MIN_DPI, min(dpi - 10, int(dpi * math.sqrt(VERIFY_PNG_MAX / len(png)) * 0.9)))


def _verify(pdf: Path, pages: list[int], ask, meter) -> str:
    """The re-check of a redacted copy (spec §5 step 4): "no" only when every call answers "no". The pages go in calls of
    at most VERIFY_BATCH images and VERIFY_CALL_MAX bytes, each image at most VERIFY_PNG_MAX bytes (see _page_png);
    the first call that is not "no" ends it (the rest is not sent). "unsure" when Claude answered anything else, or a
    page cannot be rendered (small enough): a verdict, kept until /retry. "not_run" when it could not be asked at all:
    no pages, `ask` or `meter`, a refused reservation, a failed call or no usable answer (a later rebuild tries again).
    Every call reserves on `meter` (ai.verify_pages)."""
    import ai
    if not pages or ask is None or meter is None:
        return "not_run"
    answers = []

    def asked(content, on_cost=None):
        answers.append(ask(content, on_cost=on_cost))
        return answers[-1]

    def call(batch) -> str:
        n = len(answers)
        if ai.verify_pages(batch, asked, meter) == "no":
            return "no"
        got = answers[n:]
        answered = bool(got) and isinstance(got[-1], dict) and got[-1].get("readable") in ai.CHECK_ENUM
        return "unsure" if answered else "not_run"
    batch, size = [], 0
    try:
        for p in pages:
            png = _page_png(pdf, p)
            if png is None:
                return "unsure"
            if batch and (len(batch) >= VERIFY_BATCH or size + len(png) > VERIFY_CALL_MAX):
                v = call(batch)
                if v != "no":
                    return v
                batch, size = [], 0
            batch.append(png)
            size += len(png)
    except Exception as e:                          # never print e: it may quote the file
        print(f"verify: a page could not be rendered ({type(e).__name__})", file=sys.stderr)
        return "unsure"
    return call(batch)


def _near_duplicate(it, d, repo: Path, ask, meter, seen: dict) -> tuple[str | None, dict | None]:
    """(repo path it duplicates, None), or (None, variant), for a file about to be committed as decision `d` (spec §5
    step 5). It is compared with the PDFs in d's course folder (none when the course has no folder yet); the best match
    decides: SAME_AT or more is the same file; from ASK_AT the first two pages of both go to the comparison (`ask`, on
    `meter`), whose "same" is a duplicate, "different_set" the next free set letter, "different_version" the next
    version number, "different" nothing. When there is no answer (no `ask` or `meter`, a refused reservation, a failed
    call) the variant is {"possible_duplicate": True}: committed, flagged for a look, never silently "different".
    `seen` holds the repo files' first pages for the whole run (dedupe.first_pages)."""
    import ai
    import dedupe
    from decide import stem_label
    course = find_course_dir(repo, d.code)
    found = dedupe.candidates(it, course, repo, cache=seen)
    if not found:
        return None, None
    path, score = found[0]
    if score >= dedupe.SAME_AT:
        return path, None
    if ask is None or meter is None:
        return None, {"possible_duplicate": True}
    theirs = dedupe.first_pages(repo / path, seen) or []      # the run's cache: read when it was scored
    rel = ai.compare_pair("\n".join(it.page_text[:dedupe.PAGES]), "\n".join(theirs), ask, meter)
    label = stem_label(d.type, d.label)
    if rel is None:
        return None, {"possible_duplicate": True}
    if rel == "same":
        return path, None
    if rel == "different_set":
        return None, {"set": dedupe.next_set(course, d.code, d.session, label)}
    if rel == "different_version":
        return None, {"version": dedupe.next_version(course, d.code, d.session, label)}
    return None, None


def _guarded(d, c, b):
    """The anti-goal, once more: a file the check found personal data in (or something to black out) is committed only
    as a redacted copy that was confirmed (re-checked, or text only), or when a maintainer approved it. The reason says
    what an /approve would commit (decide.REDACTION_PROBLEMS)."""
    from dataclasses import replace
    from decide import REDACTION_PROBLEMS
    if d.outcome not in ("commit", "inbox") or c is None or (c.personal_data == "no" and not c.redactions):
        return d
    if any(str(x).startswith("approved_by:") for x in d.flags):
        return d
    if b is not None and b.status == "ok" and b.out is not None and b.verified in ("passed", "not_needed"):
        return d
    key = "no_copy" if b is None or b.out is None else "failed" if b.status == "failed" else "verify_unsure"
    zh, _, en = REDACTION_PROBLEMS[key].partition(" | ")
    return replace(d, outcome="review", reason=f"需要你看：{zh} | needs a look: {en}", blocking=True)


def process(f: Form, issue: int, login: str, repo: Path, work: Path, files: list[Path] | None = None,
            base_sha: str = "dry-run", run_url: str | None = None, branch: str = "",
            asks: dict | None = None, meter=None, cache: dict | None = None, budget=None) -> Result:
    """Download (unless files are given), unpack, number the files, drop exact duplicates, sort (`asks["sort"]`), check
    every page of the files that will be kept (`asks["check"]`), black out what the check names in a copy of each PDF,
    re-check the redacted pages that need it (`asks["verify"]`), look for near-duplicates in the course folder
    (`asks["compare"]` for the doubtful ones), decide each file, place them (redacted copies, never their originals),
    inspect and thumbnail what is committed, build the site, write the report. Every call is within `meter`. `cache` is
    the upload's bot-state cache: read and updated in place (items, sort, check with the rectangles applied, check_skipped,
    check_failed, verify); overrides are only read; the caller adds the meter's spend. `budget` gives the pool totals."""
    import ai
    from decide import decide, validate_sort
    from items import exact_duplicates, make_items
    cache = {} if cache is None else cache
    asks = asks or {}
    work.mkdir(parents=True, exist_ok=True)
    if files is None:
        downloads, rejected = download(f.links, work)
    else:
        downloads, rejected = [(p.name, p) for p in files], []
    accepted, bad, origins = unpack(downloads, work)
    rejected += bad
    if not accepted:
        raise NothingAccepted(rejected)
    # From here on a file is item-<k>.<ext>: the name an uploader gave a file inside a zip (often their own name or
    # student ID) never reaches a log line, Claude, the cache, the report or the tree.
    named_dir = work / "items"
    shutil.rmtree(named_dir, ignore_errors=True)
    named_dir.mkdir(parents=True)
    named = []
    for k, (_, path) in enumerate(accepted, 1):
        dest = named_dir / f"item-{k}.{_split_ext(path.name)[1]}"
        try:
            shutil.copyfile(path, dest)
        except OSError as e:               # the message would quote the source path, i.e. the member's own name
            raise RuntimeError(f"could not copy file #{k} ({type(e).__name__})") from None
        named.append((dest.name, dest))
    shutil.rmtree(work / "unzipped", ignore_errors=True)
    items = make_items(named, origins)
    cache["items"] = [{"k": it.k, "sha": it.sha, "source": it.source} for it in items]
    stored = cache.get("overrides") if isinstance(cache.get("overrides"), dict) else {}
    overrides = {sha: o for sha, o in stored.items() if isinstance(o, dict) and not str(sha).startswith("_")}   # files only
    dups = exact_duplicates(items, repo)
    live = [it for it in items if it.k not in dups and not (overrides.get(it.sha) or {}).get("skip")]

    def spent() -> float:
        return meter.spent if meter is not None else 0.0

    cat = ai.catalogue(repo)
    raw = _sort(live, f, cat, asks.get("sort"), meter, cache)
    cat_codes = {c["code"] for c in cat}
    sorts = {it.k: validate_sort(raw.get(it.k), cat_codes) for it in live}
    keep = {it.k for it in live if _kept(sorts.get(it.k), overrides.get(it.sha))}
    sort_usd = spent()
    answers = _check(live, keep, asks.get("check"), meter, cache)
    check_usd = spent() - sort_usd
    checks, why = {}, {}
    for it in live:
        if it.k in keep:
            c = _check_of(answers.get(it.k))
            if c is not None:
                checks[it.k] = c
            else:                              # no answer is cached: a later rebuild asks again, up to ai.CHECK_TRIES
                why[it.k] = _why_unchecked(it, asked=asks.get("check") is not None and meter is not None, cache=cache)
        elif raw.get(it.k) is None:
            why[it.k] = "unsorted"

    # Per kept file: check → redact → re-check → near-duplicates → decide (spec §5 steps 3–6). Office files are never
    # redacted: with personal data they wait under Needs you (decide). Everything here runs on this thread.
    red: dict[int, Blackout] = {}
    out_dir = work / "redacted"
    shutil.rmtree(out_dir, ignore_errors=True)
    for it in live:
        c = checks.get(it.k)
        if c is not None and c.redactions and it.ext == "pdf" and it.view is not None:
            red[it.k] = _redact_one(it, c, answers.get(it.k), cache, out_dir)

    def decided(it, dup=None, variant=None, redaction=None):
        b = red.get(it.k)
        if redaction is None and b is not None:          # "no_copy": an /approve would commit the original
            redaction = "no_copy" if b.out is None else b.status
        return decide(it, sorts.get(it.k), checks.get(it.k), f, overrides.get(it.sha), dups.get(it.k) or dup, variant,
                      redaction)

    before = spent()
    for it in live:
        b = red.get(it.k)
        if b is None or b.status != "ok" or not b.look:
            continue
        if decided(it, redaction="ok").outcome not in ("commit", "inbox"):
            continue                       # it is not going in even with a good redaction: no money on a re-check
        verdict = _found(cache, "verify").get(it.sha)     # a rebuild with the same rectangles: no new call
        if verdict not in ("no", "unsure"):
            verdict = _verify(b.out, b.look, asks.get("verify"), meter)
            if verdict in ("no", "unsure"):                 # Claude's answer is kept (until /retry); "not_run" is not
                _store(cache, "verify")[it.sha] = verdict
        if verdict == "no":
            b.verified = "passed"
        else:
            b.status, b.verified = "verify_unsure", "failed" if verdict == "unsure" else "later"
    verify_usd = spent() - before

    before = spent()
    near: dict[int, str] = {}
    variants: dict[int, dict] = {}
    seen: dict = {}                        # the repo's first pages, read once per run
    for it in live:
        d = decided(it)
        if d.outcome != "commit" or it.scanned or (overrides.get(it.sha) or {}).get("keep"):
            continue                       # /keep: a maintainer took it back, near-duplicate or not
        dup, variant = _near_duplicate(it, d, repo, asks.get("compare"), meter, seen)
        if dup:
            near[it.k] = dup
        if variant:
            variants[it.k] = variant
    compare_usd = spent() - before

    decisions = [_guarded(decided(it, near.get(it.k), variants.get(it.k)), checks.get(it.k), red.get(it.k)) for it in items]
    copies = {k: b.out for k, b in red.items() if b.out is not None}   # never the original of a file that was redacted
    pl = apply_decisions(repo, f, decisions, {it.k: it for it in items}, login, issue, files=copies, checks=checks)
    committed = committed_paths(pl, issue, {it.k: it.ext for it in items})
    held = sum(1 for d in decisions if d.blocking) + len(pl.parked)
    inspections = {p: inspect_file(repo / p) for p in committed}
    sheet = work.parent / "thumbs" / f"issue-{issue}.png"
    sheet.unlink(missing_ok=True)
    thumbnails([repo / p for p in committed if p.lower().endswith(".pdf")], sheet)
    build = None
    if committed:
        r = subprocess.run([sys.executable, str(HERE.parent / "site" / "build.py"), "--root", str(repo), "--out", str(work / "site")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        build = (r.returncode == 0, list(dict.fromkeys(l.strip() for l in (r.stderr + r.stdout).splitlines()
                                                       if "WARNING" in l or "ERROR" in l)))
    spend = spend_lines([sort_usd, check_usd, verify_usd, compare_usd], budget,
                        asked=any(asks.get(x) for x in ("sort", "check", "verify", "compare")))
    tries = {it.k: ai.failed_checks(cache, it.sha) for it in live if why.get(it.k) == "failed"}
    report = Report(f, issue, items, decisions, pl, rejected, checks, sorts, why, inspections, build, spend,
                    run_url, branch or f"upload/issue-{issue}", red, tries)
    return Result(pl, pr_body(report, base_sha, held), decisions, rejected, held > 0, len(committed), held,
                  "" if committed else issue_comment(report))


def pr_title(n: int, res: Result) -> str:
    if not res.committable:
        return f"[上传 | Upload] #{n}：暂时没有能提交的文件 | nothing to commit yet"
    codes = [c.rsplit("/", 1)[-1].split("_", 1)[0] for c in res.placement.courses]
    return f"[上传 | Upload] #{n}：{res.committable} 个文件 | files（{'、'.join(codes) or WAITING}）"


def commit_message(n: int, res: Result) -> str:
    """`content: add files from #<n>`, then one line per course (and one for the waiting folder)."""
    counts: dict[str, int] = {c: 0 for c in res.placement.courses}
    for _, p in res.placement.placed:
        course = "/".join(p.split("/")[:2])
        counts[course] = counts.get(course, 0) + 1
    waiting = len(res.placement.inbox) + len(res.placement.parked)
    if waiting:
        counts[f"{WAITING}/issue-{n}"] = waiting
    return f"content: add files from #{n}\n\n" + "\n".join(f"- {c}: {k} file{'' if k == 1 else 's'}" for c, k in counts.items())


def status_text(res: Result) -> str:
    if not res.committable:
        return "现在没有能提交的文件，这个分支是上一次的内容 | nothing can be committed now; this branch is the previous build"
    if res.blocking:
        return f"还有 {res.held} 个文件要维护者处理 | {res.held} file(s) still need a maintainer"
    return "没有挡合并的文件 | nothing blocks the merge"


def _run(cmd: list[str], **kw) -> str:
    """Run with output captured; a failure raises with stderr included, so the Actions log says why."""
    r = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8", **kw)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-2000:]}")
    return r.stdout.strip()


class GH:
    """The few `gh` calls the bot needs. Arguments are lists: untrusted text never meets a shell."""

    BOT_LOGIN = "github-actions[bot]"

    def __init__(self, repo: str = REPO):
        self.repo = repo

    def _run(self, *args: str, input: str | None = None) -> str:
        return _run(["gh", *args], input=input)

    def _api(self, method: str, path: str, body: dict) -> str:
        return self._run("api", "-X", method, f"repos/{self.repo}/{path}", "--input", "-", input=json.dumps(body))

    def issue(self, n: int) -> dict:
        d = json.loads(self._run("api", f"repos/{self.repo}/issues/{n}"))
        return {"body": d.get("body") or "", "title": d.get("title") or "", "state": d["state"],
                "login": d["user"]["login"], "id": d["user"]["id"], "labels": [l["name"] for l in d.get("labels", [])]}

    def comment(self, n: int, text: str) -> None:
        """A comment on issue or pull request n (the issues API serves both)."""
        self._api("POST", f"issues/{int(n)}/comments", {"body": text})

    def comment_pr(self, n: int, text: str) -> None:
        self._run("pr", "comment", str(n), "--repo", self.repo, "--body-file", "-", input=text)

    def upsert_comment(self, issue: int, marker: str, text: str) -> None:
        """Edit the bot's comment that carries `marker`, or post it: one comment per issue, updated in place."""
        text = text if marker in text else f"{marker}\n{text}"
        ids = self._run("api", "--paginate", f"repos/{self.repo}/issues/{int(issue)}/comments", "-q",
                        f'.[] | select(.user.login == "{self.BOT_LOGIN}" and (.body | contains({json.dumps(marker)}))) | .id').split()
        if ids:
            self._api("PATCH", f"issues/comments/{int(ids[0])}", {"body": text})
        else:
            self.comment(issue, text)

    def react(self, comment_id: int, content: str) -> None:
        self._api("POST", f"issues/comments/{int(comment_id)}/reactions", {"content": content})

    def label(self, n: int, add: tuple[str, ...] = (), remove: tuple[str, ...] = ()) -> None:
        args = [x for l in add for x in ("--add-label", l)] + [x for l in remove for x in ("--remove-label", l)]
        if args:
            self._run("issue", "edit", str(n), "--repo", self.repo, *args)

    def ensure_labels(self) -> None:
        for name, color, desc in LABELS:
            self._run("label", "create", name, "--repo", self.repo, "--color", color, "--description", desc, "--force")

    def open_upload_prs(self) -> list[int]:
        """Issues of this repository's open `upload/issue-<n>` PRs. A fork's PR may use the same branch name: skipped
        (and so is any PR whose origin is not stated)."""
        prs = json.loads(self._run("pr", "list", "--repo", self.repo, "--state", "open", "--limit", "200",
                                   "--json", "headRefName,isCrossRepository"))
        out = []
        for pr in prs:
            m = re.fullmatch(r"upload/issue-(\d+)", pr.get("headRefName") or "")
            if m and pr.get("isCrossRepository") is False:
                out.append(int(m.group(1)))
        return sorted(out)

    def open_upload_issues(self) -> list[int]:
        """Open `upload` issues the bot has not answered (no PR, no needs-info): a cancelled queued run may have dropped
        them, and uploads with nothing committable yet are tried again."""
        issues = json.loads(self._run("issue", "list", "--repo", self.repo, "--label", LABEL_UPLOAD, "--state", "open",
                                      "--limit", "200", "--json", "number,labels"))
        return sorted(i["number"] for i in issues if not {l["name"] for l in i["labels"]} & {LABEL_NEEDS_INFO, LABEL_PR})

    def pr_for(self, branch: str) -> int | None:
        prs = json.loads(self._run("pr", "list", "--repo", self.repo, "--head", branch, "--state", "open", "--json", "number"))
        return prs[0]["number"] if prs else None

    def pr_head(self, n: int) -> str:
        return self._run("pr", "view", str(n), "--repo", self.repo, "--json", "headRefName", "-q", ".headRefName")

    def pr_head_sha(self, n: int) -> str:
        return self._run("pr", "view", str(n), "--repo", self.repo, "--json", "headRefOid", "-q", ".headRefOid")

    def create_or_edit_pr(self, branch: str, title: str, body: str, draft: bool = False) -> str:
        n = self.pr_for(branch)
        if n:
            self._run("pr", "edit", str(n), "--repo", self.repo, "--title", title, "--body-file", "-", input=body)
            return f"https://github.com/{self.repo}/pull/{n}"
        return self._run("pr", "create", "--repo", self.repo, "--base", "main", "--head", branch,
                         "--title", title, "--body-file", "-", *(["--draft"] if draft else []), input=body)

    def set_draft(self, pr: int, draft: bool) -> None:
        now = self._run("pr", "view", str(pr), "--repo", self.repo, "--json", "isDraft", "-q", ".isDraft") == "true"
        if now != draft:
            self._run("pr", "ready", str(pr), "--repo", self.repo, *(["--undo"] if draft else []))

    def set_status(self, sha: str, ok: bool, text: str) -> None:
        """The commit status `intake/待整理` on the PR head: failure while a file still needs a maintainer."""
        self._api("POST", f"statuses/{sha}", {"state": "success" if ok else "failure", "context": STATUS_CONTEXT,
                                              "description": text[:140]})

    def dispatch(self, n: int, sweep: bool = True) -> None:
        """Start the intake workflow for issue n on main (GITHUB_TOKEN may start workflow_dispatch runs)."""
        self._run("workflow", "run", "intake.yml", "--repo", self.repo, "--ref", "main", "-f", f"issue={int(n)}",
                  "-f", f"sweep={'true' if sweep else 'false'}")

    def intake_cache(self, issue: int):
        from cache import IntakeCache
        return IntakeCache(self.repo, issue)


class Git:
    def __init__(self, repo: Path):
        self.repo = repo

    def _run(self, *args: str) -> str:
        return _run(["git", *args], cwd=self.repo)

    def reset_branch(self, name: str) -> None:
        """A clean tree at origin/main on branch `name`, whatever happened before."""
        self._run("fetch", "-q", "origin", "main")
        self._run("reset", "-q", "--hard")
        self._run("clean", "-qfd")
        self._run("checkout", "-q", "-B", name, "origin/main")

    def head(self) -> str:
        return self._run("rev-parse", "HEAD")

    def commit_all(self, message: str, author: str) -> None:
        self._run("add", "-A")
        self._run("commit", "-q", "-m", message, "--author", author)

    def force_push(self, name: str) -> None:
        self._run("push", "-q", "--force", "-u", "origin", name)


def claude_from_env(repo: str):
    """(asks, budget) for a real run: Claude and the dollar ledger when ANTHROPIC_API_KEY is set, otherwise nothing is
    asked (every kept file then waits under Needs you as not checked)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}, None
    import ai
    return ai.make_asks(), _cc().Budget(repo)


class _NoLedger:
    """The dry run's stand-in ledger: every reservation fits, nothing is booked anywhere."""

    def reserve(self, pool: str, usd: float) -> bool:
        return True

    def settle(self, pool: str, delta: float) -> None:
        pass


def fake_claude(f: Form):
    """asks for a dry run without a key: the sorter repeats the form's hints and is sure only when the form names a
    course; the check finds nothing (all "no", nothing to black out)."""
    hinted = bool(CODE_RE.match(f.code or ""))
    assessment = clean_label(f.assessment.replace(" ", "")) if hinted and f.type == "Past-Papers" else ""

    def sort(content, on_cost=None):
        return {"course_code": f.code if hinted else "", "course_name": f.name if hinted else "",
                "session": (f.session or "unknown") if hinted else "unknown", "type": f.type if hinted else "",
                "label": assessment, "assessment": assessment, "set": "", "keep": True, "skip_reason": "",
                "confident": hinted, "note": "干跑：假 AI | dry run: stand-in AI"}
    def check(content, on_cost=None):
        return {"answers": "no", "marking_scheme": "no", "personal_data": "no",
                "summary": "干跑：假 AI 没有读文件 | dry run: the stand-in AI read nothing", "notes": "",
                "lecturers": [], "weighting": "", "redactions": []}
    return {"sort": sort, "check": check}


def _pr_number(url: str) -> int:
    m = re.search(r"(\d+)/?\s*$", url or "")
    if not m:
        raise RuntimeError(f"no pull request number in {url!r}")
    return int(m.group(1))


def run_issue(n: int, repo: Path, gh: GH, git: Git, run_url: str | None, work: Path | None = None, sweep: bool = False,
              asks: dict | None = None, budget=None) -> None:
    """One issue → a comment with problems; or a rebuilt branch and PR (a draft with a failing `intake/待整理` status
    while any file needs a maintainer); or, when no file can be committed, one issue comment kept up to date.
    `asks` and `budget` stand in for Claude and the dollar ledger in tests; with `asks` left out both come from the
    environment (real Claude only when ANTHROPIC_API_KEY is set). Unexpected errors are reported and re-raised."""
    work = work or repo / "_intake"
    try:
        iss = gh.issue(n)
        if iss["state"] != "open":
            print(f"#{n} is {iss['state']}, skipped")
            return
        if LABEL_UPLOAD not in iss["labels"]:   # whatever asked (a fork PR's branch name, a dispatch): not an upload
            print(f"#{n} has no `{LABEL_UPLOAD}` label, skipped")
            return
        f = parse_form(iss["body"])
        branch = f"upload/issue-{n}"
        problems = validate(f)
        if problems:
            if sweep and LABEL_NEEDS_INFO in iss["labels"]:
                print(f"#{n}: still invalid, already told")
                return
            gh.comment(n, problems_comment(problems))
            gh.label(n, add=(LABEL_NEEDS_INFO,))
            pr = gh.pr_for(branch)
            if pr:
                gh.comment_pr(pr, f"#{n} 现在校验不通过，这个 PR 没有重建，保持原样 | #{n} no longer validates; this PR was left as it was.")
            print(f"#{n}: {len(problems)} problem(s), commented")
            return
        if LABEL_NEEDS_INFO in iss["labels"]:
            gh.label(n, remove=(LABEL_NEEDS_INFO,))
        git.reset_branch(branch)
        base = git.head()
        if asks is None:
            asks, budget = claude_from_env(getattr(gh, "repo", REPO))
        meter = None
        if budget is not None:
            import ai
            meter = ai.Meter(budget)
        store = gh.intake_cache(n)
        cache = store.load()            # a broken cache raises: without the maintainers' overrides nothing is safe to rebuild
        before = json.dumps(cache, sort_keys=True, ensure_ascii=False)
        apply_retry(cache)              # a new /retry: failed redactions and unconfirmed re-checks are asked again
        try:
            res = process(f, n, iss["login"], repo, work / f"issue-{n}", base_sha=base, run_url=run_url, branch=branch,
                          asks=asks, meter=meter, cache=cache, budget=budget)
        except (IntakeLimit, NothingAccepted) as e:
            rejected = getattr(e, "rejected", [])
            gh.comment(n, f"{e}\n\n{rejected_lines(rejected)}\n\n改好后编辑这个 Issue 即可重跑 | Edit this issue to run the bot again.".strip())
            gh.label(n, add=(LABEL_NEEDS_INFO,))
            print(f"#{n}: nothing accepted, commented")
            return
        finally:
            if meter is not None:
                meter.settle()
                _add_spent(cache, meter.spent)
            if json.dumps(cache, sort_keys=True, ensure_ascii=False) != before:
                try:
                    store.save(cache)
                except Exception as e:   # the AI results are lost for the next run, nothing worse
                    print(f"#{n}: could not save the cache ({type(e).__name__})", file=sys.stderr)
        existing = gh.pr_for(branch)
        if not res.committable:
            gh.upsert_comment(n, COMMENT_MARKER, res.comment)
            if existing:            # an older PR (say, from before a /skip): the same sections, and kept from merging
                gh.create_or_edit_pr(branch, pr_title(n, res), res.body)
                gh.set_draft(existing, True)
                gh.set_status(gh.pr_head_sha(existing), False, status_text(res))
            print(f"#{n}: nothing to commit yet, {res.held} file(s) need a maintainer; issue comment updated")
            return
        anonymous = f.credit == CREDIT_ANON
        author = BOT_AUTHOR if anonymous else f"{iss['login']} <{iss['id']}+{iss['login']}@users.noreply.github.com>"
        git.commit_all(commit_message(n, res), author)
        git.force_push(branch)
        url = gh.create_or_edit_pr(branch, pr_title(n, res), res.body, draft=res.blocking)
        pr = existing or _pr_number(url)
        gh.set_draft(pr, res.blocking)
        gh.set_status(git.head(), not res.blocking, status_text(res))
        if not existing:  # rebuilds stay quiet: the PR body footer says when and from which main it was rebuilt
            skipped = f"\n\n未收录 | Not included:\n{rejected_lines(res.rejected)}" if res.rejected else ""
            gh.comment(n, f"已整理成 PR，等维护者审核：{url}\n要改内容就编辑这个 Issue，PR 会自动更新。"
                          f"\n\nFiled as a pull request for review: {url}\nTo change anything, edit this issue and the PR updates itself.{skipped}")
            gh.label(n, add=(LABEL_PR,))
        print(f"#{n}: {res.committable} file(s), {res.held} need a maintainer → {url}")
    except Exception:
        try:
            gh.comment(n, "机器人出错了，维护者会手动处理 | The bot hit an error; a maintainer will handle this by hand."
                          + (f"\n\n{run_url}" if run_url else ""))
        except Exception:
            pass
        raise


UNAUTHORIZED = ("只有仓库的 OWNER、MEMBER 或 COLLABORATOR 能用这些指令，这条留言没有执行 "
                "| Only the repository's owners, members and collaborators can use these commands; nothing was done.")


RETRY_KEY = "_retry"     # overrides[RETRY_KEY] = {"seq": n}: the n-th /retry (not a file: file keys are sha256 hex)


def _retry_seq(overrides) -> int:
    r = overrides.get(RETRY_KEY) if isinstance(overrides, dict) else None
    seq = r.get("seq") if isinstance(r, dict) else 0
    return seq if type(seq) is int and seq > 0 else 0


def apply_retry(cache: dict) -> bool:
    """At the start of a run: a /retry not acted on yet (overrides["_retry"]["seq"] above cache["retry_done"]) drops
    every check whose redaction failed (`redactions_ok` False) and every check whose re-check verdict is not "no",
    with that verdict, so this run checks those files again from scratch, and clears every failed-check count
    (cache["check_failed"]), so files held after ai.CHECK_TRIES failures are asked again; then retry_done = seq, saved
    with the run's results. Ordinary runs drop nothing. True when it acted."""
    seq = _retry_seq(cache.get("overrides"))
    done = cache.get("retry_done")
    if not seq or (type(done) is int and seq <= done):
        return False
    checks, verdicts = _found(cache, "check"), _found(cache, "verify")
    drop = {sha for sha, v in checks.items() if isinstance(v, dict) and v.get("redactions_ok") is False}
    drop |= {sha for sha, v in verdicts.items() if v != "no"}
    for sha in drop:
        checks.pop(sha, None)
        verdicts.pop(sha, None)
    if "check_failed" in cache:
        cache["check_failed"] = {}      # emptied, not deleted: a save keeps the stored keys a run leaves out
    cache["retry_done"] = seq
    return True


def store_commands(event: dict, gh) -> int | None:
    """The first half of a maintainer command (spec §7), run outside the issue's concurrency group so a queued rebuild
    can never cancel it: store the commands, answer the comment, and return the issue to rebuild (None: nothing to do).
    Ignored: comments that do not start with "/", words that are not ours, and threads that are neither an `upload`
    issue nor a PR from `upload/issue-<n>`. An author who is not OWNER / MEMBER / COLLABORATOR gets one bilingual reply
    and nothing else. Commands are compare-and-swapped into the cache's `overrides` by file hash (`#k` resolved through
    cache["items"] of the same read), retried on a Conflict; every other key stays as stored. The comment gets 👍, or
    a reply naming what could not be done. `/retry` asks for the rebuild (a file that got no answer has no cached
    check, so it is asked again) and bumps overrides["_retry"]["seq"] in the same compare-and-swap: the next run then
    drops failed redactions and unconfirmed re-checks and clears the failed-check counts, so files held after
    repeated failures are asked again too (apply_retry). Runs never write overrides, so a rebuild that is already
    running cannot undo it."""
    import commands
    c = event.get("comment") or {}
    if not str(c.get("body") or "").lstrip().startswith("/"):
        return None
    cmds, parse_errors = commands.parse(c.get("body") or "")
    if not cmds and not parse_errors:              # another bot's /word
        return None
    thread = event.get("issue") or {}
    where = int(thread.get("number") or 0)
    if thread.get("pull_request"):
        m = re.fullmatch(r"upload/issue-(\d+)", gh.pr_head(where) or "")
        if not m:
            return None
        n = int(m.group(1))
    elif LABEL_UPLOAD in {l.get("name") for l in thread.get("labels") or [] if isinstance(l, dict)}:
        n = where
    else:
        return None
    if not commands.authorized(str(c.get("author_association") or "")):
        gh.comment(where, UNAUTHORIZED)
        return None
    login = str((c.get("user") or {}).get("login") or "")
    retry = any(x.kind == "retry" for x in cmds)
    rebuild = retry
    todo = [x for x in cmds if x.kind != "retry"]
    unknown: list[str] = []

    def apply(data: dict) -> dict | None:
        """The commands against one read of the cache; run again on every retry."""
        unknown.clear()
        shas = {}
        for i in data.get("items") or []:
            if isinstance(i, dict) and str(i.get("k", "")).isdigit() and i.get("sha"):
                shas[int(i["k"])] = str(i["sha"])
        stored = data.get("overrides")
        overrides = {s: o for s, o in stored.items() if isinstance(o, dict)} if isinstance(stored, dict) else {}
        changed = False
        for x in todo:
            sha = shas.get(x.k)
            if not sha:
                unknown.append(f"没有 #{x.k} 这个文件，编号见 PR 正文或机器人的评论 "
                               f"| there is no file #{x.k}; the numbers are in the PR body or the bot's comment")
                continue
            overrides[sha] = commands.merge(overrides.get(sha), x, login)
            changed = True
        if retry:
            overrides[RETRY_KEY] = {"seq": _retry_seq(overrides) + 1}
            changed = True
        return overrides if changed else None
    if (todo or retry) and gh.intake_cache(n).update_overrides(apply) is not None:
        rebuild = True
    errors = parse_errors + unknown
    if errors:
        done = "\n\n其余指令已执行 | The other commands were applied." if rebuild else ""
        gh.comment(where, "\n".join(f"- {e}" for e in errors) + done)
    else:
        gh.react(int(c.get("id") or 0), "+1")
    return n if rebuild else None


def handle_comment(event: dict, gh, git, repo: Path, run) -> None:
    """store_commands, then `run(n)` for the issue it names (in the workflow the two halves are separate jobs)."""
    n = store_commands(event, gh)
    if n is not None:
        run(n)


def set_output(name: str, value) -> None:
    """A step output for the workflow (the file $GITHUB_OUTPUT names); None writes an empty value."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as out:
            out.write(f"{name}={'' if value is None else value}\n")


def dispatch_open(gh) -> tuple[list[int], list[int]]:
    """The push sweep: one workflow run per open upload (PRs, and upload issues the bot has not answered), each in its
    issue's own concurrency group, instead of rebuilding them all here outside those groups. A failed dispatch is
    reported and the sweep goes on. Returns (dispatched, failed)."""
    done, failed = [], []
    for n in sorted(set(gh.open_upload_prs()) | set(gh.open_upload_issues())):
        try:
            gh.dispatch(n)
            done.append(n)
        except Exception as e:
            print(f"#{n}: could not start its rebuild: {e}", file=sys.stderr)
            failed.append(n)
    return done, failed


def main(argv: list[str] | None = None) -> int:
    import argparse
    import traceback
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--issue", type=int, help="process this issue")
    ap.add_argument("--sweep", action="store_true", help="with --issue: a sweep's rebuild, quiet when the form is still invalid")
    ap.add_argument("--all-open", action="store_true", help="rebuild every open upload/issue-N pull request here, one by one")
    ap.add_argument("--dispatch-open", action="store_true",
                    help="start one intake workflow run per open upload (the push sweep; each run in its issue's group)")
    ap.add_argument("--comment-event", help="store the maintainer commands in this GitHub issue_comment event (JSON file) "
                                            "and write the issue to rebuild to $GITHUB_OUTPUT (issue=<n>)")
    ap.add_argument("--dry-run", action="store_true", help="no network: --form FILE --files F... --repo DIR "
                                                         "(a stand-in AI unless ANTHROPIC_API_KEY is set)")
    ap.add_argument("--form", help="dry run: a file holding the rendered issue body")
    ap.add_argument("--files", nargs="*", default=[], help="dry run: local files standing in for the attachments")
    ap.add_argument("--repo", default=str(HERE.parent), help="checkout to modify")
    ap.add_argument("--login", default="someone", help="dry run: uploader's GitHub login")
    ap.add_argument("--work", help="scratch directory (default <repo>/_intake)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    repo = Path(args.repo).resolve()
    work = Path(args.work).resolve() if args.work else repo / "_intake"
    run_url = os.environ.get("GITHUB_RUN_URL")

    if args.dry_run:
        import ai
        f = parse_form(Path(args.form).read_text(encoding="utf-8"))
        work.mkdir(parents=True, exist_ok=True)
        problems = validate(f)
        if problems:
            (work / "PROBLEMS.md").write_text(problems_comment(problems), encoding="utf-8")
            print(f"{len(problems)} problem(s) -> {work / 'PROBLEMS.md'}")
            return 0
        issue = args.issue or 0
        if os.environ.get("ANTHROPIC_API_KEY"):
            asks, budget = claude_from_env(REPO)
            meter = ai.Meter(budget)
        else:
            asks, budget = fake_claude(f), None
            meter = ai.Meter(_NoLedger(), run_cap=float("inf"))
        try:
            res = process(f, issue, args.login, repo, work / f"issue-{issue}", files=[Path(p) for p in args.files],
                          asks=asks, meter=meter, cache={}, budget=budget)
        except (IntakeLimit, NothingAccepted) as e:
            (work / "PROBLEMS.md").write_text(str(e), encoding="utf-8")
            print(f"nothing accepted -> {work / 'PROBLEMS.md'}")
            return 0
        finally:
            meter.settle()
        name, other = ("PR_BODY.md", "ISSUE_COMMENT.md") if res.committable else ("ISSUE_COMMENT.md", "PR_BODY.md")
        (work / other).unlink(missing_ok=True)
        (work / name).write_text(res.body if res.committable else res.comment, encoding="utf-8")
        print(f"{res.committable} file(s) committed, {res.held} need a maintainer; {name} -> {work / name}")
        return 0

    gh, git = GH(), Git(repo)
    if args.comment_event:
        event = json.loads(Path(args.comment_event).read_text(encoding="utf-8"))
        try:
            n = store_commands(event, gh)
        except Exception:
            traceback.print_exc()
            return 1
        set_output("issue", n)
        print(f"rebuild #{n}" if n else "nothing to rebuild")
        return 0
    if args.dispatch_open:
        done, failed = dispatch_open(gh)
        print(f"dispatched {len(done)} rebuild(s): {' '.join(f'#{n}' for n in done) or 'none'}"
              + (f"; failed: {' '.join(f'#{n}' for n in failed)}" if failed else ""))
        return 1 if failed else 0
    gh.ensure_labels()
    issues = [args.issue] if args.issue else sorted(set(gh.open_upload_prs()) | set(gh.open_upload_issues())) if args.all_open else []
    if not issues:
        print("no open upload PRs" if args.all_open else
              "nothing to do: pass --issue N, --all-open, --dispatch-open or --comment-event FILE")
        return 0
    failed = 0
    for n in issues:
        try:
            run_issue(n, repo, gh, git, run_url, work, sweep=args.all_open or args.sweep)
        except Exception:
            traceback.print_exc()
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
