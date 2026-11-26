"""W11c-B1, the operator's ruling of 24 November: the public repository is Slickfish/talima and its image is
ghcr.io/slickfish/talima.

Until the ruling the source tree named the repository it is written in, which stays private: in the image default
a stranger's compose file pulls, in the source label on the image, and in the contact the seeder sends to
Wikidata, whose policy wants an address a stranger can reach (RUN_WORKLIST W11b, step 6). Each names the public
repository now. The release workflow read the image's owner from the repository and wrote its name out; it reads
both from the repository it runs in now, so a release of the public repository pushes the image its own compose
file pulls.

WHAT IS HELD HERE
    P1  The image default is IMAGE wherever the tree states one: every `${NEXUS_IMAGE:-...}` of docker-compose.yml
        and of cloud-init's embedded copy, the comment that names the default in each, `.env.example`'s line, and
        the sentence in docs/DEPLOY.md.
    P2  The repository is REPOSITORY_URL where the tree names one for a stranger: the Dockerfile's
        `org.opencontainers.image.source` label, and the seeder's User-Agent contact, read by AST from
        backend/kb/seeder.py.
    P3  `.github/workflows/release.yml`, held two ways.
        THE TEXT. The file is the one reviewed on 24 November: WORKFLOW_SHA256 is its SHA-256, line ends aside
        (CRLF and a lone CR are read as LF, as Python reads a text file, so a checkout with CRLF ends holds). Any
        other edit to the workflow fails here until that constant is edited too: a recorded decision each time,
        and the price is meant. This is what holds the workflow. Three reviews each passed altered workflows
        through the check of its day (a reading for the naming expression; a reading of docker's spellings; an
        allow-list of commands), and no reading is offered as the holder again.
        THE READING. Of that text, as YAML gives it, compared whole and never searched: the image job is the
        mapping written out below, key for key, its commands character for character (the image is named by
        three commands: read `ghcr.io/<github.repository>` lower-cased, hand it on as the step's output, print
        it; it is built under that output with the release's tag and `latest`; the login is to ghcr.io; the two
        pushes are those two tags); and exactly one step of the release job is the one written out as creating
        the release (its keys, its four lines, its notes word for word by their hash).
        A LIST BESIDE IT, which names an obvious edit in words and proves nothing about the rest. Outside the
        image job and that one step: no step's commands hold the words `gh release` or one of the eight tools
        and four registry hosts of OF_AN_IMAGE; every job's commands are run by bash and no step has a shell of
        its own; every action a step uses is one of four, at whatever version; no job calls a workflow kept
        elsewhere; the workflow has no environment of its own (the tag job reads the tag: test_release_tag.py).
        On a system with a shell the naming step's own text is run as the file has it, by bash with the options
        a runner gives `shell: bash` (the text handed over with `-c` where a runner writes a file), with the one
        expression Actions fills in filled in: for the public repository it gives IMAGE; and a step whose first
        command fails stops there.
    P4  Companions. An image default left elsewhere, in each of the four files in turn; the label and the
        contact at another address. Fifty-four altered workflows, each named by the reading or the list ALONE
        (the text's hash names every altered file, so it is left out of that check): where the reading is the
        check the plant is something it does not name, and for each part of the list (a tool, a host in either
        case, `gh release`, a word broken across a continued line) one plant holds a word of the list, because a
        list can only be shown to fire on its own words. Most of them would push or announce another image or
        break the push; some are a change of shape only. A release job with no step that creates the release as
        held, with two, and with one that creates it through the API, each named by the reading's count and by
        nothing else. Four edits the reading and the list do not see, each stopped by the text's hash and by
        nothing else here: these are what nothing but the hash names. And the same text with CRLF line ends,
        which holds.

WHAT IS NOT HELD HERE
    That the image exists: it does once a release of the public repository has pushed it. And that no file names
    the repository this tree is written in: a test that looked for that name would have to hold it. The public
    snapshot's scan, the run's own tool outside this tree, is what looks.
    Of the workflow: that the reviewed text pushes and announces no image but the one it names is what the review
    read; the reading above is the part of it written down, and nothing here derives the rest. The list is
    spellings in a step's `run` text: a tool or a host it does not name, a name put together as the shell runs, a
    word quoted or an option put between `gh` and `release`, a call to an API that does the same, a key written
    twice (YAML keeps the last), and the other jobs' own keys beyond `uses` and `defaults`, their environments,
    matrices, containers and services are not seen by it. An edit that makes any of them is stopped by the text's
    hash, which says that the file changed and not what the change does: whoever edits the constant reads the
    change. What a program does that another job starts (the bundles', the verifiers' and the interceptor's jobs
    run this repository's scripts, and their own tests are where those are read), what the build puts in the
    image, what the four actions do, and whether the registry takes the push are not read at all.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from tests import _proc
from tests._tracked import REPO

# The ruling's own three names, each written out: none is made from another, so each check has something to fail.
REPOSITORY = "Slickfish/talima"
REPOSITORY_URL = "https://github.com/Slickfish/talima"
IMAGE = "ghcr.io/slickfish/talima"
DEFAULT = re.compile(r"\$\{NEXUS_IMAGE:-([^}]*)\}")
COMMENT = re.compile(r"NEXUS_IMAGE and NEXUS_TAG \(the image; ([^)]+)\)")
ENV_LINE = re.compile(r"^#? ?NEXUS_IMAGE=(.*)$", re.M)
DOC_SENTENCE = re.compile(r"The image the compose file pulls is `([^`]+)`")
LABEL = re.compile(r'org\.opencontainers\.image\.source="([^"]*)"')
CONTACT = re.compile(r"\((https?://[^;)]+); ")

# --- the release workflow: its text, and what was read off it ---------------------------------------------------
WORKFLOW = ".github/workflows/release.yml"
WORKFLOW_SHA256 = "ad302f53809d5172883efb73352080b8c298c68c5a22ea59c2794a3c2167c58f"
FROM_THE_REPOSITORY = "${{ github.repository }}"
LOWERED = "tr '[:upper:]' '[:lower:]'"
READS = 'image=$(echo "ghcr.io/' + FROM_THE_REPOSITORY + '" | ' + LOWERED + ")"
NAMES_IT = (READS + "\n"
            'echo "image=$image" >> "$GITHUB_OUTPUT"\n'
            'echo "dryrun=$DRYRUN tag=$RELEASE_TAG image=$image"\n')
NAMED = '"${{ steps.name.outputs.image }}'
TAGS = [NAMED + ':$RELEASE_TAG"', NAMED + ':latest"']
PUBLISHING, DRY = "env.DRYRUN == 'false'", "env.DRYRUN == 'true'"
# Since Amendment 11 the tag job checks the tag before any build, and every job reads the tag and the flag from it
FROM_THE_TAG_JOB = {"RELEASE_TAG": "${{ needs.tag.outputs.tag }}", "DRYRUN": "${{ needs.tag.outputs.dryrun }}"}
BASH = {"run": {"shell": "bash"}}
# The image job whole, as YAML gives it: every key it has, and each step's commands character for character.
IMAGE_JOB = {
    "name": "image (ghcr.io)", "needs": "tag", "runs-on": "ubuntu-24.04", "timeout-minutes": 45,
    "env": FROM_THE_TAG_JOB,
    "defaults": BASH,
    "steps": [
        {"uses": "actions/checkout@v4", "with": {"ref": "refs/tags/${{ env.RELEASE_TAG }}"}},
        {"name": "Name the image", "id": "name", "run": NAMES_IT},
        {"name": "Build", "run": (
            "set -o pipefail\n"
            "docker build -f deploy/Dockerfile \\\n"
            "  -t " + TAGS[0] + " \\\n"
            "  -t " + TAGS[1] + " . 2>&1 | tail -n 20\n"
            "docker image inspect " + TAGS[0] + " --format 'built {{.Id}} ({{.Size}} bytes)'\n")},
        {"name": "Log in to GHCR", "if": PUBLISHING,
         "run": 'echo "${{ github.token }}" | docker login ghcr.io -u "${{ github.actor }}" --password-stdin'},
        {"name": "Push", "if": PUBLISHING, "run": "docker push " + TAGS[0] + "\ndocker push " + TAGS[1] + "\n"},
        {"name": "Dry run", "if": DRY, "run": (
            'echo "dry run ($RELEASE_TAG): the image was built and NOT pushed; no login happened" | '
            'tee -a "$GITHUB_STEP_SUMMARY"\n')},
    ],
}
# The step that creates the release: its keys, its four lines (the second, the notes, by its SHA-256; it begins
# `notes="NEXUS $RELEASE_TAG. Image: $image:$RELEASE_TAG.`), and the empty line a block's last line end leaves.
CREATES = {"name": "Create the release with the bundles as assets", "if": PUBLISHING}
NOTES_SHA256 = "9aba4d9fed5d42376836217a18eb3fccd045c271d4e0feead485d3c44fa4652e"
AROUND_THE_NOTES = [READS, "mapfile -t files < <(find assets -type f | sort)",
                    'gh release create "$RELEASE_TAG" --verify-tag --title "NEXUS $RELEASE_TAG" --notes "$notes" '
                    '"${files[@]}"', ""]
CANNOT_PUSH = ("actions/checkout@", "actions/setup-python@", "actions/upload-artifact@", "actions/download-artifact@")
OF_AN_IMAGE = re.compile(r"\b(?:docker|podman|skopeo|crane|oras|buildah|regctl|nerdctl)\b"
                         r"|\b(?:ghcr|docker|quay|gcr)\.io\b"
                         r"|\bgh release\b", re.I)
OWNER_AND_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
# `shell: bash` on a runner is `bash --noprofile --norc -eo pipefail {0}`, the step's text in the file {0}
AS_ACTIONS_STARTS_BASH = ["bash", "--noprofile", "--norc", "-eo", "pipefail"]


def read_text(path: Path) -> str:
    """A file's text with its line ends as LF, whatever the checkout wrote (Python's own reading of a text file)."""
    return path.read_text(encoding="utf-8")


def read(relative: str) -> str:
    return read_text(REPO / relative)


# --- P1 ----------------------------------------------------------------------------------------------------------

def image_default_problems(compose: str, cloud: str, env_example: str, deploy_doc: str) -> list:
    """Every way the four files' image default is not IMAGE; empty when each states it."""
    problems = []
    for name, text in (("docker-compose.yml", compose), ("deploy/cloud-init.yaml", cloud)):
        found = DEFAULT.findall(text)
        if len(found) < 2:
            problems.append(f"{name}: {len(found)} image default(s) read; the compose file states two")
        problems += [f"{name}: an image default is {d!r}, not {IMAGE!r}" for d in found if d != IMAGE]
        said = COMMENT.findall(text)
        if said != [f"{IMAGE}:latest"]:
            problems.append(f"{name}: the comment names the default as {said}, not {IMAGE}:latest")
    lines = ENV_LINE.findall(env_example)
    if lines != [IMAGE]:
        problems.append(f".env.example: the image's line gives {lines}, not {IMAGE!r}")
    told = DOC_SENTENCE.findall(deploy_doc)
    if told != [f"{IMAGE}:latest"]:
        problems.append(f"docs/DEPLOY.md: the image the compose file pulls is given as {told}, not {IMAGE}:latest")
    return problems


