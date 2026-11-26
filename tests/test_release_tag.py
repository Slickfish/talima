"""Amendment 11 (3), the operator, 24 November: the release's tag is trimmed, and a tag that is not a release tag or
does not exist is refused before anything is built.

THE SLIP. On 24 November the release was dispatched with "v1.0.0 ", a trailing space. The workflow read the input
as given. The pattern is the operator's, `^v\\d+\\.\\d+\\.\\d+(-[a-z0-9]+)?$`, written for bash (whose regular
expressions have no `\\d`) with each class spelled out character by character, so that it names its characters
itself and consults no locale's ranges. The trimming names its characters too: the six ASCII white-space
characters, so a space from outside ASCII is not trimmed and the pattern refuses it.

WHAT IS HELD HERE
    T1  release.yml's shape. A job `tag` needs nothing, runs no build (its steps are a checkout and the check), may
        only read the repository, and hands on two outputs, the tag and whether it is a dry run, from its check
        step. That step reads the dispatch's input through its environment (TAG_INPUT), never written into its
        script. Every other job needs `tag`, takes RELEASE_TAG (and DRYRUN, where it has one) from its outputs,
        and checks out that tag by its full name (`refs/tags/...`, so a branch of the same name cannot stand in
        for it). The input, and the ref's name, are read in that one place; the workflow has no environment of
        its own. The release is created with `gh release create --verify-tag`, which refuses to make the tag if it
        is gone by then.
    T2  The check step's own text, read from the file and run by bash as a runner starts it (`--noprofile --norc
        -eo pipefail`), under the C locale and under C.UTF-8 (a runner's) where this machine has it, against a
        repository of its own whose origin also holds tags that only the pattern refuses (V2.0.0, v1.0, nightly
        and others; no two of its names differ only in case) and two that git's pattern matches under a longer
        name, with a tag input planted each time:
          accepted, the input trimmed: the slip "v1.0.0 ", white space at both ends, and the dry-run tag with a
            trailing space, which is a dry run (the outputs are the tag trimmed and the flag);
          refused as not a release tag, each named and nothing handed on: empty, white space alone, no `v`, an
            upper-case `V`, two or four numbers, an empty suffix, an upper-case or underscored suffix, a space or a
            line end inside, a ref's full name, a letter outside ASCII, a full-width digit, spaces from outside
            ASCII at either end (an ideographic space, an em space, a no-break space: the refusal shows them as
            the escapes of their bytes), and two commands that would run if the input reached a shell as code
            (each leaves no file behind); each of these that the origin holds is refused by the pattern alone;
          refused as not existing, each named and nothing handed on: a well-formed tag the repository does not
            hold, one it holds only under a longer name (`x/refs/tags/v9.9.9`, which git's own pattern matches),
            one it holds only under a name that begins with it (`v6.6.6-x/refs/tags/v6.6.6`), a branch of that
            name with no tag, and a tag that only begins like the one asked for;
          refused when the repository's tags cannot be read, named as that and not as a missing tag.
    T3  Companions to T1, each planting what its checks do not name: a build job that does not wait for `tag`; a
        dry-run flag read from the raw input; a checkout of the raw input, and of the bare tag name; a build step
        in the tag job; an output that is not the check step's; an environment for the whole workflow; the tag
        job given write access; the tag job waiting for another; its commands run by another shell; a second
        name in the check's environment; the input written into the check's script; a job's RELEASE_TAG read
        from the input; the ref's name read in a step's name; a release created without `--verify-tag`.

WHAT IS NOT HELD HERE
    GitHub's side: what a runner gives the step (it is run here by this machine's bash and git, against a local
    repository standing in for the remote), and whether an output is handed on. That a locale's ranges would
    widen a pattern written with them: the two locales run here do not. Windows runs no bash here: T2 is skipped
    there, and T1 and T3 run.
"""
from __future__ import annotations

import copy
import os
import shutil
import subprocess
import sys

import pytest
import yaml

from tests import _proc
from tests._tracked import REPO

WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
TAG_INPUT = "${{ inputs.tag }}"
OUTPUTS = {"tag": "${{ steps.tag.outputs.tag }}", "dryrun": "${{ steps.tag.outputs.dryrun }}"}
FROM_THE_TAG_JOB = {"RELEASE_TAG": "${{ needs.tag.outputs.tag }}", "DRYRUN": "${{ needs.tag.outputs.dryrun }}"}
BASH = {"run": {"shell": "bash"}}
AS_A_RUNNER_STARTS_BASH = ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c"]
READS_THE_INPUT = ("inputs", "github.ref_name", "github.event.")


def load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def needs_of(job: dict) -> list:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def strings(node, path=()):
    """Every string of a parsed workflow, key or value, with where it stands."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield path + (str(key),), str(key)
            yield from strings(value, path + (str(key),))
    elif isinstance(node, list):
        for n, value in enumerate(node):
            yield from strings(value, path + (str(n),))
    elif isinstance(node, str):
        yield path, node


def check_step(wf: dict) -> dict:
    steps = ((wf.get("jobs") or {}).get("tag") or {}).get("steps") or []
    return next((s for s in steps if s.get("id") == "tag"), {})


def shape_problems(wf: dict) -> list:
    """Every way release.yml is not T1's shape; empty when it is."""
    jobs, problems = wf.get("jobs") or {}, []
    tag = jobs.get("tag")
    if not isinstance(tag, dict):
        return ["there is no job `tag`"]
    if needs_of(tag):
        problems.append(f"the tag job waits for {needs_of(tag)}: it must run first")
    if tag.get("permissions") != {"contents": "read"}:
        problems.append(f"the tag job's permissions are {tag.get('permissions')}, not contents read alone")
    if tag.get("outputs") != OUTPUTS:
        problems.append(f"the tag job's outputs are {tag.get('outputs')}, not the check step's tag and dry-run flag")
    if tag.get("defaults") != BASH:
        problems.append("the tag job's commands are not run by bash")
    steps = tag.get("steps") or []
    step = check_step(wf)
    if steps != [{"uses": "actions/checkout@v4"}, step] or sorted(step) != ["env", "id", "name", "run"]:
        problems.append(f"the tag job's steps are not a checkout and the check, and nothing else: "
                        f"{[s.get('name') or s.get('uses') for s in steps]}")
    if step.get("env") != {"TAG_INPUT": TAG_INPUT}:
        problems.append(f"the check reads {step.get('env')}, not the dispatch's input alone through TAG_INPUT")
    if TAG_INPUT in str(step.get("run")):
        problems.append("the check writes the input into its script")
    for name, job in jobs.items():
        if name == "tag":
            continue
        if "tag" not in needs_of(job):
            problems.append(f"{name}: does not wait for the tag job, so it can build before the tag is checked")
        env = job.get("env") or {}
        if env.get("RELEASE_TAG") != FROM_THE_TAG_JOB["RELEASE_TAG"]:
            problems.append(f"{name}: RELEASE_TAG is {env.get('RELEASE_TAG')!r}, not the tag job's tag")
        if "DRYRUN" in env and env["DRYRUN"] != FROM_THE_TAG_JOB["DRYRUN"]:
            problems.append(f"{name}: DRYRUN is {env['DRYRUN']!r}, not the tag job's flag")
        for s in job.get("steps") or []:
            if str(s.get("uses", "")).split("@")[0] == "actions/checkout" \
                    and (s.get("with") or {}).get("ref") != "refs/tags/${{ env.RELEASE_TAG }}":
                problems.append(f"{name}: checks out {(s.get('with') or {}).get('ref')!r}, not the checked tag")
    readers = [path for path, text in strings({k: v for k, v in wf.items() if k not in ("on", True)})
               if any(word in text for word in READS_THE_INPUT)]
    if readers != [("jobs", "tag", "steps", "1", "env", "TAG_INPUT")]:
        problems.append(f"the input or the ref's name is read at {readers}, not by the check alone")
    if wf.get("env"):
        problems.append(f"the workflow has an environment of its own: {sorted(wf['env'])}")
    creating = [line for step in (jobs.get("release") or {}).get("steps") or []
                for line in str(step.get("run") or "").splitlines() if line.split()[:3] == ["gh", "release", "create"]]
    if len(creating) != 1 or "--verify-tag" not in creating[0].split():
        problems.append(f"the release is not created for the checked tag alone (gh release create --verify-tag, which "
                        f"refuses to make a tag that is gone): {creating}")
    return problems


