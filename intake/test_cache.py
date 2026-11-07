"""Checks for intake/cache.py. Run from the repo root: python intake/test_cache.py (no network, no SDK needed)."""
import base64, copy, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import cache
import claude_check as cc


class FakeStore:
    """Stands in for the GitHub contents API on the bot-state branch: many paths, with PUT and DELETE."""

    def __init__(self, branch=False, conflicts=0):
        self.branch, self.conflicts, self.files, self.calls = branch, conflicts, {}, []   # path -> (data, sha)
        self.on_conflict = None

    @property
    def paths(self):
        return sorted(self.files)

    def gh(self, *args, input=None):
        self.calls.append(args)
        url = next(a for a in args if a.startswith("repos/"))
        if "/git/" in url:
            if url.endswith("/git/refs"):
                self.branch = True
            return json.dumps({"sha": "abc"})
        path = url.split("/contents/", 1)[1].split("?")[0]
        method = args[args.index("-X") + 1] if "-X" in args else "GET"
        if method == "GET":
            if not self.branch or path not in self.files:
                raise RuntimeError("gh: Not Found (HTTP 404)")
            data, sha = self.files[path]
            return json.dumps({"content": base64.b64encode(json.dumps(data).encode()).decode(), "sha": sha})
        if not self.branch:
            raise RuntimeError("gh: Branch bot-state not found (HTTP 404)")
        body = json.loads(input)
        if method == "DELETE":
            if path not in self.files:
                raise RuntimeError("gh: Not Found (HTTP 404)")
            if body.get("sha") != self.files[path][1]:
                raise RuntimeError("gh: sha mismatch (HTTP 409)")
            del self.files[path]
            return "{}"
        if self.conflicts:
            self.conflicts -= 1
            if self.on_conflict:
                self.on_conflict(self)
            raise RuntimeError("gh: is at x but expected y (HTTP 409)")
        cur = self.files.get(path)
        if body.get("sha") != (cur[1] if cur else None):
            raise RuntimeError("gh: sha mismatch (HTTP 409)")
        self.files[path] = (json.loads(base64.b64decode(body["content"])), (cur[1] if cur else "") + "1")
        return "{}"


def other_writer(store):
    path = "intake-cache/issue-7.json"
    data, sha = store.files[path]
    data = dict(data, overrides=dict(data.get("overrides", {}), theirs={"keep": True}))
    store.files[path] = (data, sha + "x")


def test_scrub_keeps_positions_and_drops_personal_text():
    data = {"check": {"abc": {"summary": "Cover shows EEE2109300", "notes": "", "redactions": [{"page": 1, "kind": "text", "text": "EEE2109300", "rects": [[1, 2, 3, 4]]}]}}}
    clean = cache.scrub(data)
    assert "EEE2109300" not in json.dumps(clean) and clean["check"]["abc"]["redactions"] == [{"page": 1, "kind": "text", "rects": [[1, 2, 3, 4]]}]


def test_scrub_blanks_pii_notes_everywhere_and_does_not_mutate():
    data = {"sort": {"s1": {"note": "mail me at a.b@gmail.com", "label": "Final"}},
            "check": {"s1": {"summary": "fine exam", "notes": "student id on page 2",
                             "redactions": [{"page": 2, "kind": "box", "box": [1, 2, 3, 4], "what": "student_id", "text": "ABC1234567"}]}},
            "verify": {"s1": {"notes": ["x"], "summary": "call 13812345678"}}, "spent_usd": 0.5, "extra": {"k": [1]}}
    before = copy.deepcopy(data)
    clean = cache.scrub(data)
    assert data == before
    assert clean["sort"]["s1"] == {"note": "", "label": "Final"}
    assert clean["check"]["s1"]["summary"] == "fine exam" and clean["check"]["s1"]["notes"] == ""
    assert clean["check"]["s1"]["redactions"] == [{"page": 2, "kind": "box", "box": [1, 2, 3, 4], "what": "student_id"}]
    assert clean["verify"]["s1"]["notes"] == ["x"] and clean["verify"]["s1"]["summary"] == ""
    assert clean["spent_usd"] == 0.5 and clean["extra"] == {"k": [1]}
    clean["extra"]["k"].append(2)
    assert data["extra"]["k"] == [1]


def test_scrub_redaction_label_and_nested_notes():
    data = {"check": {"s": {"redactions": [{"page": 1, "kind": "text", "what": "student ID EEE2109300"},
                                           {"page": 2, "kind": "text", "what": "student_id"},
                                           {"page": 3, "kind": "text", "what": "x" * 60}],
                            "notes": {"p1": "email a@gmail.com", "p2": "fine", "deep": [{"z": "call 13812345678"}, "ok"]}},
                      "t": {"summary": ["mail b@gmail.com", "plain"]}}}
    before = copy.deepcopy(data)
    c = cache.scrub(data)
    assert data == before
    assert [r["what"] for r in c["check"]["s"]["redactions"]] == ["", "student_id", ""]
    assert c["check"]["s"]["notes"] == {"p1": "", "p2": "fine", "deep": [{"z": ""}, "ok"]}
    assert c["check"]["t"]["summary"] == ["", "plain"]