def the_four() -> list:
    return [read("docker-compose.yml"), read("deploy/cloud-init.yaml"), read(".env.example"), read("docs/DEPLOY.md")]


def test_p1_the_image_default_is_the_public_image_wherever_the_tree_states_one():
    assert image_default_problems(*the_four()) == []


# --- P2 ----------------------------------------------------------------------------------------------------------

def seeder_contacts(source: str) -> list:
    """The contact address in each User-Agent the seeder's source spells (a string constant naming its client)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "NEXUS-Cognitive-Substrate" in node.value:
            found += CONTACT.findall(node.value) or ["no contact"]
    return found


def repository_problems(dockerfile: str, seeder: str) -> list:
    problems = []
    labels = LABEL.findall(dockerfile)
    if labels != [REPOSITORY_URL]:
        problems.append(f"deploy/Dockerfile: the image's source label is {labels}, not {REPOSITORY_URL}")
    contacts = seeder_contacts(seeder)
    if contacts != [REPOSITORY_URL]:
        problems.append(f"backend/kb/seeder.py: the User-Agent's contact is {contacts}, not {REPOSITORY_URL}")
    return problems


def test_p2_the_label_and_the_seeders_contact_name_the_public_repository():
    assert repository_problems(read("deploy/Dockerfile"), read("backend/kb/seeder.py")) == []


# --- P3 ----------------------------------------------------------------------------------------------------------

def text_problems(workflow: str) -> list:
    """Whether the workflow's text is the one reviewed: its SHA-256, taken over the text as `read` gives it."""
    got = hashlib.sha256(workflow.encode("utf-8")).hexdigest()
    if got == WORKFLOW_SHA256:
        return []
    return [f"{WORKFLOW} is not the text held here (its sha256 is {got}): read the change against what P3 says of "
            "this workflow, then make the edit here too (WORKFLOW_SHA256, and the reading where it moved)"]