def test_t1_every_build_waits_for_the_tag_job_and_reads_the_tag_from_it():
    wf = load()
    assert len(wf["jobs"]) >= 6, sorted(wf["jobs"])
    assert shape_problems(wf) == []


# --- T2: the check's own text, run with planted tags ---------------------------------------------------------------

NO_SHELL = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None or shutil.which("git") is None,
                              reason="the check is bash's and git's; no shell is assumed on Windows, where T1 holds "
                                     "the step's shape")


def git(*args, cwd, env):
    done = _proc.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                     errors="replace", timeout=60)
    assert done.returncode == 0, (args, done.stderr)
    return done.stdout


# Tags the origin holds that the pattern alone must refuse, and one held under a longer name
# (no two names the origin holds differ only in case: a case-insensitive filesystem, macOS's, keeps them as one file)
HELD_NOT_RELEASE = ["V2.0.0", "v1.0", "nightly", "v3.0.0-RC1", "v1.0.0-dry_run", "v1.0.0.0"]
LONGER_NAME = "x/refs/tags/v9.9.9"
BEGINS_WITH_IT = "v6.6.6-x/refs/tags/v6.6.6"                # held: the tag asked for, v6.6.6, begins this name
HELD = ["v1.0.0", "v1.0.0-dryrun", "v1.0.0-rc10", *HELD_NOT_RELEASE, LONGER_NAME, BEGINS_WITH_IT]


def locales() -> list:
    """C, and C.UTF-8 (a runner's) where this machine has it."""
    try:
        listed = _proc.run(["locale", "-a"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=30).stdout.lower().split()
    except (OSError, subprocess.SubprocessError):
        listed = []
    return ["C"] + (["C.UTF-8"] if {"c.utf8", "c.utf-8"} & set(listed) else [])


@pytest.fixture(scope="module")
def remote(tmp_path_factory):
    """(a checkout whose origin holds the tags v1.0.0 (annotated), v1.0.0-dryrun and v1.0.0-rc10, the tags of
    HELD_NOT_RELEASE and LONGER_NAME, and a branch v1.0.1 with no tag; the environment git runs in). Nothing of the
    caller's git configuration is read."""
    return make_origin(tmp_path_factory.mktemp("remote"), [*HELD_NOT_RELEASE, LONGER_NAME, BEGINS_WITH_IT])


def make_origin(base, tags: list) -> tuple:
    """The fixture's repositories, made in `base`; it stops first if two of the names it would hold differ only in
    case (a case-insensitive filesystem, macOS's, keeps them as one file)."""
    twins = case_twins(["v1.0.0", "v1.0.0-dryrun", "v1.0.0-rc10", *tags, "v1.0.1"])
    assert not twins, f"two names the origin would hold differ only in case: {twins}"
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(base), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "LC_ALL": "C"}
    ident = ["-c", "user.name=release", "-c", "user.email=release@example.invalid"]
    git("init", "-q", "--bare", str(base / "origin.git"), cwd=base, env=env)
    git("init", "-q", str(base / "maker"), cwd=base, env=env)
    maker = base / "maker"
    (maker / "a.txt").write_text("a\n", encoding="utf-8")
    git("add", "a.txt", cwd=maker, env=env)
    git(*ident, "commit", "-q", "-m", "one", cwd=maker, env=env)
    git(*ident, "tag", "-a", "v1.0.0", "-m", "v1.0.0", cwd=maker, env=env)
    git("tag", "v1.0.0-dryrun", cwd=maker, env=env)
    git("tag", "v1.0.0-rc10", cwd=maker, env=env)
    for name in tags:
        git("tag", name, cwd=maker, env=env)
    git("branch", "v1.0.1", cwd=maker, env=env)
    git("push", "-q", str(base / "origin.git"), "--all", cwd=maker, env=env)
    git("push", "-q", str(base / "origin.git"), "--tags", cwd=maker, env=env)
    git("clone", "-q", str(base / "origin.git"), str(base / "checkout"), cwd=base, env=env)
    return base / "checkout", env


