"""Generate the static website from this repository.

用法 / Usage:  python site/build.py [--out _site] [--prefix /XMUM-SAIR-Survival-Guide/]

扫描 EEE/ AIT/ ROBO/ GE/ 下的课程文件夹，渲染仓库里的 markdown，生成 HTML 和搜索索引到
_site/，最后对生成结果做自检。设计说明见 site/DESIGN.md。

Scans the course folders under EEE/ AIT/ ROBO/ GE/, renders the repo's markdown,
writes HTML plus a search index to _site/ and checks its own output.
See site/DESIGN.md for the design.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import html as html_mod
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from urllib.parse import quote, unquote

import markdown

REPO = "xmum-sair/xmum-sair.github.io"
GH = f"https://github.com/{REPO}"
RAW = f"https://raw.githubusercontent.com/{REPO}/main"
SITE_URL = "https://xmum-sair.github.io/"   # canonical address; changes with REPO after the org move
DOWNLOADS = f"{GH}/releases/download/downloads"                      # per-session zips kept by .github/workflows/downloads.yml
# the site's Cloudflare Worker (assistant/): the helper and, since 2026-11-06, the course reviews. pages.yml passes the
# repository variables; the defaults are the deployed Worker and its public Turnstile site key.
WORKER_URL = os.environ.get("ASSISTANT_URL", "").strip().rstrip("/") or "https://xmum-sair-assistant.xieqiyi.workers.dev"
TURNSTILE_SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "").strip() or "0x4AAAAAAF1_G8n4iH_6hvtK"
RATINGS_URL = f"{WORKER_URL}/ratings"
LEGACY_RATINGS_URL = f"https://raw.githubusercontent.com/{REPO}/data/ratings.json"   # the GitHub form's last file: fallback only
# the discussion area's boards (names and icons); assistant/src/forum.js keeps the same keys, check() compares them
BOARDS_CFG = json.loads((Path(__file__).resolve().parent.parent / "discussions" / "boards.json").read_text(encoding="utf-8"))
PROGS = {
    "EEE": "电子信息工程 | Electrical & Electronic Engineering",
    "AIT": "人工智能 | Artificial Intelligence",
    "ROBO": "机器人与自动化工程 | Robotics & Automation Engineering",
    "GE": "公共课 | University-wide courses",
}
PROGRAMMES = ("EEE", "AIT", "ROBO")   # degree programmes, each with a timeline; GE/ holds the G electives and MPU subjects all of them take
LEGACY_DIR = {"GE": "EEE"}            # GE courses sat under EEE/ until 2026-11-06; their old addresses redirect
PROG_ICONS = {"EEE": "⚡", "AIT": "🧠", "ROBO": "🤖", "GE": "🌐"}
SUBS = ("Past-Papers", "Syllabus", "Assignments", "Labs", "Tutorials",
        "Handbooks", "Notes", "Exam-Review", "Tips")
SESSION_RE = re.compile(r"\d{4}-\d{2}")
# timeline rows "| 2022-04 | [COM107](folder/) | ..." — same regex as scripts/update_index.py
TAKEN_RE = re.compile(r"^\| (\d{4}-\d{2}) \| \[?([^\]|]+?)\]?(?:\([^)]*\))? \|")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


@dataclass
class File:
    prog: str
    code: str
    course: str
    session: str
    type: str
    name: str
    path: str   # repo-relative posix path
    size: int


@dataclass
class Course:
    prog: str
    dir: str
    code: str
    name: str
    path: str   # "EEE/COM107_Circuits_and_Devices"
    readme: str | None
    sessions: dict[str, dict[str, list[File]]] = field(default_factory=dict)
    taken_in: list[str] = field(default_factory=list)
    lecturers: dict[str, str] = field(default_factory=dict)
    contributors: dict[str, str] = field(default_factory=dict)   # session → the README's 资料来自 cell


def warn(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr)


def parse_taken(readme_text: str) -> dict[str, list[str]]:
    taught: dict[str, list[str]] = {}
    for line in readme_text.splitlines():
        m = TAKEN_RE.match(line)
        if m:
            sess, code = m.groups()
            sessions = taught.setdefault(code.strip(), [])
            if sess not in sessions:
                sessions.append(sess)
    return {code: sorted(s) for code, s in taught.items()}


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_lecturers(readme_text: str) -> dict[str, str]:
    """Session → lecturer from the first table (session cell may be a link)."""
    return parse_column(readme_text, 1)


def parse_contributors(readme_text: str) -> dict[str, str]:
    """Session → the 资料来自 / Contributed by cell of the first table."""
    return parse_column(readme_text, 3)


def parse_column(readme_text: str, col: int) -> dict[str, str]:
    rows = []
    for line in readme_text.splitlines():
        if line.lstrip().startswith("|"):
            rows.append(line)
        elif rows:
            break
    out: dict[str, str] = {}
    for line in rows[2:]:   # skip header and separator
        cells = _cells(line)
        if len(cells) <= col:
            continue
        session = LINK_RE.sub(r"\1", cells[0]).strip()
        if (SESSION_RE.fullmatch(session) or session == "unknown") and cells[col]:
            out[session] = cells[col]
    return out


def _session_key(s: str) -> tuple[int, str]:
    """Sort key so that reverse=True gives newest first and 'unknown' last."""
    return (-1, "") if s == "unknown" else (0, s)


def scan(root: Path) -> list[Course]:
    courses: list[Course] = []
    taken: dict[str, list[str]] = {}   # every programme's timeline counts: a G or MPU course is listed by whoever took it
    for prog in PROGRAMMES:
        preadme = root / prog / "README.md"
        if preadme.is_file():
            for code, sessions in parse_taken(preadme.read_text(encoding="utf-8")).items():
                taken[code] = sorted(set(taken.get(code, [])) | set(sessions))
    for prog in PROGS:
        pdir = root / prog
        if not pdir.is_dir():
            continue
        for d in sorted(os.listdir(pdir)):
            cdir = pdir / d
            if not cdir.is_dir() or "_" not in d:
                continue
            code, name = d.split("_", 1)
            rpath = cdir / "README.md"
            readme = rpath.read_text(encoding="utf-8") if rpath.is_file() else None
            if readme is None:
                warn(f"{prog}/{d}: README.md missing")
            course = Course(prog, d, code, name.replace("_", " "), f"{prog}/{d}", readme,
                            taken_in=taken.get(code, []),
                            lecturers=parse_lecturers(readme) if readme else {},
                            contributors=parse_contributors(readme) if readme else {})
            sessions = [s for s in os.listdir(cdir) if (cdir / s).is_dir()
                        and (SESSION_RE.fullmatch(s) or s == "unknown")]
            for sess in sorted(sessions, key=_session_key, reverse=True):
                types: dict[str, list[File]] = {}
                for sub in SUBS:
                    sdir = cdir / sess / sub
                    if not sdir.is_dir():
                        continue
                    files = [File(prog, code, course.name, sess, sub, f,
                                  f"{course.path}/{sess}/{sub}/{f}", (sdir / f).stat().st_size)
                             for f in sorted(os.listdir(sdir))
                             if not f.startswith(".") and (sdir / f).is_file()]
                    if files:
                        types[sub] = files
                if types:
                    course.sessions[sess] = types
            courses.append(course)
    return courses


def all_files(courses: list[Course]) -> list[File]:
    return [f for c in courses for types in c.sessions.values() for files in types.values() for f in files]


# ---------------------------------------------------------------- rendering

# repo markdown → site page (directory with index.html)
ROOT_PAGES = {
    "README.md": "about/",
    "DISCLAIMER.md": "disclaimer/",
    "CONTRIBUTING.md": "contributing/",
}
# repo markdown folded into another page: links to it go to that page's section
LINK_ALIASES = {"CONTRIBUTORS.md": "about/#contributors", "Lecturers.md": "reviews/#lecturers",
                "Research-Opportunities.md": "opportunities/"}
# addresses that used to be pages
REDIRECTS = {"contributors/": "about/#contributors", "lecturers/": "reviews/#lecturers", "research/": "opportunities/"}
REDIRECT_MARK = '<meta http-equiv="refresh"'


class Site:
    def __init__(self, root: Path, prefix: str):
        self.root = Path(root)
        self.prefix = prefix
        self.courses = scan(self.root)
        self.by_path = {c.path: c for c in self.courses}
        self.topics: dict[str, dict] = {}            # repo path → {topics, about} from topics/topics.py


def url_path(path: str) -> str:
    return quote(path, safe="/")


def slugify(text: str, sep: str) -> str:
    """GitHub-style heading id: lowercase, drop punctuation, spaces → sep (repeats kept)."""
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return text.strip().replace(" ", sep)


# ---------------------------------------------------------------- lecturers, recent uploads, wanted list, sitemap

TITLE_RE = re.compile(r"^(?:dr|prof|professor|assoc|associate|asst|assistant|puan|encik|en|cik|mr|mrs|ms|ir|ts)\.?\s+", re.I)


def lecturer_slug(name: str) -> str:
    """Same key as reviews/reviews.py uses: titles dropped, lowercase, hyphens."""
    name = re.sub(r"\s+", " ", name).strip()
    while TITLE_RE.match(name):
        name = TITLE_RE.sub("", name, count=1)
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def ratings_html(attrs: str, rate_url: str | None = None) -> str:
    """A container static/ratings.js fills from the Worker's ratings (the old data-branch file if the Worker is down),
    plus the link to the site's rating form."""
    btn = f' <a class="btn" href="{esc(rate_url)}">评价这门课 | Rate this course</a>' if rate_url else ""
    return (f'<div class="ratings" {attrs} data-src="{RATINGS_URL}" data-fallback="{LEGACY_RATINGS_URL}">'
            '<p class="muted">正在读取评价… | Loading ratings…</p></div>'
            f'<p class="rate">{btn} <small>匿名打分，满 3 人显示平均分 | anonymous scores; averages appear at 3 voters</small></p>')


def split_lecturers(cell: str) -> list[str]:
    """A README cell may name several people: 'A, B', 'A、B', 'A & B'. A slash stays: 'Lohgheswary A/P Nagarethinam' is one person."""
    return [n.strip() for n in re.split(r"[,;、&]| and ", cell) if n.strip() and not n.strip().startswith("[")]


def lecturers(site: Site) -> dict[str, list[tuple[str, str]]]:
    """Lecturer → [(course code, session)], from every course README's session table."""
    out: dict[str, list[tuple[str, str]]] = {}
    for c in site.courses:
        for sess, cell in c.lecturers.items():
            for name in split_lecturers(cell):
                out.setdefault(name, []).append((c.code, sess))
    return dict(sorted(out.items(), key=lambda kv: kv[0].lower()))