def creates_the_release(step: dict) -> bool:
    """Whether a step is the one held here as creating the release: its keys, its four lines, its notes."""
    lines = str(step.get("run") or "").split("\n")
    return ({key: value for key, value in step.items() if key != "run"} == CREATES
            and [lines[0], *lines[2:]] == AROUND_THE_NOTES
            and hashlib.sha256(lines[1].encode("utf-8")).hexdigest() == NOTES_SHA256)


def commands(step: dict) -> str:
    """A step's commands for the list's search: each backslash that ends a line taken out with its line end, which
    is how bash joins a continued line, so that a word broken across two lines is found whole. A comment is a
    line like any other: a here-document can hand one to a shell."""
    return str(step.get("run") or "").replace("\\\n", "")


def reading_problems(workflow: str) -> list:
    """Every way the release workflow, as YAML gives it, is not what P3's reading and list say; empty when it
    is. The text's hash is not looked at here: text_problems does that, and P3 asserts both."""
    wf = yaml.safe_load(workflow) or {}
    jobs = wf.get("jobs") or {}
    problems = []
    image = jobs.get("image") or {}
    if image != IMAGE_JOB:
        differs = sorted(key for key in set(image) | set(IMAGE_JOB) if image.get(key) != IMAGE_JOB.get(key))
        where_it_differs = f"it differs in {differs}"
        if "steps" in differs:
            steps, held = image.get("steps") or [], IMAGE_JOB["steps"]
            at = next((n for n, pair in enumerate(zip(steps, held)) if pair[0] != pair[1]),
                      min(len(steps), len(held)))
            where_it_differs += (f"; it has {len(steps)} step(s) for the {len(held)} held, and the first that "
                                 f"differs is step {at + 1}: {steps[at] if at < len(steps) else 'missing'}")
        problems.append("the image job is not the one held here whole (an edit to it is made here too): "
                        + where_it_differs)
    release = (jobs.get("release") or {}).get("steps") or []
    creating = [step for step in release if creates_the_release(step)]
    if len(creating) != 1:
        named = [step for step in release if step.get("name") == CREATES["name"]]
        problems.append(f"{len(creating)} step(s) of the release job are the one held here as creating the release "
                        f"(its keys, its four lines, its notes word for word by their hash): not one. "
                        f"{len(named)} have its name" + "".join(f": {step}" for step in named[:1]))
    if wf.get("env"):
        problems.append(f"the workflow has an environment of its own, {sorted(wf['env'])}: the tag is read by the tag "
                        "job alone")
    for name, job in jobs.items():
        if "uses" in job:
            problems.append(f"{name}: calls a workflow kept elsewhere ({job['uses']}); what that pushes is not read")
        if job.get("defaults") != BASH:
            problems.append(f"{name}: its commands are not run by bash as written (defaults: {job.get('defaults')})")
        for step in job.get("steps") or []:
            uses = str(step.get("uses") or "")
            if "shell" in step:
                problems.append(f"{name}: the step {step.get('name')!r} has a shell of its own: {step['shell']}")
            if uses and not uses.startswith(CANNOT_PUSH):
                problems.append(f"{name}: a step uses {uses}, which is not one of the four actions held to be unable "
                                "to push")
            if name == "image" or any(step is held for held in creating):
                continue
            found = sorted(set(OF_AN_IMAGE.findall(commands(step))))
            if found:
                problems.append(f"{name}: the step {step.get('name')!r} names {found} outside the image job and the "
                                "step that creates the release")
    return problems