def run_the_check(text: str, given: str, checkout, env, folder, locale: str = "C") -> tuple:
    """(exit status, the lines handed on as outputs, what the step printed) for the check's text and one input.
    The child's environment is the caller's, less what would reach the shell or git from outside (BASH_ENV, ENV,
    BASHOPTS, SHELLOPTS, the locale, and any GIT_, LC_ or GITHUB_ name), with the fixture's and the step's own
    added."""
    out = folder / "github_output"
    out.write_text("", encoding="utf-8")
    child = {k: v for k, v in os.environ.items()
             if k not in ("BASH_ENV", "ENV", "BASHOPTS", "SHELLOPTS", "LANG", "LANGUAGE", "TAG_INPUT")
             and not k.startswith(("GIT_", "LC_", "GITHUB_"))}
    child.update(env, LC_ALL=locale, TAG_INPUT=given, GITHUB_OUTPUT=str(out), GITHUB_REPOSITORY="Slickfish/talima")
    done = _proc.run([*AS_A_RUNNER_STARTS_BASH, text], cwd=checkout, capture_output=True, text=True,
                     encoding="utf-8", errors="replace", timeout=60, env=child)
    return done.returncode, out.read_text(encoding="utf-8").splitlines(), done.stdout + done.stderr


ACCEPTED = [
    ("the slip of 24 November", "v1.0.0 ", "v1.0.0", "false"),
    ("white space at both ends", " \tv1.0.0\n", "v1.0.0", "false"),
    ("a tag as given", "v1.0.0", "v1.0.0", "false"),
    ("the dry-run tag with a trailing space", "v1.0.0-dryrun ", "v1.0.0-dryrun", "true"),
    ("a suffix of letters and digits", "v1.0.0-rc10", "v1.0.0-rc10", "false"),
    ("a vertical tab and a form feed", "\vv1.0.0\f", "v1.0.0", "false"),
    ("a carriage return, as a Windows clipboard leaves one", "v1.0.0\r\n", "v1.0.0", "false"),
]


LOCALES = pytest.mark.parametrize("locale", locales())


@NO_SHELL
@LOCALES
@pytest.mark.parametrize("what, given, tag, dryrun", ACCEPTED, ids=[a[0].replace(" ", "-") for a in ACCEPTED])
def test_t2_a_release_tag_the_repository_holds_is_trimmed_and_handed_on(remote, tmp_path, what, given, tag, dryrun,
                                                                        locale):
    status, outputs, said = run_the_check(check_step(load())["run"], given, *remote, tmp_path, locale)
    assert status == 0, (what, said)
    assert outputs == [f"tag={tag}", f"dryrun={dryrun}"], (what, outputs)


NOT_A_RELEASE_TAG = [
    ("empty", ""), ("white space alone", " \t "), ("no v", "1.0.0"), ("an upper-case V", "V2.0.0"),
    ("two numbers", "v1.0"), ("four numbers", "v1.0.0.0"), ("an empty suffix", "v1.0.0-"),
    ("an upper-case suffix", "v3.0.0-RC1"), ("an underscore in the suffix", "v1.0.0-dry_run"),
    ("a space inside", "v1.0.0 v1.0.1"), ("a line end inside", "v1.0.0\nv1.0.1"),
    ("a ref's full name", "refs/tags/v1.0.0"), ("a letter outside ASCII", "v1.0.0-\u00e9"),
    ("a full-width digit", "v\uff11.0.0"), ("a command after it", "v1.0.0;touch ran"),
    ("a substitution", "$(touch ran)v1.0.0"), ("a name the origin holds", "nightly"),
    ("an ideographic space before it", "\u3000v1.0.0"), ("an em space before it", "\u2003v1.0.0"),
    ("a no-break space after it", "v1.0.0\u00a0"),
]
# How the refusal shows a space from outside ASCII: as the escapes of its bytes
SHOWN = {"\u3000v1.0.0": "\\343\\200\\200", "\u2003v1.0.0": "\\342\\200\\203",
         "v1.0.0\u00a0": "\\302\\240"}