def known_lecturers(site: Site) -> str:
    """Slugs of the lecturers that have a directory page; ratings.js links a name only when it is listed here
    (a review can name a lecturer no course README mentions)."""
    if not hasattr(site, "_known"):
        site._known = " ".join(sorted({lecturer_slug(n) for n in lecturers(site)}))
    return site._known


def lecturer_links_html(site: Site, cell: str) -> str:
    names = split_lecturers(cell)
    if not names:
        return esc(cell)
    return "、".join(f'<a href="{site.prefix}lecturers/{lecturer_slug(n)}/">{esc(n)}</a>' for n in names)


def rate_cta(site: Site) -> str:
    return (f'<p class="rate-cta"><a class="btn big" href="{site.prefix}rate/">➕ 添加评价 | Add a review</a> '
            '<small>一分钟，不用登录 | a minute, no login</small></p>')


def lecturer_directory_html(site: Site) -> str:
    """Every lecturer named in a course README, as chips on /reviews/; the review search box finds them too."""
    chips = []
    for name, taught in lecturers(site).items():
        codes = " ".join(sorted({code for code, _ in taught}))
        chips.append(f'<a class="chip" href="{site.prefix}lecturers/{lecturer_slug(name)}/">{esc(name)} <small>{esc(codes)}</small></a>')
    return ('<h2 id="lecturers">教师 | Lecturers</h2>'
            '<p class="muted">课程 README 里出现过的老师，点进去看教过哪些课。评分只在课程评价里，本站不收关于老师的文字评价。'
            '<br>Lecturers named in the course READMEs; open one for the courses they taught. '
            'Scores live only in course reviews; this site takes no written reviews of lecturers.</p>'
            f'<div class="chips">{"".join(chips)}</div>')


def lecturer_page(site: Site, name: str, taught: list[tuple[str, str]]) -> str:
    """Courses taught, with links to the session and to the course's review section. Nothing is written about the person."""
    rows = []
    for code, sess in sorted(taught, key=lambda t: (t[0], t[1])):
        c = next((c for c in site.courses if c.code == code), None)
        if c is None:
            continue
        rows.append(f'<tr><td>{esc(code)}</td><td><a href="{site.prefix}{c.path}/">{esc(c.name)}</a></td>'
                    f'<td><a href="{site.prefix}{c.path}/#{sess}">{esc(sess)}</a></td>'
                    f'<td><a href="{site.prefix}{c.path}/#review">课程评价 | Course review</a></td></tr>')
    table = ('<div class="table-wrap"><table><thead><tr><th>代码 / Code</th><th>课程 / Course</th><th>学期 / Session</th>'
             f'<th>评分 / Ratings</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    content = (f'<p class="meta"><a href="{site.prefix}reviews/#lecturers">← 全部老师 | All lecturers</a></p><h1>{esc(name)}</h1>'
               '<p class="muted">名单来自各课程文件夹 README 的学期表。评分只在课程页的「课程评价」里，按课程和老师列出；本站不收关于老师的文字评价。'
               "<br>From the session tables in the course READMEs. Scores live only in each course's review section, listed per course and lecturer; "
               "this site takes no written reviews of lecturers.</p>"
               f"<h2>教过的课 | Courses taught</h2>{table}")
    return page(site, name, content, f"{name}：教过的课程 | courses taught")


def parse_added(raw: str) -> dict[str, str]:
    """`git log --format=%cs --name-status -M --diff-filter=AR` output → path → date the file first entered the repo,
    following renames, so moving a folder (EEE/ → GE/) does not make old files look new."""
    added: dict[str, str] = {}
    alias: dict[str, str] = {}   # new path → the path it was renamed from
    date = ""
    for line in raw.splitlines():
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", line):
            date = line
            continue
        parts = line.split("\t")
        if parts[0] == "A" and len(parts) == 2:
            added[parts[1]] = date            # newest commit first, so the last assignment wins: the earliest add
        elif parts[0].startswith("R") and len(parts) == 3:
            alias.setdefault(parts[2], parts[1])

    def first(path: str) -> str | None:
        seen = set()
        while path not in added and path in alias and path not in seen:
            seen.add(path)
            path = alias[path]
        return added.get(path)

    return {p: d for p in {*added, *alias} if (d := first(p))}