def test_p3_the_release_workflow_is_the_text_reviewed_and_names_its_image_after_its_repository():
    workflow = read(WORKFLOW)
    assert text_problems(workflow) == []
    assert reading_problems(workflow) == []
    assert ("ghcr.io/" + REPOSITORY).lower() == IMAGE and REPOSITORY_URL == "https://github.com/" + REPOSITORY, \
        "the ruling's three names are one repository's"


NO_SHELL = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="the naming commands are bash's (the job's shell); no shell is assumed on Windows, where P3 holds the "
           "same three commands by their text")


def named_by_running(workflow: str, repository: str, folder) -> tuple:
    """(the step's output file's lines, what it printed) when the workflow's naming step is run for `repository`.
    What is run is the step's own text from the file, and only when that text is NAMES_IT character for character
    and the repository is an owner and a name in the letters GitHub gives one: anything else is refused before a
    shell is started. The shell is started with the options Actions gives `shell: bash` (the text handed over
    with `-c` and not in a file), with an environment of its own (the path to find `tr`, and the three variables
    the step reads) and the folder given."""
    jobs = yaml.safe_load(workflow)["jobs"]
    step = next(step for step in jobs["image"]["steps"] if step.get("id") == "name")
    assert step.get("run") == NAMES_IT, "the naming step is not the three commands held; nothing was run"
    assert OWNER_AND_NAME.fullmatch(repository), f"{repository!r} is not an owner and a name; nothing was run"
    out = folder / "github_output"
    env = {"PATH": os.environ.get("PATH", ""), "GITHUB_OUTPUT": str(out), "DRYRUN": "true",
           "RELEASE_TAG": "v0.0.0-dryrun"}
    done = _proc.run([*AS_ACTIONS_STARTS_BASH, "-c", step["run"].replace(FROM_THE_REPOSITORY, repository)], env=env,
                     cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert done.returncode == 0, f"the naming step exited {done.returncode}: {done.stderr}"
    return out.read_text(encoding="utf-8").splitlines(), done.stdout.splitlines()


@NO_SHELL
@pytest.mark.parametrize("repository, image",
                         [(REPOSITORY, IMAGE), ("Some-One/My.Fork_1", "ghcr.io/some-one/my.fork_1")],
                         ids=["the-public-repository", "a-fork"])
def test_p3b_the_naming_step_run_as_the_file_has_it_gives_the_repositorys_own_image(tmp_path, repository, image):
    """The workflow's own naming step, its text read from the file, with the one expression Actions fills in
    filled in."""
    output, printed = named_by_running(read(WORKFLOW), repository, tmp_path)
    assert output == [f"image={image}"]
    assert printed == [f"dryrun=true tag=v0.0.0-dryrun image={image}"]


@NO_SHELL
@pytest.mark.parametrize("old, new", [
    ('          echo "image=$image" >> "$GITHUB_OUTPUT"\n',
     '          echo "image=$image" >> "$GITHUB_OUTPUT"\n          touch "{marker}"\n'),
    ('          echo "image=$image" >> "$GITHUB_OUTPUT"\n',
     '          echo "image=$image"  >>  "$GITHUB_OUTPUT"\n'),
    ("          image=$(echo", '          touch\\\n           "{marker}"\n          image=$(echo'),
], ids=["a-fourth-command", "other-spacing", "a-continued-line"])
def test_p3b_companion_a_naming_step_that_is_any_other_text_is_not_run(tmp_path, old, new):
    """The first delta check's D2 and the second's E1: with a fourth command in the step the test ran it, with
    the caller's whole environment; then it compared the step with its spacing collapsed and its continued lines
    joined by a space, and ran its own constants where the file said something else. The step's text is compared
    character for character, and any other text is refused before a shell is started: the marker a planted
    command would write is not there."""
    marker = tmp_path / "ran"
    planted = swapped(read(WORKFLOW), old, new.replace("{marker}", marker.as_posix()), 1)
    with pytest.raises(AssertionError, match="nothing was run"):
        named_by_running(planted, REPOSITORY, tmp_path)
    assert not marker.exists(), "the planted command was run"


# Values that are not an owner and a name; the first three would run `touch ran` in the shell's folder
NOT_A_NAME = ['someone/x"; touch ran; echo "', "someone/$(touch ran)", "someone/`touch ran`", "someone/x y",
              "someone/x\n", "a/b/c", "someone", "/x", ""]


@NO_SHELL
@pytest.mark.parametrize("repository", NOT_A_NAME, ids=["closes-the-quote", "a-substitution", "backticks", "a-space",
                                                        "a-line-end", "three-parts", "one-part", "no-owner", "empty"])
def test_p3b_companion_a_repository_that_is_not_a_name_is_refused_before_a_shell(tmp_path, repository):
    """The second delta check's E12 and the third's F3. The repository is put inside a quoted word of the script:
    a value that closes the quote, or needs no closing (a substitution), is refused before a shell is started."""
    with pytest.raises(AssertionError, match="nothing was run"):
        named_by_running(read(WORKFLOW), repository, tmp_path)
    assert not (tmp_path / "ran").exists(), "the value's command was run"
    assert not (tmp_path / "github_output").exists(), "a shell was started"


@NO_SHELL
def test_p3b_companion_a_step_whose_first_command_fails_stops_there_as_on_a_runner(tmp_path, monkeypatch):
    """The third delta check's F4. A runner starts `shell: bash` with `-e`: with a `tr` that fails, the step
    stops at its first command, the later two are not run, and the run is reported as failed and not read. The
    failing `tr` leaves a file in the folder it was run in: the shell's folder is the one given."""
    tools, work = tmp_path / "tools", tmp_path / "work"
    tools.mkdir(), work.mkdir()
    fake = tools / "tr"
    fake.write_text("#!/bin/sh\n: > tr-was-here\nexit 3\n", encoding="utf-8", newline="")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ.get("PATH", ""))
    with pytest.raises(AssertionError, match="the naming step exited 3"):
        named_by_running(read(WORKFLOW), REPOSITORY, work)
    assert (work / "tr-was-here").exists(), "the shell's folder is not the one given"
    assert not (work / "github_output").exists(), "a later command ran after the first had failed"