@NO_SHELL
@LOCALES
@pytest.mark.parametrize("what, given", NOT_A_RELEASE_TAG, ids=[a[0].replace(" ", "-") for a in NOT_A_RELEASE_TAG])
def test_t2_a_tag_that_is_not_a_release_tag_is_refused_before_anything_else(remote, tmp_path, what, given, locale):
    checkout, env = remote
    status, outputs, said = run_the_check(check_step(load())["run"], given, checkout, env, tmp_path, locale)
    assert refusal_problems(given, status, outputs, said, [checkout, tmp_path]) == [], what


def refusal_problems(given: str, status: int, outputs: list, said: str, folders: list) -> list:
    """Every way a run of the check is not a refusal of a tag as not a release tag: an exit of 0, an output handed
    on, another message, a file the input made, or a space from outside ASCII shown as itself and not as the
    escapes of its bytes."""
    problems = []
    if status == 0 or outputs:
        problems.append(f"not refused: exit {status}, outputs {outputs}")
    if "is not a release tag" not in said:
        problems.append(f"refused for another reason: {said.strip()[-200:]}")
    problems += [f"the input ran in {folder}" for folder in folders if (folder / "ran").exists()]
    if given in SHOWN and SHOWN[given] not in said:
        problems.append("the refusal does not show the space as the escapes of its bytes")
    return problems


@NO_SHELL
@pytest.mark.parametrize("name, value", [("BASHOPTS", "nocasematch"), ("SHELLOPTS", "noexec")])
def test_t2_companion_the_callers_shell_options_do_not_reach_the_check(remote, tmp_path, monkeypatch, name, value):
    """Delta check 2's E9 and the last delta check's F2: bash reads each of these at start, if the environment holds
    it. BASHOPTS=nocasematch let the pattern take an upper-case V; SHELLOPTS=noexec runs nothing and exits 0. The
    check's child is given neither."""
    monkeypatch.setenv(name, value)
    checkout, env = remote
    status, outputs, said = run_the_check(check_step(load())["run"], "V2.0.0", checkout, env, tmp_path)
    assert refusal_problems("V2.0.0", status, outputs, said, [checkout, tmp_path]) == [], said


@NO_SHELL
def test_t2_companion_an_acceptance_and_another_refusal_are_not_taken_for_the_patterns(remote, tmp_path):
    """refusal_problems names a run that accepted, and a run refused for another reason, each a run of the step."""
    checkout, env = remote
    text = check_step(load())["run"]
    status, outputs, said = run_the_check(text, "v1.0.0", checkout, env, tmp_path)
    found = refusal_problems("v1.0.0", status, outputs, said, [checkout, tmp_path])
    assert found[0] == "not refused: exit 0, outputs ['tag=v1.0.0', 'dryrun=false']", found
    status, outputs, said = run_the_check(text, "v9.9.9", checkout, env, tmp_path)
    found = refusal_problems("v9.9.9", status, outputs, said, [checkout, tmp_path])
    assert len(found) == 1 and found[0].startswith("refused for another reason:"), found