def added_dates(root: Path) -> dict[str, str]:
    """Repo path → date (YYYY-MM-DD) the file first appeared, from git history; {} when there is none."""
    try:
        raw = subprocess.run(["git", "-c", "core.quotepath=off", "log", "--format=%cs", "--name-status", "-M",
                              "--diff-filter=AR", "--", *PROGS], cwd=root, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        warn("git history unavailable, the recent-uploads list is empty")
        return {}
    return parse_added(raw.decode("utf-8", "replace"))


def recent_entries(site: Site, limit: int = 100) -> list[dict]:
    """Uploads grouped by (date, course, session, type), newest first."""
    dates = added_dates(site.root)
    if not dates:
        return []
    groups: dict[tuple, dict] = {}
    for c in site.courses:
        for sess, types in c.sessions.items():
            for t, files in types.items():
                for f in files:
                    d = dates.get(f.path)
                    if not d:
                        continue
                    g = groups.setdefault((d, c.code, sess, t), {
                        "date": d, "prog": c.prog, "code": c.code, "name": c.name, "session": sess, "type": t, "n": 0,
                        "url": f"{site.prefix}{c.path}/#{sess}", "by": LINK_RE.sub(r"\1", c.contributors.get(sess, ""))})
                    g["n"] += 1
    return sorted(groups.values(), key=lambda g: (g["date"], g["code"], g["session"], g["type"]), reverse=True)[:limit]


def recent_html(entries: list[dict], compact: bool = False) -> str:
    if not entries:
        return "<p>暂时无法读取上传记录 | Upload history is not available right now.</p>"
    items = []
    for e in entries:
        by = f" · 来自 | by {esc(e['by'])}" if e["by"] else ""
        if compact:   # programme pages: date, course, what was added
            items.append(f'<li><time datetime="{e["date"]}">{e["date"][5:]}</time> <a href="{e["url"]}">{esc(e["code"])} {esc(e["name"])}</a> '
                         f'<small>+{e["n"]} {esc(e["type"])} · {esc(e["session"])}</small></li>')
            continue
        items.append(f'<li><time datetime="{e["date"]}">{e["date"]}</time> <a href="{e["url"]}">{esc(e["code"])} {esc(e["name"])}</a> '
                     f'<small>{esc(e["session"])} · {esc(e["type"])} · {e["n"]} 份 | files{by}</small></li>')
    return f'<ul class="recent{" compact" if compact else ""}">{"".join(items)}</ul>'


def recent_page(site: Site) -> str:
    content = ("<h1>最近上传 | Recent uploads</h1>"
               f'<p>按文件进入仓库的日期排列，最新在前。订阅：<a href="{site.prefix}feed.xml">RSS / Atom</a>。'
               f'<br>By the date each file entered the repository, newest first. Subscribe: <a href="{site.prefix}feed.xml">RSS / Atom</a>.</p>'
               + recent_html(getattr(site, "recent", [])))
    return page(site, "最近上传 | Recent uploads", content)


def feed_xml(site: Site, entries: list[dict]) -> str:
    def x(s: str) -> str:
        return html_mod.escape(s, quote=True)
    updated = (entries[0]["date"] if entries else "2026-01-01") + "T00:00:00Z"
    items = []
    for e in entries[:50]:
        url = SITE_URL + e["url"][len(site.prefix):]
        title = f"{e['code']} {e['session']} · {e['type']}（{e['n']} 份 | files）"
        summary = f"{e['code']} {e['name']} {e['session']} {e['type']}: {e['n']} 份文件 | files" + (f"，来自 | by {e['by']}" if e["by"] else "")
        items.append(f"<entry><title>{x(title)}</title><link href=\"{x(url)}\"/>"
                     f"<id>{x(SITE_URL)}recent/#{x(e['date'])}-{x(e['code'])}-{x(e['session'])}-{x(e['type'])}</id>"
                     f"<updated>{e['date']}T00:00:00Z</updated><summary>{x(summary)}</summary></entry>")
    return ('<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">'
            f'<title>XMUM SAIR 生存指南 · 最近上传 | Recent uploads</title><link href="{x(SITE_URL)}"/>'
            f'<link rel="self" href="{x(SITE_URL)}feed.xml"/><id>{x(SITE_URL)}</id><updated>{updated}</updated>'
            + "".join(items) + "</feed>\n")


def wanted_page(site: Site) -> str:
    parts = ["<h1>征集榜 | Wanted</h1>",
             f'<p>下面这些课还没有任何资料。修过的同学如果手上有空白试卷、大纲、作业题或笔记，<a href="{site.prefix}upload/">上传</a>给我们。'
             f'<br>These courses have no material yet. If you took one and still have blank papers, a syllabus, assignment briefs or notes, '
             f'<a href="{site.prefix}upload/">upload them</a>.</p>']
    def item(c: Course, extra: str = "") -> str:
        return f'<li><a href="{site.prefix}{c.path}/">{esc(c.code)} {esc(c.name)}</a>{extra}</li>'

    def section(title: str, items: list[str]) -> str:
        return f"<h3>{title}（{len(items)}）</h3>" + (f"<ul>{''.join(items)}</ul>" if items else "<p>无 | none</p>")

    parts.append(forum_html(site, "requests"))   # material requests from the discussion area, filled by static/forum.js

    for prog, pname in PROGS.items():
        cs = [c for c in site.courses if c.prog == prog]
        if not cs:
            continue
        empty = [item(c, f' <small>修课学期 | taken in {esc("、".join(c.taken_in))}</small>' if c.taken_in else "")
                 for c in cs if not c.sessions]
        no_papers = [item(c) for c in cs if c.sessions and not any("Past-Papers" in types for types in c.sessions.values())]
        gaps = [item(c, f" <small>· {esc(s)}</small>") for c in cs for s in c.taken_in if s not in c.sessions]
        parts.append(f"<h2>{prog} · {esc(pname)}</h2>"
                     + section("还没有任何资料 | No material at all", empty)
                     + section("缺空白试卷 | No past papers yet", no_papers)
                     + section("有人修过、但那个学期还没资料 | Sessions someone took that are not covered", gaps))
    parts.append("<p>⬜ 征集中 / wanted 也会标在各专业页的课程索引里。| The programme pages mark courses without material as wanted too.</p>")
    return page(site, "征集榜 | Wanted", "".join(parts))


def reviews_page(site: Site) -> str:
    content = ("<h1>课程评价 | Course review</h1>" + rate_cta(site) +
               "<p>匿名打分，不写字：课程质量、老师质量、难度、作业量四个主维度，另有四个可选细分，全部 1–5 分；"
               "每人对同一门课同一位老师一票，满 3 人显示平均分。"
               "<br>Anonymous scores, no text: course quality, instructor quality, difficulty and work required, plus four optional extras, "
               "all 1–5. One vote per person per course and lecturer; averages appear at 3 voters.</p>"
               '<form class="lect-find" role="search"><label for="rv-q">搜课程或老师 | Search a course or lecturer</label>'
               '<input id="rv-q" type="search" autocomplete="off" placeholder="课程代码、课名或老师 | code, course name or lecturer"></form>'
               '<div id="rv-list"></div><section id="rv-panel" class="rv-panel" hidden></section>'
               '<section id="rv-all"><h2 id="rated">已有评价的课程 | Rated courses</h2>'
               '<p class="muted">点一门课，看它的总平均和每位老师的分数。| Open a course for its overall scores and each lecturer\'s.</p>'
               + ratings_html(f'data-all data-courses="{site.prefix}search.json" data-form="{site.prefix}rate/?from=reviews" data-known="{known_lecturers(site)}"') + "</section>"
               + lecturer_directory_html(site))
    return page(site, "课程评价 | Course review", content, "匿名课程评分：课程质量、老师、难度、作业量 | anonymous course ratings")


TYPE_LABELS = {"Past-Papers": "空白往年试卷 | blank past papers", "Syllabus": "大纲、课程介绍 | syllabus",
               "Assignments": "作业题目 | assignment briefs", "Labs": "实验手册 | lab manuals", "Tutorials": "习题课题目 | tutorials",
               "Handbooks": "指南、模板、评分标准 | guides, templates, rubrics", "Notes": "笔记 | notes",
               "Exam-Review": "复习资料 | revision", "Tips": "学习建议 | tips"}


def upload_page(site: Site) -> str:
    """GitHub issue forms cannot suggest values, so this page does: known courses and lecturers appear as you type,
    anything else is taken as new, and the answers are passed to the GitHub form, where only the files are left to add."""
    data = {"courses": {c.code: {"name": c.name, "prog": c.prog,
                                 "lecturers": sorted({n for cell in c.lecturers.values() for n in split_lecturers(cell)})}
                        for c in site.courses},
            "lecturers": list(lecturers(site))}
    codes = "".join(f'<option value="{esc(c.code)}">{esc(c.name)}</option>' for c in sorted(site.courses, key=lambda c: c.code))
    lects = "".join(f'<option value="{esc(n)}"></option>' for n in data["lecturers"])
    years = "".join(f'<option value="{y}">{y}</option>' for y in range(datetime.date.today().year + 1, 2017, -1))
    types = "".join(f'<option value="{t}">{t} · {esc(TYPE_LABELS[t])}</option>' for t in SUBS)
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = (
        "<h1>上传资料 | Upload material</h1>"
        "<p>拖文件就行，剩下的交给机器人。<br><small>Just drop your files; the bot does the rest.</small></p>"
        '<ol class="rate-steps">'
        "<li>点下面的按钮。知道课程、老师、学期的话可以先写在「给机器人的提示」里，怎么写都行；不写也行。"
        "<br><small>Press the button below. If you know the course, lecturer or session, you can add them under Hints first, in any format; you can also leave them out.</small></li>"
        "<li>GitHub 表单会打开：选你的专业，把文件拖进「文件」框，勾选确认后提交（需要 GitHub 账号）。多份文件、zip、不同课程混在一起都行。"
        "<br><small>The GitHub form opens: pick your programme, drag your files into the Files box, tick the checklist and submit (needs a GitHub account). "
        "Several files, zips and mixed courses are all fine.</small></li>"
        "<li>几分钟内机器人回复一个整理好的 PR，维护者审核合并后网站更新。"
        "<br><small>Within minutes the bot replies with a pull request; once a maintainer merges it, the site updates.</small></li></ol>"
        f'<form id="up-form" class="up-form" data-form="{UPLOAD_URL}" novalidate>'
        '<details class="up-hints"><summary>给机器人的提示（选填）<small>Hints for the bot (optional)</small></summary>'
        '<label for="up-code">课程代码 | Course code</label>'
        '<input id="up-code" list="up-codes" autocomplete="off" placeholder="例如 COM107、MPU1.1、G0191 | e.g. COM107">'
        f'<datalist id="up-codes">{codes}</datalist>'
        '<p id="up-course-note" class="hint" aria-live="polite"></p>'
        '<div id="up-name-row"><label for="up-name">课程名称 | Course name</label>'
        '<input id="up-name" autocomplete="off" placeholder="试卷或大纲上的英文名 | as printed, e.g. Circuits and Devices"></div>'
        '<label for="up-lect">老师 | Lecturer</label>'
        '<input id="up-lect" list="up-lects" autocomplete="off" placeholder="例如 Wong Shen Yuong；不确定可留空 | leave blank if unsure">'
        f'<datalist id="up-lects">{lects}</datalist>'
        '<label for="up-year">学期 | Session</label><div class="up-row">'
        f'<select id="up-year" aria-label="年份 | Year"><option value="">年份 | Year</option>{years}</select>'
        '<select id="up-term" aria-label="学期 | Term"><option value="">学期 | Term</option>'
        '<option value="02">02</option><option value="04">04</option><option value="09">09</option>'
        '<option value="other">其他 | Other</option></select>'
        '<input id="up-other" hidden placeholder="例如 2024/04、Apr 2024 | e.g. 2024/04, Apr 2024" aria-label="其他学期 | Other session"></div>'
        f'<label for="up-type">资料类型 | Type</label><select id="up-type"><option value="">不确定 | Not sure</option>{types}</select>'
        '<div id="up-assess-row" hidden><label for="up-assess">考核名称 | Assessment</label>'
        '<input id="up-assess" placeholder="Final、Midterm、Test 1…"></div></details>'
        '<p id="up-error" class="error" role="alert"></p>'
        '<p><button type="submit" class="btn big">去 GitHub 上传 | Upload on GitHub</button></p></form>'
        f'<script type="application/json" id="up-data">{blob}</script>'
        "<h2>机器人会做什么 | What the bot does</h2><ul>"
        "<li>逐个分拣：认出每个文件是哪门课、哪个学期、什么类型，按规则改名、放好、登记。"
        "<br><small>Sorts each file: works out its course, session and type, then renames, files and registers it.</small></li>"
        "<li>逐页检查：有没有答案和评分标准，有没有个人信息。"
        "<br><small>Checks every page for answers, marking schemes and personal data.</small></li>"
        "<li>PDF 里的学号、姓名、签名等自动涂黑，放进仓库的是涂黑后的副本；Word / PPT 里有个人信息的交给维护者处理。"
        "<br><small>Blacks out student IDs, names, signatures and the like in PDFs, and commits the blacked-out copy; "
        "a Word or PPT file with personal data goes to a maintainer.</small></li>"
        "<li>仓库里已有的同一份文件不重复收；另一套题或新版本自动加后缀。"
        "<br><small>Skips a file the repo already has; another set or a new version gets a suffix.</small></li>"
        "<li>拿不准的留在 PR 里，交给维护者。<br><small>Leaves anything it is unsure about in the PR for a maintainer.</small></li></ul>"
        '<p class="notice"><strong>注意：拖进 GitHub 的附件任何人都能下载</strong>，这一点机器人改不了。文件上有你或同学的学号、名字，'
        "最好先涂掉再传（手机扫描 App、Acrobat 都能涂）；不方便的话发邮件给我们。"
        "<br><strong>Note: anyone can download the files you attach on GitHub</strong>, and the bot cannot change that. "
        "If a file shows your or a classmate's student ID or name, black it out before uploading (a phone scanner app or Acrobat can do it), "
        "or email it to us instead.</p>"
        '<p class="muted">没有 GitHub 账号，或者不想公开原文件？发邮件到 <code>xieqiyi@ieee.org</code>，我们帮你处理后上传并署名。'
        '<br>No GitHub account, or rather not make the original public? Email <code>xieqiyi@ieee.org</code>: we clean it up, upload it and credit you.</p>')
    return page(site, "上传资料 | Upload material", content, "上传试卷、大纲、笔记：拖文件就行，机器人用 AI 分拣、打码、查重 | upload material")


def prog_cards(site: Site) -> str:
    cards = []
    for prog, name in PROGS.items():
        cs = [c for c in site.courses if c.prog == prog]
        n_mat, n_files = sum(1 for c in cs if c.sessions), len(all_files(cs))
        head = f"{PROG_ICONS[prog]} {prog}" if prog != "GE" else f"{PROG_ICONS[prog]} 公共课"
        cards.append(f'<a class="card" href="{site.prefix}{prog}/"><h3>{head}</h3><p>{esc(name)}</p>'
                     f'<p class="stats">{n_mat} / {len(cs)} 门课有资料 · {n_files} 份文件<br>'
                     f'{n_mat} of {len(cs)} courses with material · {n_files} files</p></a>')
    return f'<div class="cards">{"".join(cards)}</div>'


def courses_page(site: Site) -> str:
    content = ("<h1>课程总览 | All courses</h1>"
               "<p>选一个专业看它的课程索引；代码以 G 或 MPU 开头的全校公共课在「公共课」。"
               "<br>Pick a programme for its course index; university-wide courses (codes starting with G or MPU) are under University-wide.</p>"
               + prog_cards(site) +
               f'<p><a href="{site.prefix}wanted/">还缺资料的课 | Courses still wanted</a> · '
               f'<a href="{site.prefix}recent/">最近上传 | Recent uploads</a></p>')
    return page(site, "课程总览 | All courses", content)


def redirect_page(site: Site, target: str) -> str:
    """A page that moved: send the visitor (and search engines) to its new address."""
    url = esc(site.prefix + target)
    return ('<!doctype html><html lang="zh-Hans"><head><meta charset="utf-8">'
            f'{REDIRECT_MARK} content="0; url={url}"><link rel="canonical" href="{esc(SITE_URL + target)}">'
            '<meta name="robots" content="noindex"><title>已移动 | Moved</title></head>'
            f'<body><p>这一页已移到 | This page moved to <a href="{url}">{url}</a></p></body></html>\n')


RATE_DIMS = [  # (key, Chinese, English, label for 1, label for 5)
    ("course", "课程质量", "Course quality", "很差 poor", "很好 great"),
    ("instructor", "老师质量", "Instructor quality", "很差 poor", "很好 great"),
    ("difficulty", "难度", "Difficulty", "很简单 easy", "很难 hard"),
    ("work", "作业量", "Work required", "很少 light", "很多 heavy"),
    ("learned", "学到多少", "Amount learned", "很少 little", "很多 a lot"),
    ("communication", "沟通", "Communication", "很差 poor", "很好 great"),
    ("access", "找得到老师", "Instructor access", "很难找 hard", "很好找 easy"),
    ("recommend", "是否推荐", "Would recommend", "不推荐 no", "强烈推荐 yes"),
]


def rate_page(site: Site) -> str:
    """Search a course and lecturer, then rate on this page: posted to the Worker, no GitHub account (static/rate.js)."""
    def scale(key: str, zh: str, en: str, lo: str, hi: str, optional: bool) -> str:
        opts = "".join(f'<label><input type="radio" name="{key}" value="{n}"><span>{n}</span></label>' for n in range(1, 6))
        skip = f'<label class="skip"><input type="radio" name="{key}" value="" checked><span>不评 Skip</span></label>' if optional else ""
        return (f'<fieldset class="scale"><legend>{zh} | {en}{"" if optional else " *"}</legend>'
                f'<div class="scale-row"><small>1 {esc(lo)}</small>{opts}<small>5 {esc(hi)}</small>{skip}</div></fieldset>')
    years = "".join(f'<option value="{y}">{y}</option>' for y in range(datetime.date.today().year + 1, 2017, -1))
    codes = "".join(f'<option value="{esc(c.code)}">{esc(c.name)}</option>' for c in sorted(site.courses, key=lambda c: c.code))
    main = "".join(scale(*d, optional=False) for d in RATE_DIMS[:4])
    extra = "".join(scale(*d, optional=True) for d in RATE_DIMS[4:])
    content = (
        "<h1>添加评价 | Add a review</h1>"
        "<p>不需要 GitHub 账号。只打分、不写字，匿名登记；每台浏览器对同一门课同一位老师只算一票，再交一次就是改分。每次提交都要过一次人机验证。"
        "<br>No GitHub account needed. Scores only, recorded anonymously; one vote per browser per course and lecturer, and "
        "submitting again changes it. Every submission needs a quick human check.</p>"
        '<form class="lect-find" role="search"><label for="rate-q">先找课程和老师 | Find the course and lecturer</label>'
        '<input id="rate-q" type="search" autocomplete="off" placeholder="例如 COM107、Circuits、Chua | e.g. COM107, Circuits, Chua"></form>'
        '<div id="rate-results" aria-live="polite"><p class="muted">输入后这里会列出匹配的课程和老师。| Matches appear here as you type.</p></div>'
        f'<form id="rate-form" class="rate-form" hidden data-vote="{WORKER_URL}/vote" data-sitekey="{TURNSTILE_SITE_KEY}">'
        '<h2 id="rate-title">打分 | Rate</h2>'
        '<label for="rate-code">课程代码 | Course code *</label><input id="rate-code" list="rate-codes" autocomplete="off" required>'
        f'<datalist id="rate-codes">{codes}</datalist>'
        '<label for="rate-lecturer">老师 | Lecturer *</label>'
        '<input id="rate-lecturer" list="rate-lects" maxlength="80" autocomplete="off" required placeholder="名单里没有的可以直接写 | type a name not on the list">'
        '<datalist id="rate-lects"></datalist>'
        '<label for="rate-year">修课学期 | Session（可不填 | optional）</label><div class="up-row">'
        f'<select id="rate-year" aria-label="年份 | Year"><option value="">年份 | Year</option>{years}</select>'
        '<select id="rate-term" aria-label="学期 | Term"><option value="">学期 | Term</option><option value="02">02</option>'
        '<option value="04">04</option><option value="09">09</option><option value="other">其他 | Other</option></select>'
        '<input id="rate-other" hidden placeholder="YYYY-MM" aria-label="其他学期 | Other session"></div>'
        f"{main}<details class=\"rate-extra\"><summary>四个细分（可跳过）| Four optional extras</summary>{extra}</details>"
        '<label class="confirm"><input type="checkbox" id="rate-confirm"> 我修过这门课，这些分数是我自己的评价 | I took this course; these scores are my own</label>'
        '<div id="rate-verify"></div><p id="rate-error" class="error" role="alert"></p>'
        '<p><button type="submit" class="btn big">提交 | Submit</button></p></form>'
        '<div id="rate-done" class="notice" hidden></div>'
        '<p class="muted">为防刷分：同一网络每天最多 100 票，同一浏览器每天最多 20 票；地址和浏览器只以加密哈希保存，不可还原。'
        "<br>Against vote stuffing: at most 100 votes a day per network and 20 per browser; addresses and browsers are kept only as salted hashes.</p>"
        f'<script src="{site.prefix}static/rate.js" defer></script>')
    return page(site, "添加评价 | Add a review", content, "不用登录，给一门课和老师匿名打分 | rate a course and lecturer anonymously, no login")


def site_pages(site: Site) -> list[str]:
    """Every page's path relative to the site root, for the sitemap."""
    pages = ["", "qa/", "blog/", "courses/", "upload/", "recent/", "wanted/", "reviews/", "rate/", "news/", "opportunities/", *ROOT_PAGES.values(), *(f"{p}/" for p in PROGS),
             *(f"{c.path}/" for c in site.courses), *(f"lecturers/{lecturer_slug(n)}/" for n in lecturers(site))]
    return list(dict.fromkeys(pages))


def sitemap_xml(site: Site) -> str:
    urls = "".join(f"<url><loc>{html_mod.escape(SITE_URL + p, quote=True)}</loc></url>" for p in site_pages(site))
    return ('<?xml version="1.0" encoding="utf-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + urls + "</urlset>\n")


def robots_txt() -> str:
    return f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}sitemap.xml\n"


def rewrite_link(site: Site, href: str, src_md: str) -> str:
    """Turn a relative link inside repo markdown into a site URL or a GitHub URL."""
    if href.startswith(("http://", "https://", "mailto:", "#")):
        return href
    path, _, frag = href.partition("#")
    frag = f"#{frag}" if frag else ""
    p = posixpath.normpath(posixpath.join(posixpath.dirname(src_md), unquote(path))).strip("/")
    if p in ROOT_PAGES:
        return site.prefix + ROOT_PAGES[p] + frag
    if p in LINK_ALIASES:
        page_path, _, section = LINK_ALIASES[p].partition("#")
        return site.prefix + page_path + (frag or f"#{section}")
    parts = p.split("/")
    if len(parts) == 2 and parts[0] in PROGS and parts[1] == "README.md":
        return f"{site.prefix}{parts[0]}/{frag}"
    course = site.by_path.get("/".join(parts[:2]))
    if course:
        rest = parts[2:]
        if not rest or rest == ["README.md"]:
            return f"{site.prefix}{course.path}/{frag}"
        # a session folder, or a type folder inside one, is a section of the course page
        if len(rest) == 1 or (len(rest) == 2 and rest[1] in SUBS):
            if rest[0] in course.sessions:
                return f"{site.prefix}{course.path}/#{rest[0]}"
            if SESSION_RE.fullmatch(rest[0]) or rest[0] == "unknown":
                warn(f"{src_md}: {p} has no indexed material, linking to GitHub instead")
    full = site.root / p
    if full.is_dir():
        return f"{GH}/tree/main/{url_path(p)}"
    if not full.is_file():
        warn(f"{src_md}: link to missing {p}")
    return f"{GH}/blob/main/{url_path(p)}"


def render_md(site: Site, text: str, src_md: str) -> str:
    out = markdown.markdown(text, extensions=["tables", "fenced_code", "toc"],
                            extension_configs={"toc": {"slugify": slugify}})

    def fix(m: re.Match) -> str:
        href = rewrite_link(site, html_mod.unescape(m.group(1)), src_md)
        return f'href="{html_mod.escape(href, quote=True)}"'

    out = re.sub(r'href="([^"]*)"', fix, out)
    # wide bilingual tables scroll sideways on phones instead of breaking the layout
    return out.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")


# ---------------------------------------------------------------- README clean-up for the site (the repo files stay as they are)

SEPARATOR_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
INSTRUCTION_RE = re.compile(r"每个修读学期一行|One row per session")         # the course README template's note to contributors
EMPTY_BULLET_RE = re.compile(r"^\s*[-*]\s*[^：:\[\]]{1,40}[：:]\s*$")        # "- 小贴士 / Tips：" with nothing after it
NOTICE_RE = re.compile(r"^\s*[-*]\s*代码变更")                               # a code-change note belongs at the top of the page
FOLDER_LINK_RE = re.compile(r"^\[[^\]]*\]\((?:\d{4}-\d{2}|unknown)/[^)]*\)$")  # "[Past-Papers](2021-09/Past-Papers/)"


def _md_cells(line: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def _md_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def tidy_tables(md: str, cell_fix=None) -> str:
    """Template rows ([Name] …) dropped, columns empty in every row dropped; cell_fix(header, col, text) edits data cells."""
    lines, out, i = md.split("\n"), [], 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) and SEPARATOR_RE.match(lines[i + 1]):
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            head = _md_cells(lines[i])
            body = [(_md_cells(l) + [""] * len(head))[:len(head)] for l in lines[i + 2:j]]
            if cell_fix:
                body = [[cell_fix(head, k, v) for k, v in enumerate(r)] for r in body]
            body = [r for r in body if "[Name]" not in r and "YYYY" not in r[0]]
            keep = [k for k in range(len(head)) if not body or any(r[k] for r in body)]
            out += [_md_row([head[k] for k in keep]), _md_row(["---"] * len(keep))]
            out += [_md_row([r[k] for k in keep]) for r in body]
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _session_cells(head: list[str], col: int, text: str) -> str:
    """Course session table: the session as plain text, no folder links in the notes (the files section has both)."""
    if not head or not head[0].startswith("学期"):
        return text
    if col == 0:
        return LINK_RE.sub(r"\1", text)
    if col == len(head) - 1:
        return " · ".join(t for t in (x.strip() for x in text.split(" · ")) if t and not FOLDER_LINK_RE.match(t))
    return text


def course_readme_parts(text: str) -> tuple[str, str]:
    """(notices, details) markdown of a course README: code-change notes for the top of the page, the rest for the
    details section, without the title, the template's instruction line, empty bullets and folder links."""
    notices, rest = [], []
    for line in text.replace("\r\n", "\n").split("\n"):
        if re.match(r"^# ", line) or INSTRUCTION_RE.search(line) or EMPTY_BULLET_RE.match(line):
            continue
        (notices if NOTICE_RE.match(line) else rest).append(line)
    return "\n".join(notices), tidy_tables("\n".join(rest), _session_cells).strip()


def _timeline_cells(head: list[str], col: int, text: str) -> str:
    return LINK_RE.sub(r"\1", text) if col == 1 else text   # one link per row: the course name keeps it, the code goes plain


def timeline_html(site: Site, prog: str) -> str:
    """The programme README's course timeline, folded away: contributor headings and tables only, no instructions."""
    rpath = site.root / prog / "README.md"
    if not rpath.is_file():
        return ""
    text = rpath.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(r"^## 修课时间表.*?$(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    if not m:
        return ""
    keep = [l for l in m.group(1).split("\n") if l.startswith("### ") or l.lstrip().startswith("|")]
    md, blocks = [], 0
    for l in keep:   # blank lines around headings and tables so markdown sees them as blocks
        if l.startswith("### ") or (md and not md[-1].lstrip().startswith("|") and l.lstrip().startswith("|")):
            md.append("")
        md.append(l)
        blocks += l.startswith("|")
    if not blocks:
        return ""
    body = render_md(site, tidy_tables("\n".join(md), _timeline_cells), f"{prog}/README.md")
    body = re.sub(r"<h3 id=\"[^\"]*\">", "<h3>", body)
    return ('<details class="timeline"><summary>修课时间表 | Course timeline'
            '<small>每位贡献者实际修读各门课的学期 | the session each contributor took each course</small></summary>'
            f"{body}</details>")


# ---------------------------------------------------------------- pages

HERE = Path(__file__).resolve().parent
esc = html_mod.escape
DEFAULT_DESC = ("厦门大学马来西亚分校 SAIR 学院学生自发整理的开放学习资料库：空白试卷、大纲、作业、实验手册、笔记 | "
                "Student-run open archive of study material for XMUM SAIR: blank papers, syllabi, assignments, lab manuals, notes")
GITHUB_MARK = ('<svg viewBox="0 0 16 16" width="20" height="20" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 '
               '6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 '
               '1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 '
               '.67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 '
               '3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>')
# header blocks in the maintainer's order (2026-11-06): reading first, contributing last. Courses opens a row with the
# programmes and GE (nav-sub) instead of three separate blocks; lecturers live on Reviews, contributors on About.
# (Chinese, English, site path or absolute URL, icon); kept short so the row fits one line at the page width
NAV = [("课程", "Courses", "courses/", "📚"), ("讨论", "Discussion", "qa/", "💬"), ("评价", "Reviews", "reviews/", "📊"),
       ("机会", "Opportunities", "opportunities/", "🧭"), ("关于", "About", "about/", "ℹ️"), ("GitHub", "仓库", GH, GITHUB_MARK),
       ("如何贡献", "Contribute", "contributing/", "🤝"), ("上传", "Upload", "upload/", "📤")]
UPLOAD_URL = f"{GH}/issues/new?template=upload.yml"  # the upload form (intake/DESIGN.md); /upload/ fills it in for the visitor


def template() -> Template:
    return Template((HERE / "templates" / "base.html").read_text(encoding="utf-8"))


def nav_block(i: int, href: str, icon: str, main: str, sub: str, cls: str = "", attrs: str = "") -> str:
    """One header block: icon on the left, two-line label (Chinese over English) on the right."""
    return (f'<a class="nb{(" " + cls) if cls else ""}" href="{href}"{attrs} style="--i:{i}">'
            f'<span class="nb-ic" aria-hidden="true">{icon}</span>'
            f'<span class="nb-t">{esc(main)}<small>{esc(sub)}</small></span></a>')


def nav_html(site: Site) -> str:
    blocks = []
    for i, (main, sub, path, icon) in enumerate(NAV):
        href = path if path.startswith("http") else f"{site.prefix}{path}"
        cls = "upload" if path == "upload/" else ""
        attrs = ' data-menu="nav-courses" aria-controls="nav-courses" aria-expanded="false"' if path == "courses/" else ""
        blocks.append(nav_block(i, href, icon, main, sub, cls, attrs))
    # the Courses block opens this row (site.js); without script the link goes to the overview page
    subs = [nav_block(i, f"{site.prefix}{p}/", PROG_ICONS[p], *((p, PROGS[p].split(" | ")[0]) if p != "GE" else ("公共课", "G 选修 · MPU")))
            for i, p in enumerate(PROGS)]
    subs.append(nav_block(len(subs), f"{site.prefix}courses/", "🗂️", "总览", "All courses"))
    form = (f'<form class="mini-search" action="{site.prefix}" method="get" role="search">'
            '<label class="sr-only" for="nav-q">搜索 | Search</label>'
            '<input id="nav-q" name="q" type="search" placeholder="搜索 | Search"></form>')
    tools = (f'<div class="tools">{form}'
             '<button class="theme-toggle" type="button" aria-label="切换深浅色 | Toggle theme">🌙</button></div>')
    return (f'{tools}<nav class="nav-blocks" aria-label="主导航 | Main navigation">{"".join(blocks)}</nav>'
            f'<div class="nav-sub" id="nav-courses" hidden>{"".join(subs)}</div>')


def assistant_html(site: Site) -> str:
    """The helper's chat window (static/assistant.js), when its service is deployed (assistant/README.md): pages.yml passes
    the repository variables ASSISTANT_URL and TURNSTILE_SITE_KEY. BOTS_PAUSED=1 hides it."""
    url, key = os.environ.get("ASSISTANT_URL", "").strip(), os.environ.get("TURNSTILE_SITE_KEY", "").strip()
    if not (url and key) or os.environ.get("BOTS_PAUSED", "").strip() == "1":
        return ""
    return (f'<div id="helper" data-url="{esc(url)}" data-sitekey="{esc(key)}"></div>'
            f'<script src="{site.prefix}static/assistant.js" defer></script>')


def page(site: Site, title: str, content: str, description: str = "") -> str:
    return template().substitute(title=esc(title), description=esc(description or DEFAULT_DESC),
                                 base=site.prefix, nav=nav_html(site), content=content, assistant=assistant_html(site))


def human_size(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, round(n / 1024))} KB"


def course_table_html(site: Site, courses: list[Course]) -> str:
    """Code, course (the one link), sessions with material, file count."""
    head = ("<thead><tr><th>代码 / Code</th><th>课程 / Course</th><th>资料学期 / Sessions</th>"
            '<th class="num">文件 / Files</th></tr></thead>')
    rows = []
    for c in courses:
        n = len(all_files([c]))
        sessions = " · ".join("未知 unknown" if s == "unknown" else s for s in c.sessions) or "—"
        files = str(n) if n else '<span class="wanted">⬜ 征集中 | wanted</span>'
        rows.append(f'<tr><td>{esc(c.code)}</td><td><a href="{site.prefix}{c.path}/">{esc(c.name)}</a></td>'
                    f'<td class="sessions">{sessions}</td><td class="num">{files}</td></tr>')
    if not rows:
        rows.append('<tr><td colspan="4">暂无课程 | No courses yet</td></tr>')
    return f'<div class="table-wrap"><table class="index">{head}<tbody>{"".join(rows)}</tbody></table></div>'


def file_item(f: File) -> str:
    links = ""
    if f.name.lower().endswith(".pdf"):
        links += f' <a class="btn" href="{GH}/blob/main/{url_path(f.path)}">预览 | Preview</a>'
    links += f' <a class="btn" href="{RAW}/{url_path(f.path)}">下载 | Download</a>'
    return f'<li><span class="fname">{esc(f.name)}</span> <small>{human_size(f.size)}</small>{links}</li>'


def course_page(site: Site, c: Course) -> str:
    """Order: title, course review, files, course details from the README, Q&A."""
    title = f"{c.code} {c.name}"
    src = f"{c.path}/README.md"
    notices, details = course_readme_parts(c.readme) if c.readme else ("", "")
    top = (f"<h1>{esc(title)}</h1>"
           f'<p class="meta"><a class="chip" href="{site.prefix}{c.prog}/">{PROG_ICONS[c.prog]} {c.prog} · {esc(PROGS[c.prog].split(" | ")[0])}</a> '
           f'<a class="chip" href="{GH}/tree/main/{url_path(c.path)}">GitHub 文件夹 | Folder</a></p>')
    if notices:
        top += f'<div class="notice">{render_md(site, notices, src)}</div>'
    parts = ['<h2 id="files">资料 | Files</h2>']
    if not c.sessions:
        parts.append("<p>⬜ 征集中 / wanted</p>")
    for s, types in c.sessions.items():
        label = "未知学期 | unknown" if s == "unknown" else s
        lect = f" · 老师 | Lecturer: {lecturer_links_html(site, c.lecturers[s])}" if s in c.lecturers else ""
        zipb = (f' <a class="btn" href="{DOWNLOADS}/{url_path(c.code)}_{s}.zip" '
                f'title="这个学期的全部文件打成一个 zip | Every file of this session in one zip">打包下载 | Download all</a>')
        parts.append(f'<h3 id="{s}">{label}{lect}{zipb}</h3>')
        for t, fs in types.items():
            parts.append(f'<h4>{t} ({len(fs)})</h4><ul class="files">{"".join(file_item(f) for f in fs)}</ul>')
    lects = sorted({n for cell in c.lecturers.values() for n in split_lecturers(cell)})
    rate = f"{site.prefix}rate/?code={quote(c.code)}" + (f"&lecturer={quote(lects[0])}" if len(lects) == 1 else "")
    review = '<h2 id="review">课程评价 | Course review</h2>' + ratings_html(f'data-code="{esc(c.code)}" data-known="{known_lecturers(site)}"', rate)
    info = ['<h2 id="details">课程信息 | Course details</h2>',
            f'<p class="meta">修课学期 | Taken in: {"、".join(c.taken_in) or "—"}</p>']
    if details:
        info.append(render_md(site, details, src))
    qa = f'<h2 id="qa">讨论 | Discussion</h2><p class="notice">{FORUM_NOTICE}</p>' + forum_html(site, "course", c.code)
    return page(site, title, top + review + "".join(parts) + "".join(info) + qa,
                f"{title}：{PROGS[c.prog]} 课程资料与讨论 | course material and discussion")


def prog_page(site: Site, prog: str) -> str:
    """A generated page, not the README: a line about the programme, the course index, the timeline folded away.
    The README's notes for contributors (how the index is generated, naming rules) stay on GitHub."""
    cs = [c for c in site.courses if c.prog == prog]
    up = f'<a href="{site.prefix}upload/">上传</a>'
    if prog == "GE":
        lead = ("所有专业通用：G 开头是全校选修课，MPU 开头是马来西亚政府规定的必修通识课。谁在哪个学期修了这些课，记在各专业页的修课时间表里。"
                "<br>Shared by every programme: G courses are university electives, MPU courses are Malaysia's compulsory general subjects. "
                "Who took them when is in each programme's course timeline.")
        groups = [("G 开头：全校选修 | G: university electives", [c for c in cs if not c.code.startswith("MPU")]),
                  ("MPU 开头：马来西亚必修通识课 | MPU: Malaysia's compulsory subjects", [c for c in cs if c.code.startswith("MPU")])]
        body = "".join(f"<h2>{t}</h2>{course_table_html(site, g)}" for t, g in groups)
    else:
        zh, en = PROGS[prog].split(" | ")
        lead = (f"{zh}专业课。G 和 MPU 开头的全校公共课在<a href=\"{site.prefix}GE/\">公共课</a>。"
                + ("" if cs else f"这里还没有资料，修过的同学欢迎{up}。")
                + f"<br>{en} courses. University-wide G and MPU courses are under <a href=\"{site.prefix}GE/\">University-wide</a>."
                + ("" if cs else f' Nothing here yet: <a href="{site.prefix}upload/">uploads welcome</a>.'))
        body = '<h2 id="courses">课程 | Courses</h2>' + course_table_html(site, cs) + timeline_html(site, prog)
    newest = [e for e in getattr(site, "recent", []) if e.get("prog") == prog][:5]
    fresh = (f'<section class="fresh"><h2>🆕 最近新增 | Recently added</h2>{recent_html(newest, compact=True)}'
             f'<p class="more"><a href="{site.prefix}recent/">全部最近上传 | All recent uploads</a></p></section>') if newest else ""
    content = (f"<h1>{PROG_ICONS[prog]} {prog} · {esc(PROGS[prog])}</h1><p class=\"lead\">{lead}</p>"
               + fresh + body + f'<p class="meta">有新资料？{up} · <a href="{site.prefix}upload/">Upload</a></p>')
    return page(site, f"{prog} · {PROGS[prog]}", content, f"{prog} {PROGS[prog]} 课程索引 | course index")


def contributors_html(site: Site) -> str:
    """CONTRIBUTORS.md as a section of the About page: headings one level down, template rows and empty columns left out."""
    path = site.root / "CONTRIBUTORS.md"
    if not path.is_file():
        return ""
    text = re.sub(r"^# .*\n", "", path.read_text(encoding="utf-8").replace("\r\n", "\n"), count=1)
    body = render_md(site, tidy_tables(text), "CONTRIBUTORS.md")
    body = re.sub(r"<(/?)h3([ >])", r"<\1h4\2", body)
    body = re.sub(r"<(/?)h2([ >])", r"<\1h3\2", body)
    return f'<h2 id="contributors">贡献者 | Contributors</h2>{body}'


def root_page(site: Site, md: str) -> str:
    text = (site.root / md).read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(r"^# (.+)$", text, flags=re.M)
    if md == "README.md":   # About: the README's pointer to CONTRIBUTORS.md gives way to the list itself
        text, n = re.subn(r"^## 贡献者 \| Contributors\n.*?(?=^## |\Z)", "CONTRIBUTORSTOKEN\n\n", text, count=1, flags=re.M | re.S)
        body = render_md(site, text, md)
        body = body.replace("<p>CONTRIBUTORSTOKEN</p>", contributors_html(site)) if n else body + contributors_html(site)
    else:
        body = render_md(site, text, md)
    if md == "CONTRIBUTING.md":
        body = (f'<p class="notice">最简单的方式：从<a href="{site.prefix}upload/">上传页</a>去 GitHub，选专业、拖文件。机器人用 AI 分拣、改名、打码、查重，开一个 PR 等维护者审核。<br>'
                f'Easiest way: go from the <a href="{site.prefix}upload/">upload page</a> to GitHub, pick your programme and drop your files. '
                'The bot sorts, renames, redacts and de-duplicates them with AI and opens a PR for review.</p>') + body
    return page(site, m.group(1).strip() if m else md, body)


def home_page(site: Site) -> str:
    """Search first; then a one-line changelog note, the programmes, new opportunities and new posts side by side, and the
    two links the header does not carry (wanted list, add a review). Everything else is one click away in the header."""
    files = all_files(site.courses)
    with_mat = sum(1 for c in site.courses if c.sessions)
    stats = (f'<p class="stats home-stats">{with_mat} 门课有资料 · {len(files)} 份文件 · 全部免费 | '
             f'{with_mat} courses with material · {len(files)} files · all free</p>')
    content = (
        '<section class="hero"><h1>XMUM SAIR 生存指南 <span>| Survival Guide</span></h1>'
        "<p>SAIR 三个本科专业的开放资料库：空白试卷、大纲、作业、实验手册、笔记，同学自发整理，免费公开。"
        "<br><span class=\"muted\">An open, student-run archive for XMUM SAIR's three undergraduate programmes: past papers, "
        "syllabi, assignments, lab manuals and notes, all free.</span></p></section>"
        f'<form id="search" action="{site.prefix}" method="get" role="search">'
        '<label class="sr-only" for="q">搜索课程代码、课名、老师、知识点或文件名 | Search by course code, name, lecturer, topic or file name</label>'
        '<input id="q" name="q" type="search" autocomplete="off" placeholder="课程代码、课名、老师、知识点… 如 COM107、傅里叶 | course, lecturer, topic"></form>'
        + stats + '<div id="results" aria-live="polite"></div>'
        + news_strip(site)
        + f'<h2>课程 | Courses</h2>{prog_cards(site)}'
        + f'<p class="chips home-more"><a class="chip" href="{site.prefix}wanted/">📋 还缺资料的课 <small>Wanted</small></a>'
        f'<a class="chip" href="{site.prefix}rate/">⭐ 给上过的课打分 <small>Rate a course</small></a></p>'
        '<div class="home-cols">'
        f'<section><h2>🧭 新机会 <small>New opportunities</small></h2>{board_html(site, preview=True)}'
        f'<p class="more"><a href="{site.prefix}opportunities/">机会墙 | All opportunities →</a></p></section>'
        f'<section><h2>💬 讨论区新帖 <small>Latest posts</small></h2>{forum_html(site, "preview")}'
        f'<p class="more"><a href="{site.prefix}qa/">讨论区 | Discussion →</a></p></section>'
        '</div>')
    content += f'<script src="{site.prefix}static/board.js" defer></script>'
    return page(site, "首页 | Home", content)


# ---------------------------------------------------------------- changelog (site/news.md)

NEWS_HEAD = re.compile(r"^## (v[\w.]+) · (\d{4}-\d{2}-\d{2})\s*$")


def parse_news(text: str) -> list[dict]:
    """site/news.md → [{version, date, items: [(zh, en)]}], newest first as written."""
    out: list[dict] = []
    for line in text.replace("\r\n", "\n").splitlines():
        if m := NEWS_HEAD.match(line):
            out.append({"version": m.group(1), "date": m.group(2), "items": []})
        elif out and line.startswith("- "):
            zh, _, en = line[2:].partition(" | ")
            out[-1]["items"].append((zh.strip(), en.strip()))
    return out


def news(site: Site) -> list[dict]:
    path = HERE / "news.md"
    return parse_news(path.read_text(encoding="utf-8")) if path.is_file() else []


def inline_md(site: Site, text: str) -> str:
    """One line of markdown (links, bold) with the site prefix on root-relative links."""
    html = markdown.markdown(text)
    html = re.sub(r"^<p>|</p>$", "", html.strip())
    return re.sub(r'href="/(?!/)', f'href="{site.prefix}', html)


def news_strip(site: Site) -> str:
    rel = news(site)
    if not rel:
        return ""
    r = rel[0]
    items = "".join(f"<li>{inline_md(site, zh)}</li>" for zh, _ in r["items"][:3])
    return (f'<aside class="news-strip" aria-label="更新 | What\'s new"><p class="news-head">🆕 <b>{esc(r["version"])}</b> '
            f'<time datetime="{r["date"]}">{r["date"]}</time> · <a href="{site.prefix}news/">更新日志 | What\'s new</a></p>'
            f'<ul>{items}</ul></aside>')


def news_page(site: Site) -> str:
    parts = ["<h1>更新日志 | What's new</h1>",
             "<p class=\"lead\">每次上线新功能都记在这里，最新的在最上面。<br>Every release, newest first.</p>"]
    for r in news(site):
        items = "".join(f'<li>{inline_md(site, zh)}<br><small>{inline_md(site, en)}</small></li>' for zh, en in r["items"])
        parts.append(f'<section class="release"><h2>{esc(r["version"])} <small><time datetime="{r["date"]}">{r["date"]}</time></small></h2>'
                     f"<ul>{items}</ul></section>")
    return page(site, "更新日志 | What's new", "".join(parts), "网站每次更新了什么 | what changed in each release")


# ---------------------------------------------------------------- opportunities (Research-Opportunities.md + the Worker's board)

# kind → [(key, Chinese, English, icon)]; static/board.js reads this list from the page, the Worker keeps the same keys
BOARD_CATS = {
    "opp": [("research", "科研", "Research", "🔬"), ("intern", "实习", "Internship", "💼"), ("job", "全职", "Full-time", "🏢"),
            ("referral", "内推码", "Referral", "🎟️"), ("event", "比赛活动", "Events", "🏆"), ("study", "升学奖学金", "Study", "🎓")],
}
# the blog's categories (experience posts moved off the opportunity wall on 2026-11-07); assistant/src/forum.js keeps the
# same keys in BLOG_HINTS and check() compares them
BLOG_CATS = [("research", "科研经历", "Research", "🔬"), ("intern", "实习经历", "Internship", "💼"), ("job", "求职面经", "Job hunting", "🏢"),
             ("study", "升学申请", "Grad school", "🎓"), ("event", "比赛经验", "Competitions", "🏆"), ("life", "校园生活", "Student life", "☕")]
CURATED_CAT = {"科研": "research", "实习": "intern", "全职": "job", "内推": "referral", "比赛活动": "event", "升学": "study"}
OPP_MD = "Research-Opportunities.md"


def _md_section(text: str, heading_start: str) -> str:
    m = re.search(rf"^## {re.escape(heading_start)}.*?\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    return m.group(1) if m else ""


def curated_opportunities(site: Site) -> list[dict]:
    """Rows of the "Curated by maintainers" table as board posts (template rows in [brackets] left out)."""
    path = site.root / OPP_MD
    if not path.is_file():
        return []
    out = []
    for line in _md_section(path.read_text(encoding="utf-8"), "维护者整理").splitlines():
        cells = _cells(line) if line.startswith("|") else []
        if len(cells) < 8 or cells[0].startswith(("[", "更新", "---")) or set(cells[0]) <= set("-: "):
            continue
        updated, cat, title, org, details, contact, deadline, status = cells[:8]
        closed = bool(re.search(r"关闭|closed", status, flags=re.I))
        fields = {k: v for k, v in (("org", org), ("contact", contact), ("deadline", deadline)) if v}
        out.append({"id": f"curated-{len(out)}", "kind": "opp", "cat": CURATED_CAT.get(cat, "research"), "title": title,
                    "body": details, "fields": fields, "author": "维护者 | maintainers", "at": updated, "pinned": True, "closed": closed})
    return out


def research_guide(site: Site) -> str:
    """The maintainers' getting-started guide, folded open under the wall ("" without one)."""
    path = site.root / OPP_MD
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^## (科研入门[^|\n]*?)\s*\|\s*(.*)$", text, flags=re.M)
    sec = _md_section(text, "科研入门")
    if not (m and sec):
        return ""
    body = re.sub(r"^这一篇在网站上.*\n", "", sec, flags=re.M).strip()
    return (f'<details class="board-how guide" open><summary>📌 {esc(m.group(1).strip())} | {esc(m.group(2).strip())}</summary>'
            f"{render_md(site, body, OPP_MD)}</details>")


def board_data(site: Site) -> str:
    data = {"cats": {k: [{"key": a, "zh": b, "en": c, "icon": d} for a, b, c, d in v] for k, v in BOARD_CATS.items()},
            "curated": curated_opportunities(site)}
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def board_html(site: Site, preview: bool = False) -> str:
    """The wall's mount point; static/board.js fills it from the Worker. Without script the curated notes still show."""
    curated = [c for c in curated_opportunities(site) if not c["closed"]]
    fallback = "".join(f'<li><b>{esc(c["title"])}</b><br><small>{esc(c["fields"].get("org", ""))}</small></li>' for c in curated[:3])
    attrs = f'data-board="{WORKER_URL}/board" data-sitekey="{TURNSTILE_SITE_KEY}"' + (' data-preview="3"' if preview else "")
    return (f'<div class="board-app" {attrs}><ul class="board-fallback">{fallback}</ul></div>'
            f'<script type="application/json" class="board-data">{board_data(site)}</script>')


def opportunities_page(site: Site) -> str:
    how = ""
    path = site.root / OPP_MD
    if path.is_file():
        how = render_md(site, _md_section(path.read_text(encoding="utf-8"), "怎么发布"), OPP_MD)
    content = (
        "<h1>🧭 机会 | Opportunities</h1>"
        '<p class="lead">实习、科研、全职、内推码、比赛、升学：同学和老师贴的机会都在这面墙上，点便签看详情。'
        f'经历过的同学，欢迎到<a href="{site.prefix}blog/">博客</a>写一篇经验。'
        "<br><span class=\"muted\">Internships, research, jobs, referral codes, competitions and study, pinned by students and lecturers; "
        f'tap a note for details. Been through one? Write it up on the <a href="{site.prefix}blog/">blog</a>.</span></p>'
        '<p class="board-actions"><button type="button" class="btn big" data-compose="opp">＋ 发布机会 | Post an opportunity</button> '
        f'<a class="btn" href="{site.prefix}blog/?write=1">✍️ 写经验 | Write an experience post</a></p>'
        + board_html(site)
        + research_guide(site)
        + f'<details class="board-how"><summary>怎么发布、怎么审核 | How posting and checking work</summary>{how}</details>'
        f'<script src="{site.prefix}static/board.js" defer></script>')
    return page(site, "机会 | Opportunities", content, "实习、科研、全职、内推码、比赛和升学机会 | internships, research, jobs, referral codes and study")


def forum_html(site: Site, mode: str, course: str = "") -> str:
    """The discussion area's mount point; static/forum.js fills it from the Worker (assistant/src/forum.js). One per page.
    mode: full (the discussion page), blog (the blog), course (one course's threads and posts), preview (home page),
    requests (wanted page)."""
    data = {"boards": [{k: b[k] for k in ("key", "zh", "en", "icon")} for b in BOARDS_CFG["boards"]],
            "cats": [{"key": a, "zh": b, "en": c, "icon": d} for a, b, c, d in BLOG_CATS],
            "courses": {c.code: {"name": c.name, "url": f"{site.prefix}{c.path}/"} for c in site.courses}}
    attrs = (f'data-forum="{WORKER_URL}/forum" data-sitekey="{TURNSTILE_SITE_KEY}" data-mode="{mode}"'
             + (f' data-course="{esc(course)}"' if course else ""))
    fallback = ('<noscript><p class="muted">讨论区和博客需要打开 JavaScript。| The discussion area and the blog need JavaScript.</p></noscript>'
                if mode in ("full", "course", "blog") else "")
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (f'<div class="forum-app" {attrs}>{fallback}</div>'
            f'<script type="application/json" class="forum-data">{blob}</script>'
            f'<script src="{site.prefix}static/forum.js" defer></script>')


FORUM_NOTICE = ("不用登录就能提问和回复，每条先审核再出现。请勿张贴答案、评分标准、他人个人信息或老师的私人联系方式。"
                "<br>No login needed; every post is checked before it appears. Do not post answers, marking schemes, "
                "other people's personal data or lecturers' private contact details.")
FORUM_RULES = ("<ul><li>不用登录，不用 GitHub 账号；昵称可填可不填，不填显示「匿名同学」。"
               "<br>No login and no GitHub account; a nickname is optional.</li>"
               "<li>每条帖子和回复都先审核：关键词表加 AI 初审，没问题的直接出现，拿不准的等维护者看过再出现，不符合规则的会退回并说明原因。"
               "<br>Every thread and reply is checked first (keyword lists and an automatic check): fine ones appear at once, "
               "unsure ones wait for a maintainer, and ones that break the rules come back with a reason.</li>"
               "<li>只谈课程、学习、科研实习和校园生活。不骂人、不人身攻击、不谈政治；不贴答案和评分标准；不写别人的姓名、学号、电话或老师的私人联系方式。"
               "<br>Courses, study, careers and campus life only. No insults, personal attacks or politics; no answers or "
               "marking schemes; no one else's name, student ID, phone number or a lecturer's private contact details.</li>"
               "<li>看到不合适的内容点「举报」：3 个人举报后先隐藏，等维护者处理。自己发的可以在同一个浏览器里「撤回」。"
               "<br>Report anything that breaks the rules: after three reports it is hidden until a maintainer looks. "
               "You can withdraw your own posts from the same browser.</li></ul>")


def qa_page(site: Site) -> str:
    """The discussion area: threads by board, served by the Worker; no GitHub account needed."""
    names = "、".join(b["zh"] for b in BOARDS_CFG["boards"])
    head = ("<h1>讨论区 | Discussion</h1>"
            f"<p>一帖一问，按话题分板块：{names}。提到某门课的帖子也会出现在那门课的页面上。"
            "<br>One question per thread, sorted into boards by topic; a thread about one course also shows on that course's page.</p>")
    rules = f'<details class="board-how"><summary>怎么发言、怎么审核 | How posting and checking work</summary>{FORUM_RULES}</details>'
    archive = ('<h2 id="earlier">旧讨论区 | Earlier discussions</h2>'
               f'<p class="muted">改版前在 GitHub 上的帖子留作只读存档：<a href="{GH}/discussions">GitHub 讨论区</a>。新问题请在上面发帖。'
               f'<br>Posts from before, on GitHub, are kept as a read-only archive: <a href="{GH}/discussions">GitHub Discussions</a>. '
               'Please start new threads above.</p>')
    return page(site, "讨论区 | Discussion", community_tabs(site, "qa") + f'<div class="forum-intro">{head}{rules}</div>' + forum_html(site, "full") + archive,
                "按话题分板块的讨论区，不用登录 | discussion boards, no login")


def community_tabs(site: Site, here: str) -> str:
    """A two-part switch over the discussion area and the blog (both sit under the header's Discussion block), each half
    saying in one line what it is for."""
    parts = [("qa", "💬", "讨论区", "Discussion", "提问、回答、求资料 | Ask, answer, find material"),
             ("blog", "📝", "博客", "Blog", "经验长文，可以放图 | Write-ups with images")]
    links = "".join(f'<a class="sw" href="{site.prefix}{k}/"' + (' aria-current="page"' if k == here else "")
                    + f'><span class="sw-ic" aria-hidden="true">{icon}</span><span class="sw-t"><b>{zh} <small>{en}</small></b>'
                    f'<span class="sw-d">{desc}</span></span></a>' for k, icon, zh, en, desc in parts)
    return f'<nav class="switch" aria-label="社区 | Community">{links}</nav>'


BLOG_RULES = ("<ul><li>写你自己的经历：科研、实习、求职面经、升学申请、比赛、校园生活。可以写公司、实验室、学校的名字和面试题。"
              "<br>Your own experience: research, internships, job hunting and interviews, grad school, competitions, student life. "
              "Naming the company, lab or school and describing interview questions is fine.</li>"
              "<li>格式：<code>## 小标题</code>、<code>**加粗**</code>、<code>- 列表</code>、<code>&gt; 引用</code>、<code>[链接](https://…)</code>，"
              "工具栏里都有按钮，写完可以预览。草稿自动保存在你的浏览器里。"
              "<br>Formatting: headings, bold, lists, quotes and links, all on the toolbar, with a preview. The draft is saved in your browser.</li>"
              "<li>图片：每篇最多 6 张，JPG / PNG / WebP。浏览器会先把图片压到长边 1600 像素、约 400 KB，同时去掉照片里的位置和设备信息；"
              "每个浏览器每天最多传 20 张。"
              "<br>Images: up to 6 per post, JPG, PNG or WebP. Your browser shrinks each to 1600 px and about 400 KB and drops the "
              "photo's location and camera data; at most 20 images per browser per day.</li>"
              "<li>审核：文字和图片都先过关键词表和 AI 初审，拿不准的等维护者看。学生证、成绩单、offer 上的个人信息、考试答案和评分标准、"
              "别人的聊天截图（带名字或号码）都不能放。"
              "<br>Checking: text and images go through the keyword lists and an automatic check first; unsure posts wait for a "
              "maintainer. No student cards, transcripts or offer letters with personal data, no exam answers or marking "
              "schemes, no screenshots of other people's chats with their names or numbers.</li>"
              "<li>评论、举报、撤回和讨论区一样。| Comments, reports and withdrawing work as in the discussion area.</li></ul>")


def blog_page(site: Site) -> str:
    """The blog: students' experience posts with images, served by the Worker like the discussion area."""
    head = ("<h1>博客 | Blog</h1>"
            "<p>同学写的经验：科研、实习、求职面经、升学申请、比赛、校园生活。不用登录就能写，可以放图片，发出前会先审核。"
            "<br>Experience posts by students: research, internships, job hunting, grad school, competitions, student life. "
            "Write without logging in, add images; every post is checked first.</p>")
    rules = f'<details class="board-how"><summary>怎么写、怎么审核 | How writing and checking work</summary>{BLOG_RULES}</details>'
    return page(site, "博客 | Blog", community_tabs(site, "blog") + f'<div class="forum-intro">{head}{rules}</div>' + forum_html(site, "blog"),
                "同学写的科研、实习、求职、升学和比赛经验 | students' write-ups of research, internships, job hunting, grad school and competitions")


def not_found_page(site: Site) -> str:
    content = ('<h1>找不到页面 | Page not found</h1><p>这个地址没有内容。课程文件夹可能改名了，试试搜索。</p>'
               f'<p>Nothing lives at this address; the course folder may have been renamed. Try the <a href="{site.prefix}">search on the home page</a>.</p>')
    return page(site, "找不到页面 | Not found", content)


def search_index(site: Site) -> dict:
    courses = [{"prog": c.prog, "code": c.code, "name": c.name, "url": f"{site.prefix}{c.path}/",
                "lecturers": sorted(set(c.lecturers.values())), "sessions": list(c.sessions),
                "files": len(all_files([c]))} for c in site.courses]
    people = [{"name": n, "slug": lecturer_slug(n), "codes": sorted({code for code, _ in taught})}
              for n, taught in lecturers(site).items()]
    files = [{"prog": f.prog, "code": f.code, "course": f.course, "session": f.session, "type": f.type,
              "name": f.name, "path": f.path,
              "preview": f"{GH}/blob/main/{url_path(f.path)}" if f.name.lower().endswith(".pdf") else None,
              "download": f"{RAW}/{url_path(f.path)}",
              **({"topics": site.topics[f.path]["topics"], "about": site.topics[f.path].get("about", "")}
                 if f.path in site.topics else {})} for f in all_files(site.courses)]
    return {"courses": courses, "lecturers": people, "files": files}


ASSET_RE = re.compile(r'(static/([\w.-]+\.(?:css|js)))"')


def asset_version(name: str, _cache: dict = {}) -> str:
    """The first 8 hex characters of a static file's sha256: a new file, a new address."""
    if name not in _cache:
        path = HERE / "static" / name
        _cache[name] = hashlib.sha256(path.read_bytes()).hexdigest()[:8] if path.is_file() else ""
    return _cache[name]


def versioned_assets(html: str) -> str:
    """static/x.css → static/x.css?v=<hash>: GitHub Pages lets browsers keep static files 10 minutes, so without this a
    release could show new pages with the old stylesheet and scripts."""
    return ASSET_RE.sub(lambda m: f'{m.group(1)}?v={asset_version(m.group(2))}"' if asset_version(m.group(2)) else m.group(0), html)


def build(root: Path, out: Path, prefix: str, topics: dict | None = None) -> Site:
    site = Site(root, prefix)
    site.topics = topics or {}
    out = Path(out)
    if out.exists():
        if not (out / "search.json").is_file():   # only wipe a directory a previous build wrote
            raise SystemExit(f"refusing to delete {out}: not a previous build output")
        shutil.rmtree(out)

    def write(rel: str, text: str) -> None:
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".html"):   # the template's canonical placeholder becomes this page's public address
            text = text.replace("__CANONICAL__", SITE_URL + (rel[:-len("index.html")] if rel.endswith("index.html") else rel))
            text = versioned_assets(text)
        p.write_text(text, encoding="utf-8")

    site.recent = recent_entries(site)

    write("index.html", home_page(site))
    write("404.html", not_found_page(site))
    write("qa/index.html", qa_page(site))
    write("blog/index.html", blog_page(site))
    write("courses/index.html", courses_page(site))
    write("upload/index.html", upload_page(site))
    write("recent/index.html", recent_page(site))
    write("reviews/index.html", reviews_page(site))
    write("rate/index.html", rate_page(site))
    write("wanted/index.html", wanted_page(site))
    write("news/index.html", news_page(site))
    write("opportunities/index.html", opportunities_page(site))
    for name, taught in lecturers(site).items():
        write(f"lecturers/{lecturer_slug(name)}/index.html", lecturer_page(site, name, taught))
    for md, rel in ROOT_PAGES.items():
        if (site.root / md).is_file():
            write(rel + "index.html", root_page(site, md))
        else:
            warn(f"{md} missing, page {rel} skipped")
    for prog in PROGS:
        write(f"{prog}/index.html", prog_page(site, prog))
    for c in site.courses:
        write(f"{c.path}/index.html", course_page(site, c))
        if c.prog in LEGACY_DIR and not (out / LEGACY_DIR[c.prog] / c.dir / "index.html").exists():
            write(f"{LEGACY_DIR[c.prog]}/{c.dir}/index.html", redirect_page(site, f"{c.path}/"))
    for old, new in REDIRECTS.items():
        write(f"{old}index.html", redirect_page(site, new))
    write("search.json", json.dumps(search_index(site), ensure_ascii=False))
    write("feed.xml", feed_xml(site, site.recent))
    write("sitemap.xml", sitemap_xml(site))
    write("robots.txt", robots_txt())
    shutil.copytree(HERE / "static", out / "static")
    return site


# ---------------------------------------------------------------- self-check

FORBIDDEN = ("Co-Authored", "Claude", "Anthropic")


def stragglers(site: Site) -> list[str]:
    """Tracked files under the programme folders that the scan did not pick up (wrong depth or name)."""
    try:
        raw = subprocess.run(["git", "ls-files", "-z", *PROGS], cwd=site.root, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):  # no git, or the root is not a checkout (intake dry run)
        warn("git unavailable, straggler check skipped")
        return []
    tracked = [p for p in raw.decode("utf-8").split("\0") if p]
    seen = {f.path for f in all_files(site.courses)}
    return [p for p in tracked if p not in seen and posixpath.basename(p) != "README.md"]


def check_generator_sources() -> list[str]:
    """Forbidden words in the generator's own files. Repo content is not scanned: people and papers may carry any name."""
    hits = []
    for p in sorted(HERE.rglob("*")):
        if p.suffix not in (".py", ".html", ".css", ".js") or "__pycache__" in p.parts or p.name.startswith("test_"):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "FORBIDDEN" in line:
                continue
            hits += [f"{p.relative_to(HERE).as_posix()}:{i}: {w}" for w in FORBIDDEN if w in line]
    return hits


def forum_board_keys(path: Path = HERE.parent / "assistant" / "src" / "forum.js", name: str = "BOARD_HINTS") -> list[str]:
    """The keys of one of the Worker's key lists (BOARD_HINTS, BLOG_HINTS), in order."""
    block = re.search(rf"export const {name} = \{{(.*?)\n\}};", path.read_text(encoding="utf-8"), re.S)
    return re.findall(r"^\s+(\w+):", block.group(1), re.M) if block else []


def check(site: Site, out: Path) -> None:
    """Assert the generated site is consistent; content problems only warn."""
    out = Path(out)
    pages = [p for p in out.glob("*/*_*/index.html") if REDIRECT_MARK not in p.read_text(encoding="utf-8")]
    assert len(pages) == len(site.courses), f"{len(pages)} course pages for {len(site.courses)} courses"
    idx = json.loads((out / "search.json").read_text(encoding="utf-8"))
    assert len(idx["files"]) == len(all_files(site.courses)), "search.json file count differs from the scan"
    for f in idx["files"]:
        assert (site.root / f["path"]).is_file(), f"search.json path missing: {f['path']}"
    bad = check_generator_sources()
    assert not bad, f"generator sources contain forbidden words: {bad}"
    worker_boards = forum_board_keys()
    assert worker_boards == [b["key"] for b in BOARDS_CFG["boards"]], \
        f"assistant/src/forum.js boards {worker_boards} differ from discussions/boards.json"
    worker_cats = forum_board_keys(name="BLOG_HINTS")
    assert worker_cats == [k for k, *_ in BLOG_CATS], f"assistant/src/forum.js blog categories {worker_cats} differ from BLOG_CATS"
    for page_file in out.rglob("*.html"):
        text = page_file.read_text(encoding="utf-8")
        for href in re.findall(r'href="([^"]*)"', text):
            href = html_mod.unescape(href)
            if not href.startswith(site.prefix):
                continue
            rel = href[len(site.prefix):].split("#")[0].split("?")[0]
            target = out / rel / "index.html" if rel == "" or rel.endswith("/") else out / rel
            assert target.is_file(), f"{page_file.relative_to(out)}: dead internal link {href}"
    for p in stragglers(site):
        warn(f"not indexed: {p}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="_site")
    ap.add_argument("--prefix", default="/", help="URL path the site is served under")
    ap.add_argument("--root", default=str(HERE.parent), help="repository tree to build from")
    ap.add_argument("--topics", help="the topic index from topics/topics.py (default: <root>/_data/topics.json)")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    tpath = Path(args.topics) if args.topics else root / "_data" / "topics.json"
    topics = json.loads(tpath.read_text(encoding="utf-8")) if tpath.is_file() else {}
    site = build(root, Path(args.out), args.prefix, topics)
    try:
        check(site, Path(args.out))
    except AssertionError as e:
        print(f"ERROR: self-check failed: {e}", file=sys.stderr)
        return 1
    print(f"{len(site.courses)} courses, {len(all_files(site.courses))} files -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