@NO_SHELL
def test_p3b_companion_the_callers_shell_settings_do_not_reach_the_shell(tmp_path, monkeypatch):
    """The second delta check's E11. The shell's environment is the one made here: a file the caller's BASH_ENV
    names, which bash would read before a script, is not read."""
    sourced = tmp_path / "sourced"
    startup = tmp_path / "startup.sh"
    startup.write_text(f'touch "{sourced.as_posix()}"\n', encoding="utf-8", newline="")
    monkeypatch.setenv("BASH_ENV", str(startup))
    output, _printed = named_by_running(read(WORKFLOW), REPOSITORY, tmp_path)
    assert output == [f"image={IMAGE}"]
    assert not sourced.exists(), "the caller's BASH_ENV file was read by the test's shell"


# --- P4: the companions ------------------------------------------------------------------------------------------

def where(problem: str) -> str:
    """The file a problem names: what stands before its first colon."""
    return problem.partition(": ")[0]


def swapped(text: str, old: str, new: str, count: "int | None" = None) -> str:
    assert old in text, f"the plant's anchor is not in the file: {old!r}"
    return text.replace(old, new) if count is None else text.replace(old, new, count)


def default_of(image: str) -> str:
    return "${NEXUS_IMAGE:-" + image + "}"


def test_p4_companions_an_image_default_left_elsewhere_is_named_file_by_file():
    compose, cloud, env_example, deploy_doc = the_four()
    elsewhere = "registry.example/other/console"
    ours, theirs = default_of(IMAGE), default_of(elsewhere)
    one = image_default_problems(swapped(compose, ours, theirs, 1), cloud, env_example, deploy_doc)
    assert one == [f"docker-compose.yml: an image default is {elsewhere!r}, not {IMAGE!r}"], one
    two = image_default_problems(compose, swapped(cloud, ours, theirs), env_example, deploy_doc)
    assert [where(p) for p in two] == ["deploy/cloud-init.yaml"] * 2, two
    assert all("an image default is" in p for p in two), two
    comment = swapped(compose, f"(the image; {IMAGE}:latest)", f"(the image; {elsewhere}:latest)")
    said = image_default_problems(comment, cloud, env_example, deploy_doc)
    assert [where(p) for p in said] == ["docker-compose.yml"] and "the comment names the default" in said[0], said
    line = swapped(env_example, f"NEXUS_IMAGE={IMAGE}", f"NEXUS_IMAGE={elsewhere}")
    three = image_default_problems(compose, cloud, line, deploy_doc)
    assert [where(p) for p in three] == [".env.example"], three
    sentence = swapped(deploy_doc, f"`{IMAGE}:latest`", f"`{elsewhere}:latest`")
    four = image_default_problems(compose, cloud, env_example, sentence)
    assert [where(p) for p in four] == ["docs/DEPLOY.md"], four
    gone = image_default_problems(swapped(compose, ours, "nexus-local"), cloud, env_example, deploy_doc)
    assert gone == ["docker-compose.yml: 0 image default(s) read; the compose file states two"], gone


def test_p4_companions_the_label_and_the_contact_at_another_address_are_named():
    dockerfile, seeder = read("deploy/Dockerfile"), read("backend/kb/seeder.py")
    other = "https://code.example/someone/else"
    label = repository_problems(swapped(dockerfile, REPOSITORY_URL, other), seeder)
    assert label == [f"deploy/Dockerfile: the image's source label is ['{other}'], not {REPOSITORY_URL}"], label
    contact = repository_problems(dockerfile, swapped(seeder, REPOSITORY_URL, other))
    assert contact == [f"backend/kb/seeder.py: the User-Agent's contact is ['{other}'], not {REPOSITORY_URL}"], contact
    none = repository_problems(dockerfile, swapped(seeder, f"({REPOSITORY_URL}; ", "("))
    assert [where(p) for p in none] == ["backend/kb/seeder.py"], none


LAST_PUSH = "          docker push " + NAMED + ':latest"\n'
BY_THE_OWNER = ('owner=$(echo "${{ github.repository_owner }}" | ' + LOWERED + ')\n'
                '          image="ghcr.io/$owner/console"')