def test_scrub_blanks_personal_lecturers_and_weighting():
    data = {"check": {"s": {"lecturers": ["Dr Tan", "tan@gmail.com", "ABC2109123"], "weighting": "ABC2109123 Final 60%"},
                      "t": {"lecturers": ["Dr Lim"], "weighting": "Final 50%, Midterm 50%"}}}
    c = cache.scrub(data)
    assert c["check"]["s"] == {"lecturers": ["Dr Tan", "", ""], "weighting": ""}
    assert c["check"]["t"] == {"lecturers": ["Dr Lim"], "weighting": "Final 50%, Midterm 50%"}
    assert "gmail" not in json.dumps(c) and "ABC2109123" not in json.dumps(c)


def test_redaction_kinds_are_a_closed_list_and_free_text_is_filtered_by_both():
    # final review, fold-in 8: `what` is one of the check's kinds; anything else (free text, a value, not a string) is ""
    labels = ["name", "student_id", "signature", "photo", "qr_code", "phone", "email", "address", "key", "other"]
    values = ["学号 2109300123", "身份证 990101-14-5678", "电话 012-345 6789", "ID EEE2109300", "mail a@gmail.com",
              "学号 | student ID", "Student_ID", "", 3, None, ["name"], {"x": 1}]
    data = {"check": {"s": {"redactions": [{"page": 1, "kind": "box", "what": w} for w in labels + values],
                            "summary": "身份证 990101-14-5678", "notes": "电话 012-345 6789",
                            "weighting": "学号 2109300123 Final 60%", "lecturers": ["Dr Tan", "Dr 2109300123"]},
                      "t": {"summary": "student id on page 2", "notes": "Final 60% | page 3"}}}
    c = cache.scrub(data)["check"]
    assert [r["what"] for r in c["s"]["redactions"]] == labels + [""] * len(values)
    assert c["s"]["summary"] == c["s"]["notes"] == c["s"]["weighting"] == "" and c["s"]["lecturers"] == ["Dr Tan", ""]
    assert c["t"] == {"summary": "", "notes": "Final 60% | page 3"}             # keywords still blank free text


def test_scrub_blanks_personal_names_and_folders_of_the_sort():                # final review, finding 2
    data = {"sort": {"s": {"label": "Assignment1_EEE2109300", "assessment": "Final", "set": "a.b@gmail.com",
                           "course_name": "Circuits EEE2109300", "course_code": "COM107", "keep": True},
                     "t": {"label": "Final", "assessment": "Midterm", "set": "B", "course_name": "Circuits and Devices"}}}
    c = cache.scrub(data)["sort"]
    assert c["s"] == {"label": "", "assessment": "Final", "set": "", "course_name": "", "course_code": "COM107", "keep": True}
    assert c["t"] == data["sort"]["t"] and "2109300" not in json.dumps(c) and "gmail" not in json.dumps(c)


PATH = "intake-cache/issue-7.json"


def interleaved(store, other):
    """store.gh, except that `other(store.gh)` runs just before the first PUT goes through: a second writer that slips
    in between this writer's read and its write, so that write carries a stale sha and the store answers 409."""
    state = {"done": False}

    def gh(*args, input=None):
        if not state["done"] and "-X" in args and args[args.index("-X") + 1] == "PUT":
            state["done"] = True
            other(store.gh)
        return store.gh(*args, input=input)
    return gh


def test_cache_round_trip_on_bot_state():
    store = FakeStore(branch=True); c = cache.IntakeCache("o/r", 7, gh=store.gh)
    c.save({"items": [{"k": 1, "sha": "s", "source": "a.pdf"}]})
    assert c.update_overrides(lambda data: {"s": {"skip": True}}) == {"s": {"skip": True}}
    assert c.load() == {"items": [{"k": 1, "sha": "s", "source": "a.pdf"}], "overrides": {"s": {"skip": True}}}
    assert store.paths == [PATH]
    c.save({"overrides": {"s": {"keep": True}}, "x": 1}, keep_stored_overrides=False)     # the whole file, as given
    assert c.load() == {"overrides": {"s": {"keep": True}}, "x": 1}


def test_load_of_a_missing_cache_is_empty():
    assert cache.IntakeCache("o/r", 7, gh=FakeStore(branch=True).gh).load() == {}