@NO_SHELL
def test_t2_companion_a_refusal_that_prints_the_space_as_itself_is_named(remote, tmp_path):
    """Under C.UTF-8 (a runner's), printf's %q in the locale prints a no-break space as itself: the message reads
    "refused:  v1.0.0" and the space cannot be seen. Planted in the step's own text."""
    if "C.UTF-8" not in locales():
        pytest.skip("this machine has no C.UTF-8 locale")
    checkout, env = remote
    text = check_step(load())["run"]
    assert text.count("$(LC_ALL=C printf '%q' \"$TAG_INPUT\")") == 1
    planted = text.replace("$(LC_ALL=C printf '%q' \"$TAG_INPUT\")", "$(printf '%q' \"$TAG_INPUT\")")
    given = "v1.0.0\u00a0"
    status, outputs, said = run_the_check(planted, given, checkout, env, tmp_path, "C.UTF-8")
    assert refusal_problems(given, status, outputs, said, [checkout, tmp_path]) == [
        "the refusal does not show the space as the escapes of its bytes"], said


@NO_SHELL
def test_t2_the_tags_only_the_pattern_refuses_are_held_by_the_origin(remote):
    """Without them, the existence check would refuse the pattern's inputs too, and the pattern's own refusal
    would have no witness."""
    checkout, env = remote
    names = {line.split("\t")[1] for line in git("ls-remote", "--tags", "origin", cwd=checkout, env=env).splitlines()}
    assert {f"refs/tags/{t}" for t in HELD} <= names, sorted(names)
    assert set(HELD_NOT_RELEASE) <= {given for _what, given in NOT_A_RELEASE_TAG}
    held_names = [line.split("\t")[1].split("/", 2)[2] for line in
                  git("ls-remote", "--heads", "--tags", "origin", cwd=checkout, env=env).splitlines()
                  if not line.endswith("^{}")]
    assert len(held_names) >= len(HELD) + 1 and case_twins(held_names) == [], "two names the origin holds differ " \
        "only in case"


def case_twins(names: list) -> list:
    """The names that another of them equals but for case: a case-insensitive filesystem keeps them as one file."""
    return sorted(name for name in names if sum(other.lower() == name.lower() for other in names) > 1)


def test_t2_companion_two_names_that_differ_only_in_case_are_named(tmp_path):
    """The last delta check's F5: the check applied where the origin is made, so a twin stops it there."""
    assert case_twins(["v1.0.0", "V1.0.0", "v1.0.0-rc10"]) == ["V1.0.0", "v1.0.0"]
    with pytest.raises(AssertionError, match="differ only in case"):
        make_origin(tmp_path, ["V1.0.0"])
    assert not (tmp_path / "origin.git").exists(), "the origin was made before the check"


NOT_HELD = [("a tag the repository does not hold", "v8.8.8"), ("a tag held only under a longer name", "v9.9.9"),
            ("a tag held only under a name that begins with it", "v6.6.6"),
            ("a branch of that name, and no tag", "v1.0.1"), ("a tag that only begins like one held", "v1.0.0-rc1")]


@NO_SHELL
@LOCALES
@pytest.mark.parametrize("what, given", NOT_HELD, ids=[a[0].replace(" ", "-") for a in NOT_HELD])
def test_t2_a_release_tag_the_repository_does_not_hold_is_refused(remote, tmp_path, what, given, locale):
    status, outputs, said = run_the_check(check_step(load())["run"], given, *remote, tmp_path, locale)
    assert status != 0 and outputs == [], (what, status, outputs)
    assert f"has no tag {given}" in said, (what, said)


@NO_SHELL
def test_t2_tags_that_cannot_be_read_are_said_to_be_that(remote, tmp_path):
    checkout, env = remote
    git("remote", "set-url", "origin", str(tmp_path / "no-such.git"), cwd=checkout, env=env)
    try:
        status, outputs, said = run_the_check(check_step(load())["run"], "v1.0.0", checkout, env, tmp_path)
    finally:
        git("remote", "set-url", "origin", str(checkout.parent / "origin.git"), cwd=checkout, env=env)
    assert status != 0 and outputs == [], (status, outputs)
    assert "could not be read" in said and "has no tag" not in said, said


# --- T3: the companions ---------------------------------------------------------------------------------------------

def altered(change) -> dict:
    wf = copy.deepcopy(load())
    change(wf)
    return wf