# (what is planted, the text it replaces, what replaces it, how many times)
ALTERED = [
    ("the owner read from the repository and the name written out, in the naming step (the workflow before the ruling)",
     READS, BY_THE_OWNER, 1),
    ("the same in the notes step alone", READS + "\n          notes=", BY_THE_OWNER + "\n          notes=", 1),
    ("notes that give another image than the one read", "Image: $image:$RELEASE_TAG.",
     "Image: ghcr.io/someone/console:$RELEASE_TAG.", 1),
    ("what was read is not lower-cased", READS, 'image="ghcr.io/' + FROM_THE_REPOSITORY + '"', 1),
    ("lower-casing in a comment only", READS, 'image="ghcr.io/' + FROM_THE_REPOSITORY + '"  # ' + LOWERED, 1),
    ("a name of its own after the repository's", '"ghcr.io/' + FROM_THE_REPOSITORY + '"',
     '"ghcr.io/' + FROM_THE_REPOSITORY + '/console"', 1),
    ("the step's output set to another image", 'echo "image=$image" >> "$GITHUB_OUTPUT"',
     'echo "image=docker.io/someone/console" >> "$GITHUB_OUTPUT"', 1),
    ("the step's output not set at all", '          echo "image=$image" >> "$GITHUB_OUTPUT"\n', "", 1),
    ("a push of an image a job's environment names", "docker push " + NAMED + ':$RELEASE_TAG"',
     'docker push "$MIRROR:$RELEASE_TAG"', 1),
    ("a push through another registry's variable", "docker push " + NAMED + ':latest"',
     'docker push "$REG/console:latest"', 1),
    ("a third tag on the build", "-t " + NAMED + ':latest" .', "-t " + NAMED + ':latest" -t "$EXTRA" .', 1),
    ("a retag and a push elsewhere", "docker push " + NAMED + ':latest"\n',
     "docker push " + NAMED + ':latest"\n          docker tag ' + NAMED + ':latest" quay.io/someone/console:latest\n'
     "          docker push quay.io/someone/console:latest\n", 1),
    ("a copy by another tool", "docker push " + NAMED + ':latest"\n',
     "docker push " + NAMED + ':latest"\n          skopeo copy docker://a docker://quay.io/someone/console\n', 1),
    ("a push from the job that writes the release", '          mapfile -t files < <(find assets -type f | sort)\n',
     '          mapfile -t files < <(find assets -type f | sort)\n'
     '          docker push "ghcr.io/someone/console:latest"\n', 1),
    # from the first delta check's D1 onward
    ("docker image tag and docker image push after the pushes", LAST_PUSH,
     LAST_PUSH + "          docker image tag " + NAMED + ':latest" ghcr.io/someone/console:latest\n'
     "          docker image push ghcr.io/someone/console:latest\n", 1),
    ("a third tag spelled --tag", "-t " + NAMED + ':latest" .', "-t " + NAMED + ':latest" --tag "$EXTRA" .', 1),
    ("a tag glued to its option", "-t " + NAMED + ':latest" .',
     "-t " + NAMED + ':latest" -tghcr.io/someone/console .', 1),
    ("docker compose push after the pushes", LAST_PUSH, LAST_PUSH + "          docker compose push\n", 1),
    ("a step that uses an action which logs in", "      - name: Name the image\n",
     "      - uses: docker/login-action@v3\n      - name: Name the image\n", 1),
    ("a job that calls a workflow kept elsewhere", "\n  release:\n",
     "\n  mirror:\n    uses: someone/workflows/.github/workflows/push.yml@v1\n"
     "    defaults:\n      run:\n        shell: bash\n\n  release:\n", 1),
    ("another job that pulls, tags and pushes", "\n  release:\n",
     "\n  mirror:\n    runs-on: ubuntu-24.04\n    defaults:\n      run:\n        shell: bash\n    steps:\n"
     "      - name: Mirror\n        run: |\n          docker pull busybox\n"
     "          docker image tag busybox ghcr.io/someone/console:latest\n"
     "          docker image push ghcr.io/someone/console:latest\n\n  release:\n", 1),
    ("a script run after the pushes", LAST_PUSH, LAST_PUSH + '          bash deploy/mirror.sh "$RELEASE_TAG"\n', 1),
    ("a script sourced before the pushes", "          docker push " + NAMED + ':$RELEASE_TAG"\n',
     "          . deploy/names.sh\n          docker push " + NAMED + ':$RELEASE_TAG"\n', 1),
    ("a shell of its own on the naming step", "        id: name\n        run: |\n",
     "        id: name\n        shell: bash deploy/wrap.sh {0}\n        run: |\n", 1),
    ("the image job's shell a wrapper", "        shell: bash\n", "        shell: bash deploy/wrap.sh {0}\n", 1),
    ("a continuation inside the word", "          docker push " + NAMED + ':latest"\n',
     "          docker pu\\\n          sh " + NAMED + ':latest"\n', 1),
    ("a here-document of comments piped to a shell in another job",
     '        run: find assets -type f | sort | tee "$RUNNER_TEMP/assets.txt"\n',
     '        run: |\n          find assets -type f | sort | tee "$RUNNER_TEMP/assets.txt"\n'
     "          sed 's/^# //' <<'EOF' | bash\n          # docker push quay.io/someone/console:latest\n"
     "          EOF\n", 1),
    ("docker through a variable", LAST_PUSH,
     LAST_PUSH + "          d=docker; $d push quay.io/someone/console:latest\n", 1),
    ("a global option before push", "          docker push " + NAMED + ':latest"\n',
     "          docker --debug push " + NAMED + ':latest"\n', 1),
    ("a push through xargs", LAST_PUSH,
     LAST_PUSH + "          echo quay.io/someone/console:latest | xargs docker push\n", 1),
    ("a login for a host that only begins like the registry's", "docker login ghcr.io -u",
     "docker login ghcr.io.mirror.example -u", 1),
    ("the image lengthened after it was read, in the notes step", READS + "\n          notes=",
     READS + '\n          image+="/console"\n          notes=', 1),
    ("a second notes line", '          mapfile -t files < <(find assets -type f | sort)\n',
     '          notes="Image: ghcr.io/someone/console"\n'
     '          mapfile -t files < <(find assets -type f | sort)\n', 1),
    ("the release edited after it was created", '--notes "$notes" "${files[@]}"\n',
     '--notes "$notes" "${files[@]}"\n'
     '          gh release edit "$RELEASE_TAG" --notes "Image: quay.io/someone/console"\n', 1),
    ("a substitution inside the notes option", '--notes "$notes"', '--notes "${notes//talima/console}"', 1),
    ("a second image beside the one read, in the notes", "Image: $image:$RELEASE_TAG.",
     "Image: $image:$RELEASE_TAG. Also ghcr.io/someone/console.", 1),
    ("every tag pushed at once", "          docker push " + NAMED + ':latest"\n',
     "          docker image push --all-tags " + NAMED + '"\n', 1),
    ("an image named in an environment of the workflow's own", "\njobs:\n",
     "\nenv:\n  image: ghcr.io/someone/console\n\njobs:\n", 1),
    # one plant for each line of the check that the others reach only together with another
    ("the image job run inside a container of its own", "    name: image (ghcr.io)\n",
     "    name: image (ghcr.io)\n    container: someone/console:latest\n", 1),
    ("the release announced again from another job", "\n  release:\n",
     "\n  announce:\n    runs-on: ubuntu-24.04\n    defaults:\n      run:\n        shell: bash\n    steps:\n"
     '      - name: Announce\n        run: gh release edit "$RELEASE_TAG" --notes "see the mirror"\n\n  release:\n', 1),
    ("a shell of its own on a step of another job", "      - name: The assets\n",
     "      - name: The assets\n        shell: bash deploy/wrap.sh {0}\n", 1),
    ("an action that is not one of the four, in another job", "      - name: The assets\n",
     "      - uses: someone/push-action@v3\n      - name: The assets\n", 1),
    # the second delta check's E1, E3 and E8, and one plant for each line that the others reach only together
    ("docker spelled across a continued line in another job",
     '        run: find assets -type f | sort | tee "$RUNNER_TEMP/assets.txt"\n',
     '        run: |\n          find assets -type f | sort | tee "$RUNNER_TEMP/assets.txt"\n'
     '          dock\\\n          er push "$MIRROR"\n', 1),
    ("a continued line where a space was: bash joins the two words", "          docker push " + NAMED + ':latest"\n',
     "          docker\\\n          push " + NAMED + ':latest"\n', 1),
    ("an environment name of its own on the image job", "    env:\n      RELEASE_TAG: ${{ needs.tag.outputs.tag }}\n"
     "      DRYRUN:", "    env:\n      RELEASE_TAG: ${{ needs.tag.outputs.tag }}\n      MIRROR: registry.example/someone/"
     "console\n      DRYRUN:", 1),
    ("a key of its own on the step that creates the release: another repository for gh",
     "      - name: Create the release with the bundles as assets\n        if: env.DRYRUN == 'false'\n",
     "      - name: Create the release with the bundles as assets\n        if: env.DRYRUN == 'false'\n"
     "        env:\n          GH_REPO: someone/elsewhere\n", 1),
    ("a dry run that would push: the image job reads its flag from somewhere else",
     "      DRYRUN: ${{ needs.tag.outputs.dryrun }}\n    defaults:", "      DRYRUN: 'false'\n    defaults:", 1),
    ("the image job built before the tag is checked", "    name: image (ghcr.io)\n    needs: tag\n",
     "    name: image (ghcr.io)\n", 1),
    ("the release job's shell a wrapper", "      GH_TOKEN: ${{ github.token }}\n    defaults:\n      run:\n"
     "        shell: bash\n", "      GH_TOKEN: ${{ github.token }}\n    defaults:\n      run:\n"
     "        shell: bash deploy/wrap.sh {0}\n", 1),
    ("the release created under a spelling the list does not read", 'gh release create "$RELEASE_TAG"',
     'gh "release" create "$RELEASE_TAG"', 1),
    ("a registry's own API called from another job",
     '        run: find assets -type f | sort | tee "$RUNNER_TEMP/assets.txt"\n',
     '        run: find assets -type f | sort | tee "$RUNNER_TEMP/assets.txt"; '
     'curl -sf -X PUT "https://quay.io/v2/someone/console/manifests/latest"\n', 1),
    # the third delta check's F2 and F6
    ("a dry run that would create the release: the creating step's condition dropped",
     "      - name: Create the release with the bundles as assets\n        if: env.DRYRUN == 'false'\n",
     "      - name: Create the release with the bundles as assets\n", 1),
    ("the step that creates the release under another name",
     "      - name: Create the release with the bundles as assets\n", "      - name: Create the release\n", 1),
    ("a registry's host in capitals (a host's name is the same in either case)",
     '        run: find assets -type f | sort | tee "$RUNNER_TEMP/assets.txt"\n',
     '        run: find assets -type f | sort | tee "$RUNNER_TEMP/assets.txt"; '
     'curl -sf -X PUT "https://QUAY.IO/v2/someone/console/manifests/latest"\n', 1),
]