def test_save_scrubs_what_it_writes():
    store = FakeStore(branch=True)
    cache.IntakeCache("o/r", 7, gh=store.gh).save({"check": {"s": {"redactions": [{"page": 1, "kind": "text", "text": "EEE2109300", "rects": []}]}}})
    assert "EEE2109300" not in json.dumps(store.files)
    c = cache.IntakeCache("o/r", 7, gh=store.gh)
    c.update_overrides(lambda data: {"s": {"skip": True, "note": "mail a@gmail.com"}})
    assert "gmail" not in json.dumps(store.files)


def test_a_run_save_keeps_the_overrides_a_command_stored_meanwhile():
    """A rebuild loads the cache, works for minutes, then saves: a command stored in between must survive."""
    store = FakeStore(branch=True)
    run, cmd = cache.IntakeCache("o/r", 7, gh=store.gh), cache.IntakeCache("o/r", 7, gh=store.gh)
    run.save({"items": [{"k": 2, "sha": "s2", "source": "b.pdf"}]})
    snapshot = run.load()                                               # the run starts: no overrides yet
    cmd.update_overrides(lambda data: {**data.get("overrides", {}), "s2": {"skip": True}})
    snapshot["check"] = {"s2": {"answers": "no"}}
    snapshot["overrides"] = {"s2": {"keep": True}}                      # whatever the run holds is ignored
    run.save(snapshot)
    data = store.files[PATH][0]
    assert data["overrides"] == {"s2": {"skip": True}} and data["check"] == {"s2": {"answers": "no"}}


def test_a_run_save_that_conflicts_reads_the_overrides_again():
    store = FakeStore(branch=True)
    cache.IntakeCache("o/r", 7, gh=store.gh).save({"items": [], "sort": {"a": 1}})
    command = lambda gh: cache.IntakeCache("o/r", 7, gh=gh).update_overrides(lambda data: {"s": {"approved_by": "m"}})
    run = cache.IntakeCache("o/r", 7, gh=interleaved(store, command))
    run.save({"sort": {"a": 1, "b": 2}, "spent_usd": 0.5})
    data = store.files[PATH][0]
    assert data == {"items": [], "sort": {"a": 1, "b": 2}, "spent_usd": 0.5, "overrides": {"s": {"approved_by": "m"}}}


def test_a_command_never_loses_results_a_run_saved_meanwhile():
    """The other direction: a command reads, a run saves paid verdicts, the command writes. Its write conflicts, so
    it reads again and applies its commands again; the verdicts stay."""
    store = FakeStore(branch=True)
    cache.IntakeCache("o/r", 7, gh=store.gh).save({"items": [{"k": 1, "sha": "s1", "source": "a.pdf"}]})
    run = lambda gh: cache.IntakeCache("o/r", 7, gh=gh).save({"items": [{"k": 1, "sha": "s1", "source": "a.pdf"}],
                                                             "check": {"s1": {"answers": "no"}}, "spent_usd": 2})
    seen = []

    def change(data):
        seen.append(dict(data))
        return {**data.get("overrides", {}), "s1": {"skip": True}}
    cache.IntakeCache("o/r", 7, gh=interleaved(store, run)).update_overrides(change)
    data = store.files[PATH][0]
    assert len(seen) == 2 and "check" not in seen[0] and seen[1]["check"] == {"s1": {"answers": "no"}}
    assert data["check"] == {"s1": {"answers": "no"}} and data["spent_usd"] == 2 and data["overrides"] == {"s1": {"skip": True}}


def test_nothing_to_change_writes_nothing():
    store = FakeStore(branch=True)
    c = cache.IntakeCache("o/r", 7, gh=store.gh)
    assert c.update_overrides(lambda data: None) is None and store.paths == []


def test_writers_retry_up_to_five_times_then_give_up():
    for writer in (lambda c: c.save({"x": 1}), lambda c: c.update_overrides(lambda data: {"s": {"skip": True}})):
        store = FakeStore(branch=True)
        c = cache.IntakeCache("o/r", 7, gh=store.gh)
        store.conflicts = 4
        writer(c)
        assert store.paths == [PATH]
        store.conflicts = 5
        try:
            writer(c)
        except cc.Conflict:
            continue
        raise AssertionError("expected Conflict")


def test_delete_removes_the_file_and_a_missing_file_is_fine():
    store = FakeStore(branch=True)
    c = cache.IntakeCache("o/r", 7, gh=store.gh)
    c.delete()                                    # nothing there
    c.save({"items": []})
    c.delete()
    assert store.paths == [] and c.load() == {}
    cache.IntakeCache("o/r", 7, gh=FakeStore().gh).delete()    # no bot-state branch at all


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"ok {len(tests)} tests")