def _no_wait(wf):
    wf["jobs"]["bundles"]["needs"] = [n for n in needs_of(wf["jobs"]["bundles"]) if n != "tag"]


def _raw_dryrun(wf):
    wf["jobs"]["image"]["env"]["DRYRUN"] = "${{ endsWith(inputs.tag, '-dryrun') }}"


def _raw_checkout(wf):
    wf["jobs"]["verifier"]["steps"][0]["with"]["ref"] = "${{ inputs.tag }}"


def _build_in_the_tag_job(wf):
    wf["jobs"]["tag"]["steps"].append({"name": "Build", "run": "python scripts/build_bundle.py --twice"})


def _another_output(wf):
    wf["jobs"]["tag"]["outputs"]["dryrun"] = "false"


def _workflow_env(wf):
    wf["env"] = {"RELEASE_TAG": "${{ inputs.tag }}"}


def _write_access(wf):
    wf["jobs"]["tag"]["permissions"] = {"contents": "write"}


def _bare_checkout(wf):
    wf["jobs"]["interceptor"]["steps"][0]["with"]["ref"] = "${{ env.RELEASE_TAG }}"


def _tag_waits(wf):
    wf["jobs"]["tag"]["needs"] = ["image"]


def _tag_shell(wf):
    wf["jobs"]["tag"]["defaults"] = {"run": {"shell": "sh"}}


def _second_env_name(wf):
    wf["jobs"]["tag"]["steps"][1]["env"]["TAG_ALSO"] = "${{ github.sha }}"


def _input_in_script(wf):
    wf["jobs"]["tag"]["steps"][1]["run"] = 'tag="${{ inputs.tag }}"\n' + wf["jobs"]["tag"]["steps"][1]["run"]


def _raw_release_tag(wf):
    wf["jobs"]["bundles"]["env"]["RELEASE_TAG"] = "${{ inputs.tag }}"


def _no_verify_tag(wf):
    step = next(s for s in wf["jobs"]["release"]["steps"] if "gh release create" in str(s.get("run")))
    step["run"] = step["run"].replace(" --verify-tag", "")


def _reader_in_a_name(wf):
    wf["jobs"]["image"]["steps"][2]["name"] = "Build ${{ github.ref_name }}"


COMPANIONS = [
    ("a build that does not wait for the tag job", _no_wait, "bundles: does not wait for the tag job"),
    ("a dry-run flag read from the raw input", _raw_dryrun, "image: DRYRUN is"),
    ("a checkout of the raw input", _raw_checkout, "verifier: checks out"),
    ("a build step in the tag job", _build_in_the_tag_job, "the tag job's steps are not a checkout and the check"),
    ("an output that is not the check step's", _another_output, "the tag job's outputs are"),
    ("an environment for the whole workflow", _workflow_env, "the workflow has an environment of its own"),
    ("the tag job given write access", _write_access, "the tag job's permissions are"),
    ("a checkout of the bare tag name", _bare_checkout, "interceptor: checks out"),
    ("the tag job waiting for another", _tag_waits, "the tag job waits for"),
    ("the tag job's commands run by another shell", _tag_shell, "the tag job's commands are not run by bash"),
    ("a second name in the check's environment", _second_env_name, "the check reads"),
    ("the input written into the check's script", _input_in_script, "the check writes the input into its script"),
    ("a job's RELEASE_TAG read from the input", _raw_release_tag, "bundles: RELEASE_TAG is"),
    ("the ref's name read in a step's name", _reader_in_a_name, "the input or the ref's name is read at"),
    ("a release that may make the tag it is given", _no_verify_tag, "the release is not created for the checked tag"),
]


@pytest.mark.parametrize("what, change, named", COMPANIONS, ids=[c[0].replace(" ", "-") for c in COMPANIONS])
def test_t3_companions_a_release_that_could_build_on_an_unchecked_tag_is_named(what, change, named):
    found = shape_problems(altered(change))
    assert any(p.startswith(named) for p in found), (what, found)