@pytest.mark.parametrize("what, old, new, times", ALTERED,
                         ids=[a[0].partition(",")[0].replace("'", "").replace(" ", "-")[:60] for a in ALTERED])
def test_p4_companions_an_altered_workflow_is_named_by_the_reading_or_the_list_alone(what, old, new, times):
    workflow = read(WORKFLOW)
    assert workflow.count(old) >= times, f"the plant's anchor is not in the workflow {times} time(s): {old!r}"
    altered = workflow.replace(old, new, times)
    assert altered != workflow and isinstance(yaml.safe_load(altered), dict), "the plant is a workflow that parses"
    assert reading_problems(altered) != [], what


def test_p4_companion_the_image_jobs_problem_says_where_it_differs():
    """The third delta check's F7: for a difference outside the steps the message named a seventh step."""
    workflow = read(WORKFLOW)
    outside = reading_problems(swapped(workflow, "    env:\n      RELEASE_TAG: ${{ needs.tag.outputs.tag }}\n      DRYRUN:",
                                       "    env:\n      RELEASE_TAG: ${{ needs.tag.outputs.tag }}\n      MIRROR: x\n"
                                       "      DRYRUN:", 1))
    assert len(outside) == 1 and outside[0].endswith("it differs in ['env']"), outside
    inside = reading_problems(swapped(workflow, LAST_PUSH, LAST_PUSH + "          docker compose push\n", 1))
    assert len(inside) == 1 and "it differs in ['steps']; it has 6 step(s) for the 6 held, and the first that " \
        "differs is step 5: {'name': 'Push'" in inside[0], inside
    last = reading_problems(swapped(workflow, 'echo "dry run ($RELEASE_TAG): the image was built',
                                    'echo "dry run: the image was built', 1))
    assert len(last) == 1 and "the first that differs is step 6: {'name': 'Dry run'" in last[0], last
    start = workflow.index("      - name: Dry run\n")
    gone = reading_problems(workflow[:start] + workflow[workflow.index("\n  bundles:\n", start):])
    assert len(gone) == 1 and gone[0].endswith("it has 5 step(s) for the 6 held, and the first that differs is "
                                               "step 6: missing"), gone


def test_p4_companion_a_release_job_that_does_not_hold_the_creating_step_once_is_named_by_the_count_alone():
    """No step that creates the release as held; two of them; and one that creates it through the API with notes
    of its own, which neither says `gh release` nor names a tool or a host of the list."""
    workflow = read(WORKFLOW)
    start = workflow.index("      - name: " + CREATES["name"] + "\n")
    end = workflow.index("      - name: Dry run\n", start)
    through_the_api = ("      - name: " + CREATES["name"] + "\n        if: " + PUBLISHING + "\n        run: |\n"
                       '          gh api -X POST "repos/$GITHUB_REPOSITORY/releases" -f tag_name="$RELEASE_TAG" '
                       '-f body="Image: registry.example/someone/console"\n')
    for count, altered in ((0, workflow[:start] + workflow[end:]),
                           (2, workflow[:end] + workflow[start:end] + workflow[end:]),
                           (0, workflow[:start] + through_the_api + workflow[end:])):
        assert isinstance(yaml.safe_load(altered), dict), "the plant is a workflow that parses"
        named = reading_problems(altered)
        assert len(named) == 1 and named[0].startswith(f"{count} step(s) of the release job are the one held"), named


# (what is planted, the text it replaces, what replaces it): edits the reading and the list do not see
QUIET = [
    ("a comment", "# The release (RUN_WORKLIST.md W3;", "# The release (RUN_WORKLIST.md W4;"),
    ("a tool the list does not name, in another job",
     '        run: find assets -type f | sort | tee "$RUNNER_TEMP/assets.txt"\n',
     '        run: find assets -type f | sort | tee "$RUNNER_TEMP/assets.txt"; '
     'buildctl build --output type=image,name=registry.example/someone/console,push=true .\n'),
    ("a name of its own in the release job's environment", "      GH_TOKEN: ${{ github.token }}\n",
     "      GH_TOKEN: ${{ github.token }}\n      GH_REPO: someone/elsewhere\n"),
    ("a key written twice, of which YAML keeps the last", "      - name: Push\n        if: env.DRYRUN == 'false'\n",
     "      - name: Push\n        run: docker push registry.example/someone/console\n"
     "        if: env.DRYRUN == 'false'\n"),
]


@pytest.mark.parametrize("what, old, new", QUIET, ids=[q[0].partition(",")[0].replace(" ", "-") for q in QUIET])
def test_p4_companions_an_edit_the_reading_does_not_see_is_stopped_by_the_texts_hash(what, old, new):
    altered = swapped(read(WORKFLOW), old, new, 1)
    assert isinstance(yaml.safe_load(altered), dict), "the plant is a workflow that parses"
    assert reading_problems(altered) == [], f"{what}: the reading or the list names it; it belongs with the altered workflows"
    named = text_problems(altered)
    assert len(named) == 1 and "is not the text held here" in named[0], what


def test_p4_companion_the_text_is_held_line_ends_aside(tmp_path):
    """A checkout that wrote CRLF line ends (git's autocrlf on Windows) holds the same text."""
    workflow = read(WORKFLOW)
    copy = tmp_path / "release.yml"
    copy.write_bytes(workflow.replace("\n", "\r\n").encode("utf-8"))
    assert copy.read_bytes().count(b"\r\n") == workflow.count("\n") > 200
    assert text_problems(read_text(copy)) == [] and reading_problems(read_text(copy)) == []
