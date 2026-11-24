"""W3 part 5 — CI's deploy smoke, the release and deploy workflows, and
docs/DEPLOY.md (run/specs/W3.md S2, D7; RUN_WORKLIST W3 items 8 and 9).

C1  actionlint over every tracked workflow, where it is on PATH (skipped by
    name otherwise; CI's ubuntu runner and the run's sandbox ran it).
C2  deploy-smoke.yml: every job pinned to ubuntu-24.04 (the cloud-init
    target's release; ubuntu-latest is migrating), the smoke builds the
    image, brings its profile up (a matrix: stub, and since W3b part 5
    hosted, each by its own name) and runs scripts/verify_deploy.py,
    tears down with `if: always()`, and is staged: a plan job over the
    push's diff, always on [ci full] and dispatch.
C3  release.yml: dispatched with a required tag, the release's own (since
    Amendment 7 a tag push starts nothing, C18); the registry login,
    the push and the release creation are each gated on the dry-run flag,
    which derives from the tag's `-dryrun` suffix, so a dry-run tag builds
    everything and publishes nothing; the image name is lowercased for
    GHCR; only first-party actions.
C4  deploy.yml: dispatch only; the three secrets read into job-level env
    (secrets are not allowed in `if:`), every step that reaches the host
    gated on them, one no-op step that runs when they are unset and says
    so; no `secrets.` in any `if:`.
C5  docs/DEPLOY.md: the profiles, the FILL values, the backup and restore
    commands as the compose header gives them, the egress policy, the
    dry-run tag, the key rotation.
C6  Companions: a planted ungated push step, a planted host step without
    the env gate, a planted `secrets.` in an `if:`, a smoke job on
    ubuntu-latest, and a planted root-only command run bare (in an option
    order the rule's source does not spell) are each named by the rules;
    none of these names appears in the rules' source.
C7  Every tracked workflow's `run:` steps: a command that needs root on a
    GitHub-hosted runner (`install -o`/`-g`, `chown`, `useradd`, `apt-get
    install`, `systemctl`) runs under `sudo`, because the runner is the
    `runner` user, not root. Found by CI's first deploy-smoke run: the
    backups directory's `install -d -o 10001` was refused there where the
    sandbox's proof had run as root.
C8  No workflow prints a verdict of its own: a `run:` line that echoes or
    prints `PASS`, `FAIL` or `SKIP` is named, because the verdicts are
    scripts/verify_deploy.py's, whose rules are tested (B6), and a verdict
    a workflow composes by hand is not (the W3 verifier's F1: deploy.yml's
    host egress probe printed PASS on any OSError, a timeout and a refused
    connect included). The companion plants one through a verb the defect
    did not use.
C9  deploy.yml's profile choice is exactly the profiles compose's NEXUS
    services run and scripts/verify_deploy.py checks, both derived (W3b's
    close: `hosted` was runnable, smoked and verified, and the manual
    deploy could not offer it). The companions drop the list's last name,
    add an option `gpu`, and plant a compose service under `gpu`. Since W4
    part 3 a verifier entry is a pattern (under hosted, `nexus-t*`: one
    service per tester, read from the compose file when the verifier runs),
    and a NEXUS service compose runs under a profile that the profile's
    pattern does not match is named (its companion: `nexus-observer` under
    hosted).

The operator's instruction of 11 November (records-only pushes start no
workflow; the windows suite in parallel shards with the same total; neither
may alter what an item's closing [ci full] run covers):

C10 and C11 are retired (Amendment 7, 17 November), and C15 below: each
    pinned how a push trigger's path and branch filters decide, and no
    workflow has a push trigger now (C18). The two real pushes that
    calibrated C11's model are in RUN_LOG.md; the model went with the
    filter it modelled.
C12 The windows shards, over the parsed ci.yml: the shard indices are
    exactly 1..K, and K is the number in the job's name and in every call
    of scripts/ci_shard.py; the job is windows, unconditional and not
    fail-fast; every step of the unsharded suite job but its run and its
    counts line is in the shard job word for word and in order; the split
    collects the unsharded target and hands the split its output; the run
    is the unsharded job's run with the shard's files for its target (the
    same flags, the same tee); the count check runs always, on the split's
    file and the run's output; the shard's file is kept under a name per
    shard; the total job needs the shards, runs always, takes their files
    and is called with K; no job but the shards runs pytest on windows.
    Companions: a shard index missing, the count check gone, a total that
    does not need the shards, windows back in the unsharded matrix, a flag
    dropped from the shard's run, a sentinel dropped, a K that disagrees, a
    second windows job running pytest under another name.
    W4 part 4 (run/specs/W4.md D23), a recorded change: the rule read
    "windows is in no other job", and its reason was that beside the shards
    the suite would run twice. The simulated UAT runs on windows and runs a
    script, not the suite, so the rule is now what its reason says: no job
    but the shards RUNS PYTEST on windows (any word of a `run:` line that is
    pytest's own command or module, through any interpreter). A windows job
    that runs no pytest is allowed, and the base's rule refused it (that
    test, run on the base, is the change's failing-first evidence).
C13 What an item's closing run covers, read from the three workflows for
    a push whose head commit is marked [ci full] and for a dispatch: the
    suite unsharded on ubuntu and macos, every windows shard, the Postgres
    job, both bundles, both deploy-smoke profiles, and (W4 part 4) the
    simulated UAT on ubuntu, windows and macos (a script, never pytest)
    with its Postgres leg on ubuntu beside the suite's Postgres service.
    The two plan steps are EXECUTED (their own Python, as the runner runs
    it) for a push that changed none of their files, so the mark is what
    decides; unmarked, the same push plans nothing and the UAT runs staged
    (ubuntu and windows), which is the control. Companions: macos dropped
    from the suite's full matrix, a bundle plan that drops macos, a smoke
    that drops a profile, macos dropped from the UAT's full matrix,
    windows dropped from its staged one, and the UAT's Postgres leg moved
    off the one system that has the service. Since Amendment 7 a push
    starts nothing (C18) and the dispatch is the one run; the push cases
    still exercise the plan steps' own branches.

The operator's Amendment 6 of 16 November, clause 6 (economy: the Postgres
suite where a store changed, and no run nobody will read; the close's
coverage unchanged):

C16 The Postgres suite runs on a head commit marked [ci full], on a manual
    dispatch, and on a push that changed a file under backend/store/,
    backend/kb/ or backend/memory/; on any other push it is skipped by
    name. ci.yml's plan step is EXECUTED for each case in a scratch
    repository whose head commit changed exactly the case's files, and the
    job is read to need the plan and to run on its word alone. With no
    previous head to compare, it runs. Companions: a watched directory
    dropped, one matched without its slash, a plan that ignores the mark,
    the job's condition removed, one that reads another output, and the
    job no longer needing the plan are each named. C13 holds what a closing
    run covers, the Postgres suite included.
C17 A run that is not a closing run shares one concurrency group with its
    branch and cancels the branch's earlier run; a head commit marked
    [ci full] and a manual dispatch each get a group of their own, cancel
    nothing and are cancelled by nothing. In ci.yml only: bundle.yml and
    deploy-smoke.yml keep one group per commit, never cancelled.
    Companions: a block that cancels every run, the earlier per-commit
    block, a closing condition that leaves a dispatch cancellable, the
    negation dropped, and the cancelling block on the bundle are each named.

The operator's instruction of 15 November (W4b's close: give the deploy
smoke a timeout that fails it by name instead of running on):

C14 No job runs on. Every job of every tracked workflow declares a
    `timeout-minutes` of its own, a plain number of at most 90: a job with
    none runs for GitHub's default, 360 minutes, and two had none (the
    plan jobs of bundle.yml and deploy-smoke.yml). In deploy-smoke.yml
    every step of both jobs declares one too, of at most 15, and each
    job's own is at most 30: the smoke takes about three minutes, so a
    step that hangs fails as that step, by its name, within a quarter of
    an hour, where the job's bound alone ended it after 50 as a
    cancellation that names no step. What each kind of timeout reports was
    measured on GitHub by two probes (ci runs 86 and 87 on
    run/wip/W4b-timeout-probe, 15 November: a step's bound gave "The action
    'A step that hangs' has timed out after 1 minutes", the step and the
    job `failure`, and the steps under failure() and always() ran; the
    job's bound alone gave "The operation was canceled", the job
    `cancelled`, the step under failure() skipped, and a step under
    failure() || cancelled() ran). No test here can stand in for those
    runs. Companions, planted in the workflow the
    instruction names and never through the rule's own list: every job of
    every workflow without its bound; a job nobody listed; a bound that is
    an expression, zero, negative, a boolean, a fraction or a string; a
    bound at 360; every smoke step without its bound, named by its own
    label; a new step named by its name, its id, its action or its
    position; a step over the step limit; and each smoke job over the job
    limit. The pre-change test takes the bounds out again and counts what
    the rule names: every step and both jobs.

The operator's Amendment 7 of 17 November (the rest is built without
GitHub Actions; a run happens only when started by hand):

C18 No tracked workflow triggers on push: every workflow's triggers are
    exactly workflow_dispatch, an allow-list of one event, so a push to a
    branch or of a tag, a pull request, a schedule or another run's end
    is each named without the rule listing it. release.yml's `v*` tag
    trigger is a push trigger and went too: it built the bundles on
    macos, which clause 4 allows once, by dispatch, at W11. Companions:
    ci.yml's own push trigger restored, a pull request, the release's
    tag, a schedule, a workflow_run, a merge queue, the list and string
    forms of `on:`, a workflow with no dispatch and one nobody has
    written yet are each named, and the rule's strings name none of
    their events (by AST).
"""
from __future__ import annotations

import ast
import fnmatch
import inspect
import json
import os
import re
import shlex
import shutil
from tests import _proc
import sys
from pathlib import Path

import pytest
import yaml

from tests._tracked import REPO, tracked

WORKFLOWS = REPO / ".github" / "workflows"
DEPLOY_MD = REPO / "docs" / "DEPLOY.md"
FIRST_PARTY = ("actions/checkout@", "actions/setup-python@", "actions/upload-artifact@", "actions/download-artifact@")
HOST_WORDS = ("ssh ", "scp ")
PUBLISH_WORDS = ("docker login", "docker push", "gh release create")
# A command line that needs root on a GitHub-hosted runner: `install` with
# an owner or group option (in any option order), `chown`, `useradd`,
# `apt-get install`, `systemctl`.
ROOT_COMMAND = re.compile(r"^(?:install\s+(?:\S+\s+)*?-[og]\s|chown\s|useradd\s|apt-get\s+install\b|systemctl\s)")
# A verdict printed by a workflow's own shell or inline Python: the verdict
# words the verifier prints, composed by hand.
VERDICT_PRINT = re.compile(r"(?:\becho|\bprintf|\bprint\()\s*\\?[\"']?(?:PASS|FAIL|SKIP)\b")


def workflow(name: str) -> dict:
    path = WORKFLOWS / name
    assert path.is_file(), f"{path} is missing"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def steps(job: dict) -> list[dict]:
    return job.get("steps") or []


def step_text(step: dict) -> str:
    return str(step.get("run") or "")


# --- the rules (functions over the parsed workflow, so the companions can plant) ---

def smoke_problems(wf: dict) -> list[str]:
    problems: list[str] = []
    jobs = wf.get("jobs") or {}
    for name, job in jobs.items():
        if job.get("runs-on") != "ubuntu-24.04":
            problems.append(f"job {name} runs on {job.get('runs-on')!r}, not the pinned ubuntu-24.04")
    smoke = next((j for n, j in jobs.items() if n != "plan"), None)
    if smoke is None:
        problems.append("no smoke job")
        return problems
    texts = [step_text(s) for s in steps(smoke)]
    if not any("docker build" in t and "deploy/Dockerfile" in t for t in texts):
        problems.append("the smoke does not build the image from deploy/Dockerfile")
    # W3b part 5 (D12): one job per profile, the stub profile (SQLite) and the
    # hosted one (Postgres), each brought up and verified by its own name.
    profiles = ((smoke.get("strategy") or {}).get("matrix") or {}).get("profile") or []
    if not {"stub", "hosted"} <= set(profiles):
        problems.append(f"the smoke's profile matrix is {profiles}, not the stub and hosted profiles")
    if str((smoke.get("env") or {}).get("PROFILE", "")).replace(" ", "") != "${{matrix.profile}}":
        problems.append("the smoke's PROFILE is not the matrix's profile")
    if not any('--profile "$PROFILE" up' in t for t in texts):
        problems.append("the smoke does not bring its profile up")
    if not any("scripts/verify_deploy.py" in t and '--profile "$PROFILE"' in t for t in texts):
        problems.append("the smoke does not run scripts/verify_deploy.py on its profile")
    downs = [s for s in steps(smoke) if "down" in step_text(s) and "compose" in step_text(s)]
    if not downs or not all(str(s.get("if", "")).strip() == "always()" for s in downs):
        problems.append("the stack is not torn down under `if: always()`")
    if "plan" not in jobs or smoke.get("needs") != "plan" or "needs.plan.outputs" not in str(smoke.get("if", "")):
        problems.append("the smoke is not staged behind a plan job")
    plan_text = " ".join(step_text(s) for s in steps(jobs.get("plan", {})))
    for watched in ("deploy/", "docker-compose.yml", "scripts/verify_deploy.py", "backend/", "requirements.txt"):
        if watched not in plan_text:
            problems.append(f"the plan does not watch {watched}")
    if "[ci full]" not in plan_text or "workflow_dispatch" not in plan_text:
        problems.append("the plan does not run on [ci full] and dispatch regardless of the diff")
    if BY_HAND not in triggers(wf):
        problems.append(f"triggers are {sorted(triggers(wf), key=str)}; {BY_HAND} is not among them")
    return problems


def release_problems(wf: dict) -> list[str]:
    problems: list[str] = []
    inputs = (triggers(wf).get(BY_HAND) or {}).get("inputs") or {}
    tag = inputs.get("tag") or {}
    if tag.get("required") is not True or tag.get("type") != "string":
        problems.append(f"the release is not dispatched with a required tag: inputs={inputs}")
    if "inputs.tag" not in str((wf.get("env") or {}).get("RELEASE_TAG", "")):
        problems.append("the release's tag is not the dispatch's input")
    perms = wf.get("permissions") or {}
    if perms.get("packages") != "write" or perms.get("contents") != "write":
        problems.append(f"permissions are {perms}, not packages and contents write")
    for name, job in (wf.get("jobs") or {}).items():
        dryrun_env = str((job.get("env") or {}).get("DRYRUN", ""))
        for step in steps(job):
            text = step_text(step)
            if any(w in text for w in PUBLISH_WORDS):
                if str(step.get("if", "")).replace(" ", "") != "env.DRYRUN!='true'":
                    problems.append(f"{name}: the step {step.get('name')!r} publishes without the dry-run gate")
                if "-dryrun" not in dryrun_env:
                    problems.append(f"{name}: DRYRUN does not derive from the tag's -dryrun suffix: {dryrun_env!r}")
            for word in ("uses",):
                uses = str(step.get(word) or "")
                if uses and not uses.startswith(FIRST_PARTY):
                    problems.append(f"{name}: a third-party action {uses}")
        if name == "image" and not any("tr '[:upper:]' '[:lower:]'" in step_text(s) for s in steps(job)):
            problems.append("the image name is not lowercased for GHCR")
    problems += verifier_problems(wf)
    return problems


VERIFIER_SCRIPT = "scripts/nexus_verify.py"
VERIFIER_SYSTEMS = {"ubuntu-24.04", "windows-latest", "macos-latest"}


def verifier_problems(wf: dict) -> list[str]:
    """W9 (run/specs/W9.md D6): the release builds the standalone verifier
    into a binary on each system a stranger may run it on, runs it, keeps it
    as an artifact, and the release waits for it."""
    jobs = wf.get("jobs") or {}
    building = [name for name, job in jobs.items()
                if any(VERIFIER_SCRIPT in step_text(s) and "PyInstaller" in step_text(s) for s in steps(job))]
    if len(building) != 1:
        return [f"{len(building)} jobs build {VERIFIER_SCRIPT} into a binary, not one"]
    name, job = building[0], jobs[building[0]]
    problems = []
    systems = set(((job.get("strategy") or {}).get("matrix") or {}).get("os") or [])
    if systems != VERIFIER_SYSTEMS:
        problems.append(f"{name}: the verifier is built on {sorted(systems)}, not {sorted(VERIFIER_SYSTEMS)}")
    built = ("nexus-verify", "${{ matrix.asset }}")      # the asset names all begin nexus-verify- (below)
    if not any("--help" in step_text(s) and any(b in step_text(s) for b in built) for s in steps(job)):
        problems.append(f"{name}: the built binary is never run")
    # A release takes one asset per file name, and the Linux and macOS builds are both `nexus-verify` unless
    # each system names its own: the matrix gives each one, the build uses it, and the names differ.
    include = ((job.get("strategy") or {}).get("matrix") or {}).get("include") or []
    assets = {i.get("os"): str(i.get("asset", "")) for i in include if isinstance(i, dict)}
    if (set(assets) != VERIFIER_SYSTEMS or len(set(assets.values())) != len(assets)
            or not all(a.startswith("nexus-verify-") for a in assets.values())):
        problems.append(f"{name}: each system's binary needs a release asset name of its own: {assets}")
    elif not any("PyInstaller" in step_text(s) and "--name \"${{ matrix.asset }}\"" in step_text(s)
                 for s in steps(job)):
        problems.append(f"{name}: the binary is not built under its system's asset name")
    if not any(str(s.get("uses", "")).startswith("actions/upload-artifact@")
               and any(b in str((s.get("with") or {}).get("name", "")) for b in built) for s in steps(job)):
        problems.append(f"{name}: the binary is not kept as an artifact")
    if name not in ((jobs.get("release") or {}).get("needs") or []):
        problems.append(f"the release does not wait for {name}")
    return problems


TALIMA_RUN = re.compile(r'^\./dist/\$\{\{ matrix\.talima \}\} --help > (\S+) && grep -q "\^usage: talima-verify" \1 \|\| exit 1$')
NEXUS_RUN = re.compile(r'^\./dist/\$\{\{ matrix\.asset \}\} --help > (\S+) && grep -q "\^usage: nexus-verify" \1 \|\| exit 1$')


def talima_problems(wf: dict) -> list[str]:
    """W11b (run/specs/W11b.md D4): the verifier job also builds the same script under the standard's name,
    `talima-verify-<system>`, one name per system, runs it, and keeps it with the release's assets. Read line by
    line, not step by step: the line that builds under the talima name starts with `python -m PyInstaller` (never a
    comment, an `echo` or a `true ||`) and ends in the verifier's own script (the wrapper `scripts/talima_verify.py`
    cannot find its sibling inside a binary); and the run step's two lines, nexus-verify's and talima-verify's, each
    start with the binary itself, grep its help for its own name and end in `|| exit 1`, so each line fails its step
    when its binary or its name check fails (the first command of an `&&` list does not trip errexit, and the step's
    status is its last line's). The run lines are pinned whole, as `release.yml` writes them, and their step is not:
    an `exit 0` before them, an `if:` that skips the step, the lines inside a no-op such as `: '...'`, or
    `continue-on-error` on the step is not seen. The build line is read at three points
    only, its start, its `--name` and its last word, so a build line that runs and builds nothing (`--help` among its
    options, an `exit 0` before it) is not seen. A correct release written another way (an option after the script,
    a continuation line, a quoted path, a pipe, a single-quoted pattern, a comment after the command, an explicit
    `.exe`) is refused, and the rule is edited with it."""
    jobs = wf.get("jobs") or {}
    building = [name for name, job in jobs.items()
                if any(VERIFIER_SCRIPT in step_text(s) and "PyInstaller" in step_text(s) for s in steps(job))]
    if len(building) != 1:
        return [f"{len(building)} jobs build {VERIFIER_SCRIPT} into a binary, not one"]
    name, job = building[0], jobs[building[0]]
    include = ((job.get("strategy") or {}).get("matrix") or {}).get("include") or []
    names = {i.get("os"): str(i.get("talima", "")) for i in include if isinstance(i, dict)}
    if (set(names) != VERIFIER_SYSTEMS or len(set(names.values())) != len(names)
            or not all(n.startswith("talima-verify-") for n in names.values())):
        return [f"{name}: each system needs a talima-verify name of its own: {names}"]
    problems = []
    lines = [line.strip() for s in steps(job) for line in str(s.get("run") or "").split("\n")]
    built = [line for line in lines if "PyInstaller" in line and '--name "${{ matrix.talima }}"' in line]
    if not built or any(not line.startswith("python -m PyInstaller ") or line.split()[-1] != VERIFIER_SCRIPT
                        for line in built):
        problems.append(f"{name}: talima-verify is not built from {VERIFIER_SCRIPT} under its system's name")
    if not any(TALIMA_RUN.match(line) for line in lines):
        problems.append(f"{name}: no talima-verify run line in the pinned shape (its help grepped for its own name, "
                        f"then || exit 1)")
    if not any(NEXUS_RUN.match(line) for line in lines):
        problems.append(f"{name}: no nexus-verify run line in the pinned shape (its help grepped for its own name, "
                        f"then || exit 1)")
    if not any(str(s.get("uses", "")).startswith("actions/upload-artifact@")
               and "${{ matrix.talima }}" in str((s.get("with") or {}).get("path", "")) for s in steps(job)):
        problems.append(f"{name}: the talima-verify binary is not kept with the release's assets")
    return problems


INTERCEPT_ENTRY = "backend/intercept/__main__.py"
INTERCEPT_HELP = re.compile(r'^\./dist/\$\{\{ matrix\.asset \}\} --help > (\S+) && grep -q "\^usage: talima-intercept" \1 \|\| exit 1$')
INTERCEPT_VERIFY = re.compile(r'^NEXUS_INTERCEPT_UPSTREAM=http://127\.0\.0\.1:9 NEXUS_INTERCEPT_DATA_ROOT="\$RUNNER_TEMP/talima-intercept" '
                              r'\./dist/\$\{\{ matrix\.asset \}\} verify > (\S+) && grep -q \'"ok": true\' \1 \|\| exit 1$')
INTERCEPT_OWN = 'grep -q "\'backend.intercept.recorder\'" "build/${{ matrix.asset }}/PYZ-00.toc" || exit 1'
INTERCEPT_NO_ENGINE = 'python tests/_archive_reach.py "build/${{ matrix.asset }}/PYZ-00.toc" || exit 1'


def interceptor_problems(wf: dict) -> list[str]:
    """W10c (run/specs/W10c.md S9): the release builds the interceptor into a binary, `talima-intercept-<system>`,
    on each system, from its own entry module; runs it under its own name; has it open a store of its own and verify
    it; reads the archive PyInstaller built for the interceptor's own module (so the reading is of something) and for
    any module of the memory engine (which fails the job); keeps it; and waits for it. Read line by line, as the
    verifier's is: each run line is pinned whole and ends in `|| exit 1`, so it fails its step; the build line starts
    with `python -m PyInstaller`, names the system's asset, puts the checkout on its path and ends in the entry
    module. What is not seen is what `talima_problems` does not see either: a step skipped by an `if:`, an `exit 0`
    before the lines, `continue-on-error`."""
    jobs = wf.get("jobs") or {}
    building = [name for name, job in jobs.items()
                if any(INTERCEPT_ENTRY in step_text(s) and "PyInstaller" in step_text(s) for s in steps(job))]
    if len(building) != 1:
        return [f"{len(building)} jobs build {INTERCEPT_ENTRY} into a binary, not one"]
    name, job = building[0], jobs[building[0]]
    problems = []
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    if set(matrix.get("os") or []) != VERIFIER_SYSTEMS:
        problems.append(f"{name}: the interceptor is built on {sorted(matrix.get('os') or [])}, not {sorted(VERIFIER_SYSTEMS)}")
    assets = {i.get("os"): str(i.get("asset", "")) for i in matrix.get("include") or [] if isinstance(i, dict)}
    if (set(assets) != VERIFIER_SYSTEMS or len(set(assets.values())) != len(assets)
            or not all(a.startswith("talima-intercept-") for a in assets.values())):
        problems.append(f"{name}: each system needs a talima-intercept name of its own: {assets}")
    lines = [line.strip() for s in steps(job) for line in str(s.get("run") or "").split("\n")]
    built = [line for line in lines if "PyInstaller" in line and INTERCEPT_ENTRY in line]
    if not built or any(not line.startswith("python -m PyInstaller ") or '--name "${{ matrix.asset }}"' not in line
                        or " --paths . " not in line or line.split()[-1] != INTERCEPT_ENTRY for line in built):
        problems.append(f"{name}: talima-intercept is not built from {INTERCEPT_ENTRY} under its system's name")
    for what, found in (("run under its own name", any(INTERCEPT_HELP.match(line) for line in lines)),
                        ("made to open a store of its own and verify it", any(INTERCEPT_VERIFY.match(line) for line in lines)),
                        ("read for the interceptor's own module", INTERCEPT_OWN in lines),
                        ("read for a module of the memory engine", INTERCEPT_NO_ENGINE in lines)):
        if not found:
            problems.append(f"{name}: the binary is not {what}, in the pinned line ending || exit 1")
    if not any(str(s.get("uses", "")).startswith("actions/upload-artifact@")
               and "${{ matrix.asset }}" in str((s.get("with") or {}).get("path", "")) for s in steps(job)):
        problems.append(f"{name}: the binary is not kept as an artifact")
    if name not in ((jobs.get("release") or {}).get("needs") or []):
        problems.append(f"the release does not wait for {name}")
    return problems


def deploy_problems(wf: dict) -> list[str]:
    problems: list[str] = []
    on = wf.get("on") or wf.get(True) or {}
    if set(on) != {"workflow_dispatch"}:
        problems.append(f"deploy triggers are {sorted(on)}, not workflow_dispatch alone")
    for name, job in (wf.get("jobs") or {}).items():
        env = job.get("env") or {}
        for secret in ("DEPLOY_HOST", "DEPLOY_USER", "DEPLOY_KEY"):
            if f"secrets.{secret}" not in str(env.get(secret, "")):
                problems.append(f"{name}: {secret} is not read into job env from secrets")
        noop = [s for s in steps(job) if "NO-OP" in step_text(s)]
        if not noop or "== ''" not in str(noop[0].get("if", "")):
            problems.append(f"{name}: no no-op step gated on an unset secret")
        for step in steps(job):
            text = step_text(step)
            cond = str(step.get("if", ""))
            if any(w in text for w in HOST_WORDS) and "env.DEPLOY_HOST != ''" not in cond:
                problems.append(f"{name}: the step {step.get('name')!r} reaches the host without the env gate")
            if "secrets." in cond:
                problems.append(f"{name}: the step {step.get('name')!r} reads secrets in its `if:`")
    return problems


def deploy_profile_problems(wf: dict, compose: dict) -> list[str]:
    """The deploy's profile choice against the two sources it must equal,
    both derived and never listed here: the profiles compose's NEXUS services
    run, and the profiles scripts/verify_deploy.py checks (PROFILE_SERVICE,
    each naming a NEXUS service of its profile). A profile compose runs that
    the deploy cannot offer is a deployment no workflow reaches (W3b's close
    found `hosted` missing), and an option the verifier does not know
    deploys a stack nothing verifies."""
    from scripts import verify_deploy as vd
    services = compose.get("services") or {}
    nexus = {name: list(svc.get("profiles") or []) for name, svc in services.items() if name.startswith("nexus-")}
    runs = {p for profiles in nexus.values() for p in profiles}
    on = wf.get("on") or wf.get(True) or {}
    choice = ((on.get("workflow_dispatch") or {}).get("inputs") or {}).get("profile") or {}
    options = list(choice.get("options") or [])
    problems: list[str] = []
    if choice.get("type") != "choice":
        problems.append("the deploy's profile input is not a choice")
    if len(options) != len(set(options)):
        problems.append(f"the deploy's profile options repeat a name: {options}")
    if set(vd.PROFILE_SERVICE) != runs:
        problems.append(f"the verifier's profiles are {sorted(vd.PROFILE_SERVICE)}, compose's NEXUS services "
                        f"run {sorted(runs)}")
    for profile, service in sorted(vd.PROFILE_SERVICE.items()):
        # W4 part 3 (run/specs/W4.md D19): an entry is a pattern, because
        # under hosted the verifier reads the services from the compose file
        # (`nexus-t*`, one per tester) rather than naming one. Every compose
        # NEXUS service it matches runs under its profile, it matches one at
        # least, and no NEXUS service compose runs under that profile goes
        # unmatched (a service the verifier would never check).
        matched = sorted(n for n in nexus if fnmatch.fnmatchcase(n, service))
        if not matched or any(profile not in nexus[n] for n in matched):
            problems.append(f"the verifier's {profile!r} names {service!r}, which compose does not run under it")
        unmatched = sorted(n for n, ps in nexus.items() if profile in ps and not fnmatch.fnmatchcase(n, service))
        if unmatched:
            problems.append(f"compose runs {unmatched} under {profile!r}, which the verifier's {service!r} does not check")
    if set(options) != set(vd.PROFILE_SERVICE) | runs:
        problems.append(f"the deploy's profile options are {sorted(set(options))}, the profiles compose runs and "
                        f"the verifier checks are {sorted(set(vd.PROFILE_SERVICE) | runs)}")
    if choice.get("default") not in options:
        problems.append(f"the deploy's default profile {choice.get('default')!r} is not one of its options")
    return problems


def runner_root_problems(wf: dict) -> list[str]:
    """Steps whose shell runs a root-only command bare on the runner. A line
    inside an `ssh` command runs on the deploy host, not the runner, and is
    not judged here (the host's own `sudo` is the runbook's business); a
    continuation line of one is not recognised as such, so the rule reads
    each line on its own."""
    problems: list[str] = []
    for name, job in (wf.get("jobs") or {}).items():
        for step in steps(job):
            for line in step_text(step).splitlines():
                stripped = line.strip()
                if stripped.startswith(("sudo ", "#")) or "ssh " in stripped:
                    continue
                if ROOT_COMMAND.match(stripped):
                    problems.append(f"{name}: the step {step.get('name')!r} needs root on the runner without "
                                    f"sudo: {stripped[:80]}")
    return problems


# The three workflows a push started until Amendment 7 (their push triggers
# carried the documents filter of 11 and 16 November; C18 holds that none is
# left), still the three whose concurrency blocks C17 reads.
RECORDS_SKIPPING = ("ci.yml", "bundle.yml", "deploy-smoke.yml")
RUN_BRANCH = "claude/run-to-production"


def triggers(wf: dict) -> dict:
    """The workflow's `on:` as {event: its settings} (PyYAML reads the bare
    key `on` as True; an event with no settings is {})."""
    on = wf.get("on") or wf.get(True) or {}
    if isinstance(on, str):
        return {on: {}}
    if isinstance(on, list):
        return {event: {} for event in on}
    return {event: (spec or {}) for event, spec in on.items()}


# Amendment 7 (17 November): the rest is built without GitHub Actions, and a
# run happens only when started by hand. The rule is an allow-list of one
# event over every tracked workflow, so whatever else would start a run (a
# push to a branch or of a tag, a pull request, a schedule, another run) is
# named without being listed here. "No workflow triggers on push" is read
# over every workflow: release.yml's `v*` tag trigger is a push trigger too,
# and it built the bundles on macos, which clause 4 allows once, at W11.
BY_HAND = "workflow_dispatch"


def by_hand_problems(name: str, wf: dict) -> list[str]:
    on = triggers(wf)
    problems = [f"{name}: the {event} trigger starts a run without a hand"
                for event in sorted(on, key=str) if event != BY_HAND]
    if BY_HAND not in on:
        problems.append(f"{name}: no {BY_HAND} trigger, so nothing starts it by hand")
    return problems


# The windows shards (the instruction of 11 November). The jobs and the script are
# named here; K, the flags and the paths are read from ci.yml itself.
SUITE_JOB, SHARD_JOB, TOTAL_JOB, POSTGRES_JOB = "suite", "suite-windows", "suite-windows-total", "suite-postgres"
SHARD_SCRIPT = "scripts/ci_shard.py"
# W4 part 4 (run/specs/W4.md D23): the simulated UAT, a script run on three
# systems, and its Postgres leg, a second job because a service container
# is job-level and Linux-only.
UAT_JOB, UAT_POSTGRES_JOB = "uat-simulate", "uat-simulate-postgres"
UAT_SCRIPT = "scripts/uat_simulate.py"
# pytest as a `run:` word names it: its own command (a path or `.exe` before
# or after it changes nothing), or the module an interpreter's -m runs.
PYTEST_WORD = re.compile(r"^(?:.*[\\/])?py\.?test(?:\.exe)?$|^-mpytest$", re.IGNORECASE)
UPLOAD, DOWNLOAD = "actions/upload-artifact@", "actions/download-artifact@"
MATRIX_SHARD = "${{ matrix.shard }}"
SHARD_FILES = "${files[@]}"
SUITE_MATRIX = re.compile(r"^\$\{\{ fromJSON\(\((?P<when>.+)\) && '(?P<full>\[[^']*\])' \|\| '(?P<staged>\[[^']*\])'\) \}\}$")
FULL_WHEN = "github.event_name == 'workflow_dispatch' || contains(github.event.head_commit.message, '[ci full]')"


def command_lines(job: dict, *starts: str) -> list[tuple[int, dict, list[str]]]:
    """(step position, step, the shell words after `starts`) for every line
    of the job's `run:` steps whose first words are `starts`, in order."""
    found = []
    for position, step in enumerate(steps(job)):
        for line in step_text(step).splitlines():
            try:
                words = shlex.split(line, comments=True)
            except ValueError:                      # a line of an inline script, not a shell command line
                continue
            if words[:len(starts)] == list(starts):
                found.append((position, step, words[len(starts):]))
    return found


def options(words: list[str]) -> dict:
    return {word: words[i + 1] for i, word in enumerate(words[:-1]) if word.startswith("--")}


def after(words: list[str], marker: str) -> "str | None":
    return words[words.index(marker) + 1] if marker in words[:-1] else None


def on_runner(text) -> str:
    """A path as a step's shell spells it: the runner's temp folder is
    `$RUNNER_TEMP` in a `run:` and `${{ runner.temp }}` in a `with:`."""
    return str(text).replace("${{ runner.temp }}", "$RUNNER_TEMP")


def always(step_or_job: dict) -> bool:
    return str(step_or_job.get("if", "")).strip() == "always()"


def matrix_systems(job: dict) -> "tuple[list[str], list[str]] | None":
    """(a job's systems on a dispatch or a head commit marked [ci full], its
    systems on any other push), read from its matrix expression (the
    suite's staging expression); None when it is not of that shape."""
    matrix = ((job or {}).get("strategy") or {}).get("matrix") or {}
    m = SUITE_MATRIX.match(str(matrix.get("os", "")).strip())
    if not m or m.group("when") != FULL_WHEN:
        return None
    return json.loads(m.group("full")), json.loads(m.group("staged"))


def suite_systems(ci: dict) -> "tuple[list[str], list[str]] | None":
    """The unsharded suite's systems, full and staged (`matrix_systems`)."""
    return matrix_systems((ci.get("jobs") or {}).get(SUITE_JOB) or {})


def on_windows(job: dict) -> bool:
    return "windows" in json.dumps([job.get("runs-on"), (job.get("strategy") or {}).get("matrix")])


def runs_pytest(job: dict) -> bool:
    """Whether a line of the job's `run:` steps starts pytest, by its shell
    words: `PYTEST_WORD`, or `-m` followed by pytest."""
    for step in steps(job):
        for line in step_text(step).splitlines():
            try:
                words = shlex.split(line, comments=True)
            except ValueError:                      # a line of an inline script, not a shell command line
                continue
            if any(PYTEST_WORD.match(w) or (w == "-m" and words[i + 1:i + 2] == ["pytest"])
                   for i, w in enumerate(words)):
                return True
    return False


def shard_problems(ci: dict) -> list[str]:
    jobs = ci.get("jobs") or {}
    suite, shard, total = jobs.get(SUITE_JOB), jobs.get(SHARD_JOB), jobs.get(TOTAL_JOB)
    if not suite or not shard or not total:
        return [f"ci.yml has the jobs {sorted(jobs)}, not {SUITE_JOB}, {SHARD_JOB} and {TOTAL_JOB}"]
    problems: list[str] = []

    # The shards: 1..K, every one, on windows, on every push.
    strategy = shard.get("strategy") or {}
    indices = (strategy.get("matrix") or {}).get("shard")
    k = len(indices) if isinstance(indices, list) else 0
    if k < 2 or indices != list(range(1, k + 1)):
        problems.append(f"the shard indices are {indices}, not exactly 1..K for a K of two or more")
    if strategy.get("fail-fast") is not False:
        problems.append("the shard job is not `fail-fast: false`: one failing shard would cancel the others' counts")
    if shard.get("runs-on") != "windows-latest":
        problems.append(f"the shard job runs on {shard.get('runs-on')!r}, not windows-latest")
    if "if" in shard:
        problems.append(f"the shard job is conditional (`if: {shard['if']}`): windows would not run on every push")
    named = f"suite (windows-latest, shard {MATRIX_SHARD} of {k})"
    if shard.get("name") != named:
        problems.append(f"the shard job is named {shard.get('name')!r}, not {named!r}")

    # No job but the shards runs pytest on windows: beside them the suite
    # would run twice, or unsharded. (W4 part 4: a windows job that runs no
    # pytest, the simulated UAT's script, is not the suite.)
    # W4b (run/specs/W4b.md D5), a recorded change: the venv shards run the
    # same suite through another interpreter, held to their own rule (C12b,
    # `venv_problems`); any other windows job that runs pytest is named.
    for name, job in jobs.items():
        if name not in (SHARD_JOB, VENV_JOB) and on_windows(job) and runs_pytest(job):
            problems.append(f"windows also runs pytest in the job {name}: beside the shards the suite would run twice")
    systems = suite_systems(ci)
    if systems is None:
        problems.append("the suite job's systems are not the dispatch-or-[ci full] expression this rule reads")
    elif sorted(systems[0]) != ["macos-latest", "ubuntu-latest"] or systems[1] != ["ubuntu-latest"]:
        problems.append(f"the unsharded suite runs on {systems[0]} at a [ci full] commit and {systems[1]} on any other "
                        f"push, not ubuntu and macos, and ubuntu")

    # The unsharded job's own run, the measure of the shard's.
    suite_runs = command_lines(suite, "python", "-m", "pytest")
    if len(suite_runs) != 1:
        return problems + [f"the suite job has {len(suite_runs)} pytest lines, not one"]
    _, suite_step, suite_words = suite_runs[0]
    shared = [s for s in steps(suite) if s is not suite_step and "GITHUB_STEP_SUMMARY" not in step_text(s)]
    shard_steps = steps(shard)
    places = [next((i for i, s in enumerate(shard_steps) if s == wanted), None) for wanted in shared]
    for wanted, place in zip(shared, places):
        if place is None:
            problems.append(f"the shard job lacks the suite job's step {wanted.get('name') or wanted.get('uses')!r}, "
                            f"word for word")
    if [p for p in places if p is not None] != sorted(p for p in places if p is not None):
        problems.append("the suite job's steps are not in the suite job's order in the shard job")

    # The split: one unsharded collection, then this shard's files.
    pytests = command_lines(shard, "python", "-m", "pytest")
    splits = command_lines(shard, "python", SHARD_SCRIPT, "split")
    checks = command_lines(shard, "python", SHARD_SCRIPT, "check")
    if len(pytests) != 2 or len(splits) != 1:
        return problems + [f"the shard job has {len(pytests)} pytest lines and {len(splits)} split calls, not a "
                           f"collection, a split and a run"]
    (collect_at, _, collect), (run_at, run_step, run) = pytests
    split_at, _, split = splits[0]
    asked = options(split)
    if collect[:1] != suite_words[:1] or "--collect-only" not in collect or "-rs" not in collect:
        problems.append(f"the split's collection is `pytest {' '.join(collect)}`, not a --collect-only -rs of the "
                        f"unsharded target {suite_words[0]!r}")
    if after(collect, ">") is None or after(collect, ">") != asked.get("--collected"):
        problems.append(f"the split reads {asked.get('--collected')!r}, the collection is written to {after(collect, '>')!r}")
    if asked.get("--shards") != str(k):
        problems.append(f"the split is called with --shards {asked.get('--shards')}, the matrix holds {k} shards")
    if asked.get("--index") != MATRIX_SHARD:
        problems.append(f"the split is called with --index {asked.get('--index')!r}, not the matrix's shard")
    if not collect_at <= split_at < run_at:
        problems.append("the collection, the split and the run are not in that order")

    # The run: the unsharded job's, with the shard's files for its target.
    if run[:1] != [SHARD_FILES] or run[1:] != suite_words[1:]:
        problems.append(f"the shard runs `pytest {' '.join(run)}`; the unsharded job runs `pytest "
                        f"{' '.join(suite_words)}`: the same words after the target are required")
    reads = [words for at, _, words in command_lines(shard, "mapfile", "-t", "files", "<") if at == run_at]
    if reads != [[asked.get("--files")]]:
        problems.append(f"the shard's run does not read its files from the split's --files ({asked.get('--files')!r}): {reads}")
    if ("set -o pipefail" in step_text(suite_step)) != ("set -o pipefail" in step_text(run_step)):
        problems.append("the shard's run and the unsharded run differ on `set -o pipefail`")

    # The count check, always, on the split's file and the run's output.
    if len(checks) != 1:
        problems.append(f"the shard job has {len(checks)} count checks (`{SHARD_SCRIPT} check`), not one")
    else:
        check_at, check_step, check = checks[0]
        given = options(check)
        if check_at <= run_at or not always(check_step):
            problems.append("the count check does not run after the suite under `if: always()`")
        if given.get("--json") != asked.get("--json") or given.get("--output") != after(run, "tee"):
            problems.append(f"the count check reads {given.get('--json')!r} and {given.get('--output')!r}, not the "
                            f"split's file and the run's output")

    # The shard's file, kept under a name per shard; the total takes them all.
    uploads = [s for s in shard_steps if str(s.get("uses") or "").startswith(UPLOAD)]
    kept = (uploads[0].get("with") or {}) if len(uploads) == 1 else {}
    if len(uploads) != 1 or not always(uploads[0]) or MATRIX_SHARD not in str(kept.get("name")) \
            or on_runner(kept.get("path")) != asked.get("--json"):
        problems.append("the shard's file is not kept, always, under a name per shard")
    needs = total.get("needs")
    if SHARD_JOB not in ([needs] if isinstance(needs, str) else list(needs or [])) or not always(total):
        problems.append(f"the total job does not need {SHARD_JOB} under `if: always()`: it would not wait for the "
                        f"shards, or would not run when one fails")
    downloads = [s for s in steps(total) if str(s.get("uses") or "").startswith(DOWNLOAD)]
    taken = (downloads[0].get("with") or {}) if len(downloads) == 1 else {}
    totals = command_lines(total, "python3", SHARD_SCRIPT, "total")
    summed = options(totals[0][2]) if len(totals) == 1 else {}
    if len(downloads) != 1 or taken.get("pattern") != str(kept.get("name")).replace(MATRIX_SHARD, "*") \
            or taken.get("merge-multiple") is not True:
        problems.append("the total job does not take every shard's file into one folder")
    if len(totals) != 1 or summed.get("--shards") != str(k) or summed.get("--dir") != on_runner(taken.get("path")):
        problems.append(f"the total is called with --shards {summed.get('--shards')} over {summed.get('--dir')!r}, "
                        f"not K={k} over the folder the shards' files are taken into")
    return problems


# W4b (run/specs/W4b.md D5): the suite through a windows virtual
# environment's interpreter, as the operator's merge gate runs it, staged like
# macos (a [ci full] commit and a dispatch). Its steps are the windows shard
# job's, word for word, but for what makes it a venv run.
VENV_JOB, VENV_TOTAL_JOB = "suite-windows-venv", "suite-windows-venv-total"
VENV_PYTHON = ".venv/Scripts/python.exe"
VENV_MAKE = "python -m venv .venv"
VENV_ONLY_STEP = "The suite's interpreter is a virtual environment's launcher"
FULL_IF = "${{ " + FULL_WHEN + " }}"
FULL_TOTAL_IF = "${{ always() && (" + FULL_WHEN + ") }}"
# A shell word that starts an interpreter: python, python3, python3.11, py,
# pythonw, with or without a path or .exe.
INTERPRETER_WORD = re.compile(r"^(?:.*[\\/])?(?:python(?:w)?(?:\d+(?:\.\d+)?)?|py)(?:\.exe)?$", re.IGNORECASE)


def _venv_normalized(job: dict) -> list:
    """The venv job's steps as the windows shard job would hold them: its
    own step dropped, the venv's interpreter spelled `python`, the line that
    makes the venv dropped, the artifact named as the shards' is."""
    text = json.dumps([s for s in steps(job) if s.get("name") != VENV_ONLY_STEP])
    text = text.replace(VENV_MAKE + "\\n", "").replace(VENV_PYTHON, "python")
    return json.loads(text.replace("suite-windows-venv-shard-", "suite-windows-shard-"))


def venv_problems(ci: dict) -> list[str]:
    """C12b over the parsed ci.yml."""
    jobs = ci.get("jobs") or {}
    venv, venv_total, shard, total = (jobs.get(n) for n in (VENV_JOB, VENV_TOTAL_JOB, SHARD_JOB, TOTAL_JOB))
    if not venv or not venv_total or not shard or not total:
        return [f"ci.yml has the jobs {sorted(jobs)}, not {VENV_JOB} and {VENV_TOTAL_JOB} beside {SHARD_JOB} and {TOTAL_JOB}"]
    problems: list[str] = []
    if venv.get("runs-on") != "windows-latest":
        problems.append(f"the venv suite runs on {venv.get('runs-on')!r}, not windows-latest")
    if str(venv.get("if", "")).strip() != FULL_IF:
        problems.append(f"the venv suite runs `if: {venv.get('if')}`, not at a [ci full] commit and on a dispatch")
    if (venv.get("strategy") or {}) != (shard.get("strategy") or {}):
        problems.append("the venv suite's shards and their strategy are not the windows shards'")
    named = str(shard.get("name", "")).replace("suite (windows-latest, ", "suite (windows-latest, venv, ", 1)
    if venv.get("name") != named:
        problems.append(f"the venv suite is named {venv.get('name')!r}, not {named!r}")
    for step in steps(venv):
        for line in step_text(step).splitlines():
            try:
                words = shlex.split(line, comments=True)
            except ValueError:
                continue
            if words and INTERPRETER_WORD.match(words[0]) and words[0] != VENV_PYTHON and line.strip() != VENV_MAKE:
                problems.append(f"the venv suite starts {words[0]!r}, not {VENV_PYTHON}: `{line.strip()}`")
    install = next((s for s in steps(venv) if s.get("name") == "Install from requirements.txt"), None)
    if install is None or step_text(install).strip().splitlines()[:1] != [VENV_MAKE]:
        problems.append(f"the venv suite's install step does not make the venv first ({VENV_MAKE})")
    if not any(s.get("name") == VENV_ONLY_STEP for s in steps(venv)):
        problems.append("the venv suite does not check that its interpreter is a venv launcher")
    if _venv_normalized(venv) != steps(shard):
        problems.append("the venv suite's steps are not the windows shard job's, but for the interpreter, the step "
                        "that makes the venv and checks it, and the artifact's name")
    if venv_total.get("needs") != VENV_JOB or str(venv_total.get("if", "")).strip() != FULL_TOTAL_IF:
        problems.append(f"the venv total needs {venv_total.get('needs')!r} under `if: {venv_total.get('if')}`, not "
                        f"{VENV_JOB} at the same staging, always")
    own = json.loads(json.dumps(steps(venv_total)).replace("suite-windows-venv-shard-", "suite-windows-shard-"))
    if own != steps(total) or venv_total.get("runs-on") != total.get("runs-on"):
        problems.append("the venv total is not the shards' total over the venv shards' files")
    return problems


def plan_outputs(wf: dict, *, event: str, message: str, before: str, out: Path, cwd: Path = REPO) -> dict:
    """The workflow's plan step, EXECUTED as the runner executes it (the
    step's own Python, from its heredoc) for one event, in this checkout
    (or in `cwd`, a scratch repository whose diff a case controls); what it
    wrote to GITHUB_OUTPUT."""
    step = next(s for s in steps((wf.get("jobs") or {}).get("plan") or {}) if s.get("id") == "plan")
    code = step_text(step).partition("<<'EOF'\n")[2].rsplit("\nEOF", 1)[0]
    out.unlink(missing_ok=True)
    env = dict(os.environ, EVENT=event, MESSAGE=message, BEFORE=before, GITHUB_OUTPUT=str(out),
               GITHUB_STEP_SUMMARY=str(out.with_suffix(".summary")))
    proc = _proc.run([sys.executable, "-c", code], cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, f"the plan step failed for {event}: {proc.stderr[-800:]}"
    return dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line)


def run_coverage(workflows: dict, *, event: str, message: str, before: str, out: Path) -> dict:
    """What one event runs, read from ci.yml, bundle.yml and
    deploy-smoke.yml: the unsharded suite's systems, whether every windows
    shard runs, whether the Postgres job runs, the bundle's systems, the
    deploy smoke's profiles, and (W4 part 4) the simulated UAT's systems and
    whether its Postgres leg runs."""
    ci, bundle, smoke = workflows["ci.yml"], workflows["bundle.yml"], workflows["deploy-smoke.yml"]
    jobs = ci.get("jobs") or {}
    systems = suite_systems(ci)
    full = event == "workflow_dispatch" or "[ci full]" in message
    shard, postgres = jobs.get(SHARD_JOB) or {}, jobs.get(POSTGRES_JOB) or {}
    indices = ((shard.get("strategy") or {}).get("matrix") or {}).get("shard") or []
    postgres_runs = command_lines(postgres, "python", "-m", "pytest")
    suite_runs = command_lines(jobs.get(SUITE_JOB) or {}, "python", "-m", "pytest")
    ci_plan = plan_outputs(ci, event=event, message=message, before=before, out=out) if "plan" in jobs else {}
    bundle_plan = plan_outputs(bundle, event=event, message=message, before=before, out=out)
    smoke_plan = plan_outputs(smoke, event=event, message=message, before=before, out=out)
    bundle_job, smoke_job = (bundle.get("jobs") or {}).get("bundle") or {}, (smoke.get("jobs") or {}).get("smoke") or {}
    bundle_gated = "needs.plan.outputs.matrix" in str(bundle_job.get("if")) and \
        "needs.plan.outputs.matrix" in str((bundle_job.get("strategy") or {}).get("matrix"))
    profiles = ((smoke_job.get("strategy") or {}).get("matrix") or {}).get("profile") or []
    # W4 part 4: the simulated UAT runs the script (one run line, no pytest),
    # unconditionally, on the systems its staging expression gives; its
    # Postgres leg runs the script with --database-url on ubuntu, beside the
    # suite's own Postgres service, the same service word for word.
    uat, uat_pg = jobs.get(UAT_JOB) or {}, jobs.get(UAT_POSTGRES_JOB) or {}
    uat_systems = matrix_systems(uat)
    uat_runs, pg_runs = command_lines(uat, "python", UAT_SCRIPT), command_lines(uat_pg, "python", UAT_SCRIPT)
    uat_runs_it = bool(uat) and "if" not in uat and len(uat_runs) == 1 and not runs_pytest(uat)
    pg_service = (uat_pg.get("services") or {}).get("postgres")
    # W4b: the venv shards, by their job-level staging expression.
    venv = jobs.get(VENV_JOB) or {}
    venv_shards = ((venv.get("strategy") or {}).get("matrix") or {}).get("shard") or []
    venv_pytest = [w for _, _, w in command_lines(venv, VENV_PYTHON, "-m", "pytest")]
    condition = str(venv.get("if", "")).strip()
    venv_runs = full if condition == FULL_IF else (bool(venv) if not condition else None)
    venv_whole = venv.get("runs-on") == "windows-latest" and venv_shards == indices and len(indices) > 1 \
        and len(venv_pytest) == 2
    return {
        "the suite through a windows venv": (f"under an unread condition: {condition}" if venv_runs is None else
                                             ("every shard" if venv_whole else "not every shard") if venv_runs
                                             else "not run"),
        "the suite, unsharded": sorted(systems[0] if full else systems[1]) if systems else None,
        "the suite on windows": ("every shard" if shard.get("runs-on") == "windows-latest" and "if" not in shard
                                 and len(indices) > 1 and indices == list(range(1, len(indices) + 1)) else "not every shard"),
        # Amendment 6: the job runs on the plan's word, which C16 reads case by case.
        "the suite on Postgres": bool(postgres) and not postgres_wiring_problems(ci)
                                 and ci_plan.get("postgres") == "true" and len(postgres_runs) == 1
                                 and len(suite_runs) == 1 and postgres_runs[0][2] == suite_runs[0][2],
        "the bundle": sorted(json.loads(bundle_plan.get("matrix", "[]"))) if bundle_gated else None,
        "the deploy smoke": sorted(profiles) if smoke_plan.get("run") == "true" else [],
        "the simulated UAT": (sorted(uat_systems[0] if full else uat_systems[1])
                              if uat_runs_it and uat_systems else None),
        "the simulated UAT on Postgres": bool(uat_pg) and "if" not in uat_pg and uat_pg.get("runs-on") == "ubuntu-latest"
                                         and pg_service is not None
                                         and pg_service == ((postgres.get("services") or {}).get("postgres"))
                                         and len(pg_runs) == 1 and "--database-url" in pg_runs[0][2]
                                         and not runs_pytest(uat_pg),
    }


# Amendment 6, clause 6 (the operator, 16 November). The Postgres suite runs
# on a head commit marked [ci full], on a manual dispatch, and on a push that
# changed a file under one of these directories; on any other push the job is
# skipped by name. The plan job decides, from the push's own diff.
POSTGRES_WATCHED = ("backend/store/", "backend/kb/", "backend/memory/")
POSTGRES_IF = "needs.plan.outputs.postgres == 'true'"
POSTGRES_OUTPUT = "${{ steps.plan.outputs.postgres }}"


def postgres_wiring_problems(ci: dict) -> list[str]:
    """How the Postgres job reads the plan's decision: it needs the plan job,
    it runs on the plan's `postgres` output being the word true and on
    nothing else, and the plan job hands its step's output on under that
    name."""
    jobs = ci.get("jobs") or {}
    plan, job = jobs.get("plan") or {}, jobs.get(POSTGRES_JOB) or {}
    needs = job.get("needs")
    needs = [needs] if isinstance(needs, str) else list(needs or [])
    problems: list[str] = []
    if not job:
        problems.append(f"ci.yml: no {POSTGRES_JOB} job")
    if "plan" not in needs:
        problems.append(f"ci.yml: {POSTGRES_JOB} needs {needs}, not the plan job, so it cannot read the plan's decision")
    if str(job.get("if", "")).strip() != POSTGRES_IF:
        problems.append(f"ci.yml: {POSTGRES_JOB} runs under `if: {job.get('if')}`, not `{POSTGRES_IF}`")
    if (plan.get("outputs") or {}).get("postgres") != POSTGRES_OUTPUT:
        problems.append(f"ci.yml: the plan job's outputs are {plan.get('outputs')}, not postgres: {POSTGRES_OUTPUT}")
    return problems


def postgres_plan_problems(ci: dict, pushes: dict, out: Path) -> list[str]:
    """The plan step, executed for each push in `pushes` (label -> event,
    message, a repository, the push's previous head, what the rule asks),
    against the rule."""
    if "plan" not in (ci.get("jobs") or {}):
        return ["ci.yml: no plan job, so every push runs the Postgres suite or none does"]
    problems: list[str] = []
    for label, (event, message, repo, before, expected) in pushes.items():
        got = plan_outputs(ci, event=event, message=message, before=before, out=out, cwd=repo).get("postgres")
        if got != ("true" if expected else "false"):
            problems.append(f"ci.yml: {label}: the plan says postgres={got}")
    return problems


# Amendment 6, clause 6, its second sentence. A run that is not a closing run
# shares one concurrency group with its branch and cancels the branch's
# earlier run: nobody will read a superseded run. A head commit marked
# [ci full] and a manual dispatch are an item's evidence: each gets a group of
# its own, cancels nothing and is cancelled by nothing. In ci.yml only:
# bundle.yml and deploy-smoke.yml decide from each push's own diff, so a
# cancelled run of theirs is a build nothing replaces, and they keep one group
# per commit.
CANCELLING = ("ci.yml",)
GROUP_SHAPE = re.compile(r"^(?P<stem>[a-z-]+)-\$\{\{ \((?P<when>.+)\) && github\.run_id \|\| github\.ref \}\}$")
CANCEL_SHAPE = re.compile(r"^\$\{\{ !\((?P<when>.+)\) \}\}$")


def concurrency_decision(block, *, full: bool, ref: str, run_id: int) -> "tuple[str, bool] | None":
    """(the group, whether it cancels the group's run in progress) for one
    run, read from the block's two expressions by their documented meaning
    (`a && b || c` is b where a holds and c elsewhere; `!a`). None where
    either is in another shape or turns on another condition than the
    closing one: refused, never guessed."""
    if not isinstance(block, dict) or sorted(block) != ["cancel-in-progress", "group"]:
        return None
    group = GROUP_SHAPE.match(str(block["group"]))
    cancel = CANCEL_SHAPE.match(str(block["cancel-in-progress"]))
    if not group or not cancel or group["when"] != FULL_WHEN or cancel["when"] != FULL_WHEN:
        return None
    return f"{group['stem']}-{run_id if full else ref}", not full


def concurrency_problems(name: str, wf: dict) -> list[str]:
    block, stem = wf.get("concurrency"), name.removesuffix(".yml")
    if name not in CANCELLING:
        per_commit = {"group": stem + "-${{ github.ref }}-${{ github.sha }}", "cancel-in-progress": False}
        return [] if block == per_commit else [f"{name}: the concurrency block is {block}, not one group per commit, "
                                               f"never cancelled"]
    a, b = "refs/heads/claude/run-to-production", "refs/heads/another"
    first, later = (concurrency_decision(block, full=False, ref=a, run_id=n) for n in (1, 2))
    elsewhere = concurrency_decision(block, full=False, ref=b, run_id=3)
    closing, dispatch = (concurrency_decision(block, full=True, ref=a, run_id=n) for n in (4, 5))
    if None in (first, later, elsewhere, closing, dispatch):
        return [f"{name}: the concurrency block is {block}, not the rule's two expressions"]
    problems: list[str] = []
    if first[0] != later[0] or not later[1] or not later[0].startswith(stem + "-"):
        problems.append(f"{name}: a later push does not cancel its branch's earlier run ({first}, {later})")
    if elsewhere[0] == first[0]:
        problems.append(f"{name}: two branches share the group {first[0]}")
    for label, run in (("a closing run", closing), ("a dispatch", dispatch)):
        if run[1] or run[0] in (first[0], elsewhere[0]):
            problems.append(f"{name}: {label} cancels, or shares its branch's group and can be cancelled ({run})")
    if closing[0] == dispatch[0]:
        problems.append(f"{name}: two closing runs share the group {closing[0]}")
    return problems


# C14: no job runs on. A job with no `timeout-minutes` runs for GitHub's default.
GITHUB_DEFAULT_JOB_MINUTES = 360
JOB_BOUND_MAX = 90
# The deploy smoke takes about three minutes. A step of it that hangs fails as that step, by name; the job's own
# bound is the backstop for whatever no step's bound covers.
STEP_BOUNDED = ("deploy-smoke.yml",)
STEP_BOUND_MAX, STEP_BOUNDED_JOB_MAX = 15, 30


def minutes(value) -> "int | None":
    """A bound GitHub reads as a number when it parses the file: a positive int, never a bool, and never an
    expression, which can evaluate to nothing."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def step_label(step: dict, n: int) -> str:
    return str(step.get("name") or step.get("id") or step.get("uses") or f"step {n}")


def bound_problems(name: str, wf: dict) -> list[str]:
    problems: list[str] = []
    for job_name, job in (wf.get("jobs") or {}).items():
        limit = STEP_BOUNDED_JOB_MAX if name in STEP_BOUNDED else JOB_BOUND_MAX
        bound = minutes(job.get("timeout-minutes"))
        if bound is None:
            problems.append(f"{name}: job {job_name} declares no timeout-minutes GitHub reads as a number "
                            f"({job.get('timeout-minutes')!r}), so it may run for the default "
                            f"{GITHUB_DEFAULT_JOB_MINUTES} minutes")
        elif bound > limit:
            problems.append(f"{name}: job {job_name} may run for {bound} minutes, over the limit of {limit}")
        if name not in STEP_BOUNDED:
            continue
        for n, step in enumerate(steps(job), 1):
            bound = minutes(step.get("timeout-minutes"))
            if bound is None:
                problems.append(f"{name}: job {job_name}, step {step_label(step, n)!r} declares no timeout-minutes "
                                f"({step.get('timeout-minutes')!r}): a hang there ends as the job's cancellation, "
                                f"which names no step")
            elif bound > STEP_BOUND_MAX:
                problems.append(f"{name}: job {job_name}, step {step_label(step, n)!r} may hang for {bound} "
                                f"minutes, over the limit of {STEP_BOUND_MAX}")
    return problems


# --- C1 ---------------------------------------------------------------------------

def test_c1_actionlint_over_every_tracked_workflow():
    files = [p for p in tracked(".github/workflows/*.yml")]
    assert {p.name for p in files} >= {"ci.yml", "bundle.yml", "deploy-smoke.yml", "release.yml", "deploy.yml"}, \
        sorted(p.name for p in files)
    linter = shutil.which("actionlint")
    if linter is None:
        pytest.skip("actionlint is not on PATH: it is how the workflows are linted (CI's runner has it; the run fetched it)")
    cmd = [linter, "-no-color", "-pyflakes="]
    if shutil.which("shellcheck") is None:
        cmd.append("-shellcheck=")
    proc = _proc.run([*cmd, *map(str, files)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120)
    print(f"C1 actionlint over {len(files)} workflows: exit {proc.returncode}\n{proc.stdout[-1500:]}")
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --- C2 to C4 ------------------------------------------------------------------------

def test_c2_the_deploy_smoke_is_pinned_staged_and_runs_the_verifier():
    problems = smoke_problems(workflow("deploy-smoke.yml"))
    print(f"C2 {problems}")
    assert not problems, problems


def test_c3b_the_release_builds_the_standalone_verifier_on_three_systems():
    """W9 part 3c. Seen failing first on the release before the job existed."""
    hold(verifier_problems(workflow("release.yml")))


def test_c3b_companions_a_verifier_job_that_falls_short_is_named():
    base = workflow("release.yml")
    assert verifier_problems(base) == []
    name = next(n for n, j in base["jobs"].items()
                if any(VERIFIER_SCRIPT in step_text(s) for s in steps(j)))
    plants = []
    gone = planted_copy(base)
    del gone["jobs"][name]
    plants.append(("no verifier job", gone))
    linux_only = planted_copy(base)
    linux_only["jobs"][name]["strategy"]["matrix"]["os"] = ["ubuntu-24.04"]
    plants.append(("built on one system", linux_only))
    unrun = planted_copy(base)
    unrun["jobs"][name]["steps"] = [s for s in unrun["jobs"][name]["steps"] if "--help" not in step_text(s)]
    plants.append(("never run", unrun))
    unkept = planted_copy(base)
    unkept["jobs"][name]["steps"] = [s for s in unkept["jobs"][name]["steps"]
                                     if not str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    plants.append(("not kept", unkept))
    unwaited = planted_copy(base)
    unwaited["jobs"]["release"]["needs"] = [n for n in unwaited["jobs"]["release"]["needs"] if n != name]
    plants.append(("not waited for", unwaited))
    shared = planted_copy(base)
    for entry in shared["jobs"][name]["strategy"]["matrix"]["include"]:
        if entry["os"] == "macos-latest":
            entry["asset"] = next(e["asset"] for e in shared["jobs"][name]["strategy"]["matrix"]["include"]
                                  if e["os"] == "ubuntu-24.04")
    plants.append(("two systems, one asset name (the release refuses the second upload)", shared))
    unnamed = planted_copy(base)
    unnamed["jobs"][name]["steps"] = [dict(s, run=str(s["run"]).replace('--name "${{ matrix.asset }}"', "--name nexus-verify"))
                                      if "PyInstaller" in step_text(s) else s for s in unnamed["jobs"][name]["steps"]]
    plants.append(("built under one name on every system", unnamed))
    for label, wf in plants:
        assert refused(verifier_problems(wf)), label


def test_c3c_the_release_also_builds_the_verifier_under_the_standards_name():
    """W11b part 2. Seen failing first on the release before the talima-verify build existed."""
    hold(talima_problems(workflow("release.yml")))


def test_c3c_companions_a_talima_build_that_falls_short_is_named():
    base = workflow("release.yml")
    assert talima_problems(base) == []
    name = next(n for n, j in base["jobs"].items() if any(VERIFIER_SCRIPT in step_text(s) for s in steps(j)))
    plants = []
    unbuilt = planted_copy(base)
    unbuilt["jobs"][name]["steps"] = [dict(s, run="\n".join(line for line in str(s["run"]).split("\n")
                                                            if "matrix.talima" not in line))
                                      if "PyInstaller" in step_text(s) else s for s in unbuilt["jobs"][name]["steps"]]
    plants.append(("never built", unbuilt))
    shared = planted_copy(base)
    for entry in shared["jobs"][name]["strategy"]["matrix"]["include"]:
        entry["talima"] = "talima-verify-linux"
    plants.append(("one name on every system (the release refuses the second upload)", shared))
    unkept = planted_copy(base)
    for s in unkept["jobs"][name]["steps"]:
        if str(s.get("uses", "")).startswith("actions/upload-artifact@"):
            s["with"]["path"] = "\n".join(line for line in str(s["with"]["path"]).split("\n")
                                          if "matrix.talima" not in line)
    plants.append(("not kept", unkept))
    # The delta of W11b's verifier: three releases a step-level reading passed.
    for label, old, new in (
            ("built from the wrapper, which cannot find its verifier inside a binary",
             '--name "${{ matrix.talima }}" scripts/nexus_verify.py', '--name "${{ matrix.talima }}" scripts/talima_verify.py'),
            ("run without its own name checked",
             ' && grep -q "^usage: talima-verify" help-talima.txt', ""),
            ("echoed, not run", "./dist/${{ matrix.talima }} --help", "echo ./dist/${{ matrix.talima }} --help"),
            # Delta check 1's (N4): a build line that builds nothing.
            ("its build commented out", 'python -m PyInstaller --onefile --clean --noconfirm --name "${{ matrix.talima }}"',
             '# python -m PyInstaller --onefile --clean --noconfirm --name "${{ matrix.talima }}"'),
            ("its build echoed", 'python -m PyInstaller --onefile --clean --noconfirm --name "${{ matrix.talima }}"',
             'echo python -m PyInstaller --onefile --clean --noconfirm --name "${{ matrix.talima }}"'),
            ("its build behind true ||", 'python -m PyInstaller --onefile --clean --noconfirm --name "${{ matrix.talima }}"',
             'true || python -m PyInstaller --onefile --clean --noconfirm --name "${{ matrix.talima }}"'),
            # Delta check 2's (11): a check that cannot fail its step (an `&&` list's first command does not trip
            # errexit; `|| exit 1` makes either command's failure the step's).
            ("nexus-verify's run line unable to fail the step", 'help-nexus.txt || exit 1', "help-nexus.txt"),
            ("talima-verify's run line without || exit 1 (it fails its step only while it is the last line)",
             'help-talima.txt || exit 1', "help-talima.txt")):
        planted = planted_copy(base)
        hits = 0
        for s in planted["jobs"][name]["steps"]:
            if old in str(s.get("run") or ""):
                s["run"] = str(s["run"]).replace(old, new)
                hits += 1
        assert hits == 1, f"non-vacuity: {label}"
        plants.append((label, planted))
    for label, wf in plants:
        assert refused(talima_problems(wf)), label


def test_c3d_the_release_builds_the_interceptor_on_three_systems_and_reads_it_for_the_engine():
    """W10c part 3. Seen failing first on the release before the job existed."""
    hold(interceptor_problems(workflow("release.yml")))


def test_c3d_companions_an_interceptor_build_that_falls_short_is_named():
    base = workflow("release.yml")
    assert interceptor_problems(base) == []
    name = next(n for n, j in base["jobs"].items() if any(INTERCEPT_ENTRY in step_text(s) for s in steps(j)))
    plants = []
    gone = planted_copy(base)
    del gone["jobs"][name]
    plants.append(("no interceptor job", gone))
    linux_only = planted_copy(base)
    linux_only["jobs"][name]["strategy"]["matrix"]["os"] = ["ubuntu-24.04"]
    plants.append(("built on one system", linux_only))
    shared = planted_copy(base)
    for entry in shared["jobs"][name]["strategy"]["matrix"]["include"]:
        entry["asset"] = "talima-intercept-linux"
    plants.append(("one name on every system (the release refuses the second upload)", shared))
    unkept = planted_copy(base)
    unkept["jobs"][name]["steps"] = [s for s in unkept["jobs"][name]["steps"]
                                     if not str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    plants.append(("not kept", unkept))
    unwaited = planted_copy(base)
    unwaited["jobs"]["release"]["needs"] = [n for n in unwaited["jobs"]["release"]["needs"] if n != name]
    plants.append(("not waited for", unwaited))
    for label, old, new in (
            ("built without the checkout on its path", " --paths . ", " "),
            ("built from another file", f" --paths . {INTERCEPT_ENTRY}", " --paths . scripts/intercept_smoke.py"),
            ("its build echoed", "python -m PyInstaller --onefile --clean --noconfirm --name", "echo python -m PyInstaller --onefile --clean --noconfirm --name"),
            ("run without its own name checked", ' && grep -q "^usage: talima-intercept" help.txt', ""),
            ("its help line unable to fail the step", "help.txt || exit 1", "help.txt"),
            ("never made to open a store", " verify > verify.json", " --help > verify.json"),
            ("its verify line unable to fail the step", "verify.json || exit 1", "verify.json"),
            ("the archive not read for its own module (the reading could be of nothing)", INTERCEPT_OWN, "true"),
            ("the archive not read for the engine", INTERCEPT_NO_ENGINE, "true"),
            ("the engine reading unable to fail the step", 'PYZ-00.toc" || exit 1\n', 'PYZ-00.toc"\n')):
        planted = planted_copy(base)
        hits = 0
        for s in planted["jobs"][name]["steps"]:
            if old in str(s.get("run") or ""):
                s["run"] = str(s["run"]).replace(old, new)
                hits += 1
        assert hits == 1, f"non-vacuity: {label}"
        plants.append((label, planted))
    for label, wf in plants:
        assert refused(interceptor_problems(wf)), label


def test_c3_the_release_publishes_only_off_a_dry_run():
    problems = release_problems(workflow("release.yml"))
    print(f"C3 {problems}")
    assert not problems, problems


def test_c4_the_deploy_is_env_gated_and_a_named_no_op_without_its_secrets():
    problems = deploy_problems(workflow("deploy.yml"))
    print(f"C4 {problems}")
    assert not problems, problems


def compose_file() -> dict:
    return yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))


def test_c9_the_deploy_offers_every_profile_compose_runs_and_the_verifier_checks():
    problems = deploy_profile_problems(workflow("deploy.yml"), compose_file())
    print(f"C9 {problems}")
    assert not problems, problems


def verdict_problems(wf: dict) -> list[str]:
    """Steps that print a verdict of their own (PASS, FAIL or SKIP through
    echo, printf or print): the verdicts are the verifier's."""
    problems: list[str] = []
    for name, job in (wf.get("jobs") or {}).items():
        for step in steps(job):
            for line in step_text(step).splitlines():
                if VERDICT_PRINT.search(line):
                    problems.append(f"{name}: the step {step.get('name')!r} prints a verdict of its own: "
                                    f"{line.strip()[:90]}")
    return problems


def test_c8_no_workflow_prints_a_verdict_of_its_own():
    problems = {p.name: verdict_problems(workflow(p.name)) for p in tracked(".github/workflows/*.yml")}
    print(f"C8 {problems}")
    assert all(not v for v in problems.values()), {k: v for k, v in problems.items() if v}


def test_c7_root_only_commands_run_under_sudo_on_the_runner():
    problems = {p.name: runner_root_problems(workflow(p.name)) for p in tracked(".github/workflows/*.yml")}
    print(f"C7 {problems}")
    assert all(not v for v in problems.values()), {k: v for k, v in problems.items() if v}


# --- C5 ---------------------------------------------------------------------------

def test_c5_deploy_md_says_the_tested_path():
    assert DEPLOY_MD.is_file(), "docs/DEPLOY.md is missing"
    text = DEPLOY_MD.read_text(encoding="utf-8")
    for want in ("`stub`", "`ollama`", "`api`", "FILL", "NEXUS_DOMAIN", "NEXUS_API_TOKEN", "ENETUNREACH",
                 "-dryrun", "DEPLOY_KEY", "cloud-init schema", "verify_deploy.py", "W3b"):
        assert want in text, f"docs/DEPLOY.md does not mention {want}"
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    restore_cmd = next(l.strip("# ").strip() for l in compose.splitlines()
                       if "run --rm --entrypoint python backup scripts/restore_verify.py" in l)
    assert restore_cmd in text, "the restore command in DEPLOY.md differs from the compose header's"
    assert "docker compose --profile backup run --rm backup" in text


# --- C6: the companions ------------------------------------------------------------------

def test_c6_planted_ungated_steps_and_an_unpinned_runner_are_named():
    smoke = workflow("deploy-smoke.yml")
    assert smoke_problems(smoke) == []
    planted = yaml.safe_load(yaml.safe_dump(smoke))
    planted["jobs"]["smoke"]["runs-on"] = "ubuntu-latest"
    assert any("not the pinned ubuntu-24.04" in p for p in smoke_problems(planted))
    planted = yaml.safe_load(yaml.safe_dump(smoke))
    for s in planted["jobs"]["smoke"]["steps"]:
        if "down" in step_text(s) and "compose" in step_text(s):
            s.pop("if", None)
    assert any("not torn down" in p for p in smoke_problems(planted))
    planted = yaml.safe_load(yaml.safe_dump(smoke))
    planted["jobs"]["smoke"]["strategy"]["matrix"]["profile"] = ["stub"]          # the hosted store unsmoked
    assert any("not the stub and hosted profiles" in p for p in smoke_problems(planted))

    release = workflow("release.yml")
    assert release_problems(release) == []
    planted = yaml.safe_load(yaml.safe_dump(release))
    planted["jobs"]["image"]["steps"].append({"name": "planted push", "run": "docker push ghcr.io/x/nexus:planted"})
    assert any("'planted push' publishes without the dry-run gate" in p for p in release_problems(planted))
    planted = yaml.safe_load(yaml.safe_dump(release))
    planted["jobs"]["image"]["steps"].append({"name": "planted action", "uses": "someone/else@v1"})
    assert any("third-party action someone/else@v1" in p for p in release_problems(planted))

    deploy = workflow("deploy.yml")
    assert deploy_problems(deploy) == []
    planted = yaml.safe_load(yaml.safe_dump(deploy))
    planted["jobs"]["deploy"]["steps"].append({"name": "planted reach", "run": "ssh host 'docker compose up -d'"})
    assert any("'planted reach' reaches the host without the env gate" in p for p in deploy_problems(planted))
    planted = yaml.safe_load(yaml.safe_dump(deploy))
    planted["jobs"]["deploy"]["steps"].append({"name": "planted if", "if": "secrets.DEPLOY_HOST != ''", "run": "echo"})
    assert any("'planted if' reads secrets in its `if:`" in p for p in deploy_problems(planted))

    # C9's class: a profile the deploy cannot offer, or offers and nothing
    # verifies, or one compose runs and the verifier does not know. Each plant
    # is a member the rule's source does not name: the option list loses its
    # last name whatever it is, an option `gpu` is added, and compose gains a
    # `nexus-gpu` service under `gpu`.
    compose = compose_file()
    assert deploy_profile_problems(deploy, compose) == []
    planted = yaml.safe_load(yaml.safe_dump(deploy))
    on = planted.get("on") or planted[True]
    dropped = on["workflow_dispatch"]["inputs"]["profile"]["options"].pop()
    named = deploy_profile_problems(planted, compose)
    assert len(named) == 1 and "the deploy's profile options are" in named[0] and repr(dropped) in named[0], named
    planted = yaml.safe_load(yaml.safe_dump(deploy))
    (planted.get("on") or planted[True])["workflow_dispatch"]["inputs"]["profile"]["options"].append("gpu")
    named = deploy_profile_problems(planted, compose)
    assert len(named) == 1 and "the deploy's profile options are" in named[0] and "'gpu'" in named[0], named
    planted_compose = yaml.safe_load(yaml.safe_dump(compose))
    planted_compose["services"]["nexus-gpu"] = {"profiles": ["gpu"]}
    named = deploy_profile_problems(deploy, planted_compose)
    assert any("compose's NEXUS services run" in p and "'gpu'" in p for p in named), named
    # W4 part 3: a NEXUS service under hosted that the verifier's pattern does not match.
    planted_compose = yaml.safe_load(yaml.safe_dump(compose))
    planted_compose["services"]["nexus-observer"] = {"profiles": ["hosted"]}
    named = deploy_profile_problems(deploy, planted_compose)
    assert len(named) == 1 and "'nexus-observer'" in named[0] and "does not check" in named[0], named

    # C7's class: a root-only command bare on the runner, in an option order
    # the rule's source does not spell; the same under sudo, and the same
    # inside an ssh command (the host's), are not named.
    assert runner_root_problems(smoke) == []
    planted = yaml.safe_load(yaml.safe_dump(smoke))
    planted["jobs"]["smoke"]["steps"].append({"name": "planted root", "run": "echo making room\n"
                                              "install -m 0750 -g docker -d /var/lib/planted\n"
                                              "sudo install -m 0750 -g docker -d /var/lib/allowed\n"
                                              "ssh host 'install -d -o 10001 -g 10001 /opt/elsewhere'\n"})
    named = runner_root_problems(planted)
    assert named == ["smoke: the step 'planted root' needs root on the runner without sudo: "
                     "install -m 0750 -g docker -d /var/lib/planted"], named
    # C8's class: a verdict the workflow composes itself, planted through a
    # verb the defect did not use (deploy.yml's was Python's print).
    assert verdict_problems(deploy) == []
    planted = yaml.safe_load(yaml.safe_dump(deploy))
    planted["jobs"]["deploy"]["steps"].append({"name": "planted verdict",
                                               "run": "printf 'SKIP %s\\n' 'V9: planted'\necho done\n"})
    named = verdict_problems(planted)
    assert named == ["deploy: the step 'planted verdict' prints a verdict of its own: printf 'SKIP %s\\n' 'V9: planted'"], named

    rules = Path(__file__).read_text(encoding="utf-8").split("# --- the rules", 1)[1].split("# --- C1", 1)[0]
    for plant in ("planted push", "someone/else", "planted reach", "planted if", "planted root", "-g docker",
                  "planted verdict", "V9", "gpu"):
        assert plant not in rules, f"the rules' source names the plant {plant!r}"


# --- the shared helpers, and C18 (Amendment 7) --------------------------------------------

def rules_source() -> str:
    return Path(__file__).read_text(encoding="utf-8").split("# --- the rules", 1)[1].split("# --- C1", 1)[0]


def hold(problems: list[str]) -> None:
    """The guards' one assertion, shared with their companions: a companion
    shows this assertion failing on its plant, never a flag."""
    assert not problems, "; ".join(problems)


def refused(problems: list[str]) -> str:
    with pytest.raises(AssertionError) as e:
        hold(problems)
    return str(e.value)


def planted_copy(wf: dict) -> dict:
    return yaml.safe_load(yaml.safe_dump(wf))


def tracked_workflows() -> dict:
    return {p.name: workflow(p.name) for p in tracked(".github/workflows/*.yml")}


# --- C18: no workflow starts without a hand (Amendment 7, 17 November) ---------------------

def test_c18_no_workflow_triggers_on_push():
    """Amendment 7, clause 1, over every tracked workflow: its triggers are
    exactly workflow_dispatch. Seen failing first on the base's workflows
    (07a63b4): ci, bundle and deploy-smoke on a push, ci on a pull request,
    the release on a `v*` tag (law 16; the RUN_LOG entry)."""
    workflows = tracked_workflows()
    assert {*RECORDS_SKIPPING, "release.yml", "deploy.yml"} <= set(workflows), sorted(workflows)
    print(f"C18 {len(workflows)} workflows: "
          f"{ {name: sorted(triggers(wf), key=str) for name, wf in sorted(workflows.items())} }")
    hold([p for name, wf in sorted(workflows.items()) for p in by_hand_problems(name, wf)])


def _on_key(wf: dict):
    return "on" if "on" in wf else True


def test_c18_companions_a_workflow_that_starts_without_a_hand_is_named():
    workflows = tracked_workflows()
    filtered = {"branches-ignore": ["run/wip/**", "claude/run-docs"],
                "paths-ignore": ["run/specs/**", "*.md", "docs/**", "LICENSE"]}
    plants = [                                   # (label, workflow, the `on:` it is given, the event named)
        ("ci.yml's own push trigger restored", "ci.yml", {"push": filtered, BY_HAND: None}, "push"),
        ("a pull request", "ci.yml", {"pull_request": None, BY_HAND: None}, "pull_request"),
        ("the release's tag", "release.yml", {"push": {"tags": ["v*"]}, BY_HAND: None}, "push"),
        ("a schedule", "bundle.yml", {"schedule": [{"cron": "17 3 * * *"}], BY_HAND: None}, "schedule"),
        ("another run's end", "deploy-smoke.yml",
         {"workflow_run": {"workflows": ["ci"], "types": ["completed"]}, BY_HAND: None}, "workflow_run"),
        ("a merge queue", "deploy.yml", {"merge_group": None, BY_HAND: None}, "merge_group"),
        ("the list form", "deploy.yml", ["push", BY_HAND], "push"),
        ("the string form, no dispatch", "ci.yml", "pull_request_target", "pull_request_target"),
        ("no dispatch at all", "bundle.yml", {}, BY_HAND),
    ]
    for label, name, on, event in plants:
        planted = planted_copy(workflows[name])
        planted[_on_key(planted)] = on
        message = refused(by_hand_problems(name, planted))
        assert event in message and name in message, (label, message)
    # A workflow nobody has written yet is read the same way.
    new = {"name": "nightly", True: {"schedule": [{"cron": "5 4 * * *"}]}, "jobs": {}}
    message = refused(by_hand_problems("nightly.yml", new))
    assert "schedule" in message and BY_HAND in message, message
    # None of the planted events is named by the rule: it allows one event
    # and names nothing else (by AST, over the rule's own strings).
    rule = next(n for n in ast.parse(Path(__file__).read_text(encoding="utf-8")).body
                if isinstance(n, ast.FunctionDef) and n.name == "by_hand_problems")
    words = {w for c in ast.walk(rule) if isinstance(c, ast.Constant) and isinstance(c.value, str)
             for w in re.findall(r"[a-z_]+", c.value)}
    named = {"push", "pull_request", "schedule", "workflow_run", "merge_group", "pull_request_target"} & words
    assert not named, f"the rule names {sorted(named)}; it must allow one event and name none"


# --- C16 and C17: the Postgres suite where a store changed; no run nobody will read (Amendment 6) ---

PLAIN_MESSAGE, MARKED_MESSAGE = "W9 (part 2): a sub-commit", "W9: the item's closing commit [ci full]"
# label -> (event, message, the files the push's head commit changed, or None for no previous head, the rule)
POSTGRES_CASES = {
    "a file under backend/store/": ("push", PLAIN_MESSAGE, ["backend/store/dialect.py"], True),
    "a file under backend/kb/": ("push", PLAIN_MESSAGE, ["backend/kb/schema.py"], True),
    "a file under backend/memory/": ("push", PLAIN_MESSAGE, ["backend/memory/lineage.py"], True),
    "a file deep under a watched directory": ("push", PLAIN_MESSAGE, ["backend/memory/a/b/c.py"], True),
    "a store file among others": ("push", PLAIN_MESSAGE, ["tests/test_x.py", "backend/kb/x.py", "start.py"], True),
    "a store file whose name has a space and an accent": ("push", PLAIN_MESSAGE, ["backend/kb/caf\u00e9 notes.py"], True),
    "backend files outside the three": ("push", PLAIN_MESSAGE, ["backend/server.py", "backend/loop/agentic_loop.py"], False),
    "tests, scripts and a workflow": ("push", PLAIN_MESSAGE,
                                      ["tests/test_x.py", "scripts/backup.py", ".github/workflows/ci.yml"], False),
    "names that only begin like a watched directory": ("push", PLAIN_MESSAGE,
                                                       ["backend/kbx/x.py", "backend/storefront.py", "backend/memory.py"], False),
    "a watched name elsewhere in the tree": ("push", PLAIN_MESSAGE, ["tests/backend/kb/x.py", "docs/backend/store/x.md"], False),
    "a quiet push whose head commit is marked": ("push", MARKED_MESSAGE, ["backend/server.py"], True),
    "a manual dispatch": ("workflow_dispatch", "", None, True),
    "a new branch, with no previous head": ("push", PLAIN_MESSAGE, None, True),
}
NOT_IN_HISTORY = "1234567890" * 4


def _git(repo: Path, *words: str) -> str:
    proc = _proc.run(["git", "-c", "user.name=ci", "-c", "user.email=ci@example.invalid", "-c", "commit.gpgsign=false",
                      "-c", "core.autocrlf=false", *words], cwd=repo, capture_output=True, text=True, encoding="utf-8",
                     errors="replace", timeout=120)
    assert proc.returncode == 0, f"git {' '.join(words)}: {proc.stderr[-400:]}"
    return proc.stdout.strip()


def scratch_pushes(tmp: Path) -> dict:
    """One scratch repository per case: a base commit, then a head commit
    that changes exactly the case's files. The push is (event, message, the
    repository, the base's hash as the previous head, what the rule asks).
    A case with no files has no previous head: none on a dispatch, forty
    zeros on a new branch, as GitHub sends them. One more push names a
    previous head that is not in the history (a forced push)."""
    pushes: dict = {}
    for n, (label, (event, message, changed, expected)) in enumerate(POSTGRES_CASES.items()):
        repo = tmp / f"push{n}"
        repo.mkdir()
        _git(repo, "init", "-q")
        (repo / "base.txt").write_text("base\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        before = _git(repo, "rev-parse", "HEAD")
        for name in changed or ["unrelated.txt"]:
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text("changed\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", message or "a commit")
        if changed is None:
            before = "" if event == "workflow_dispatch" else "0" * 40
        pushes[label] = (event, message, repo, before, expected)
    quiet = pushes["backend files outside the three"]
    pushes["a previous head that is not in the history"] = (quiet[0], quiet[1], quiet[2], NOT_IN_HISTORY, True)
    return pushes


def test_c16_the_postgres_suite_runs_at_a_close_on_a_dispatch_and_where_a_store_file_changed(tmp_path):
    """Amendment 6, clause 6. ci.yml's plan step is executed, as the runner
    executes it, for each push; the job is read to run on the plan's word
    and on nothing else. Before the change the job ran on every push and
    ci.yml had no plan (law 16; the run's RUN_LOG entry)."""
    ci, pushes = workflow("ci.yml"), scratch_pushes(tmp_path)
    assert {changed[0].split("/")[1] for _, _, changed, wanted in POSTGRES_CASES.values()
            if wanted and changed and len(changed) == 1 and changed[0].startswith("backend/")} >= \
        {w.split("/")[1] for w in POSTGRES_WATCHED}, "a case for each watched directory"
    problems = postgres_wiring_problems(ci) + postgres_plan_problems(ci, pushes, tmp_path / "plan.txt")
    print(f"C16 {len(pushes)} pushes: " + "; ".join(f"{label}: {'runs' if push[4] else 'skipped'}"
                                                    for label, push in pushes.items()))
    hold(problems)
    # The other Postgres leg is not this rule's: the simulated UAT's still runs on every push.
    assert "if" not in workflow("ci.yml")["jobs"][UAT_POSTGRES_JOB]


def test_c16_companions_a_plan_or_a_job_that_decides_otherwise_is_named(tmp_path):
    ci, pushes, out = workflow("ci.yml"), scratch_pushes(tmp_path), tmp_path / "plan.txt"
    hold(postgres_wiring_problems(ci) + postgres_plan_problems(ci, pushes, out))

    def planted_plan(old: str, new: str) -> dict:
        planted = planted_copy(ci)
        step = next(s for s in planted["jobs"]["plan"]["steps"] if s.get("id") == "plan")
        assert old in step["run"], old
        step["run"] = step["run"].replace(old, new)
        return planted

    def one(label: str) -> dict:
        return {label: pushes[label]}

    said = refused(postgres_plan_problems(planted_plan('"backend/memory/"', '"backend/eval/"'),      # a directory dropped
                                          one("a file under backend/memory/"), out))
    assert "a file under backend/memory/: the plan says postgres=false" in said, said
    said = refused(postgres_plan_problems(planted_plan('"backend/kb/"', '"backend/kb"'),             # without its slash
                                          one("names that only begin like a watched directory"), out))
    assert "names that only begin like a watched directory: the plan says postgres=true" in said, said
    said = refused(postgres_plan_problems(planted_plan('"[ci full]" in message', '"[ci everything]" in message'),
                                          one("a quiet push whose head commit is marked"), out))   # the mark ignored
    assert "a quiet push whose head commit is marked: the plan says postgres=false" in said, said
    # A dispatch is held twice over: by its event, and by having no previous head, where the suite runs.
    # So a plan that no longer reads the event still runs it, and that plant would show nothing.
    hold(postgres_plan_problems(planted_plan('event == "workflow_dispatch" or', 'event == "release" or'),
                                one("a manual dispatch"), out))
    said = refused(postgres_plan_problems(planted_plan('changed is None or any(', 'changed is not None and any('),   # no previous head: skipped
                                          one("a new branch, with no previous head"), out))
    assert "a new branch, with no previous head: the plan says postgres=false" in said, said
    planted = planted_copy(ci)                                                                       # the condition removed
    del planted["jobs"][POSTGRES_JOB]["if"]
    assert "runs under `if: None`" in refused(postgres_wiring_problems(planted))
    planted = planted_copy(ci)                                                                       # another output
    planted["jobs"][POSTGRES_JOB]["if"] = "needs.plan.outputs.sqlite == 'true'"
    assert "runs under `if: needs.plan.outputs.sqlite == 'true'`" in refused(postgres_wiring_problems(planted))
    planted = planted_copy(ci)                                                                       # always, whatever the plan
    planted["jobs"][POSTGRES_JOB]["if"] = "always() || " + POSTGRES_IF
    assert "runs under `if: always() ||" in refused(postgres_wiring_problems(planted))
    planted = planted_copy(ci)                                                                       # the plan not needed
    del planted["jobs"][POSTGRES_JOB]["needs"]
    assert "not the plan job" in refused(postgres_wiring_problems(planted))
    planted = planted_copy(ci)                                                                       # the output not handed on
    planted["jobs"]["plan"]["outputs"] = {"postgres": "true"}
    assert "the plan job's outputs are {'postgres': 'true'}" in refused(postgres_wiring_problems(planted))
    for plant in ("backend/eval/", "[ci everything]", "needs.plan.outputs.sqlite"):
        assert plant not in rules_source(), f"the rules' source names the plant {plant!r}"


def test_c17_a_superseded_run_is_cancelled_and_a_closing_run_never_is():
    """Amendment 6, clause 6, its second sentence, over the parsed
    workflows. Before the change ci.yml held one group per commit, never
    cancelled (law 16; the run's RUN_LOG entry)."""
    workflows = tracked_workflows()
    problems = [p for name in RECORDS_SKIPPING for p in concurrency_problems(name, workflows[name])]
    block = workflows["ci.yml"].get("concurrency")
    run_branch = "refs/heads/" + RUN_BRANCH
    table = {label: concurrency_decision(block, full=full, ref=run_branch, run_id=n)
             for n, (label, full) in enumerate((("a push", False), ("the next push", False), ("a closing push", True),
                                                ("a dispatch", True)), start=1)}
    print(f"C17 ci.yml: {table}")
    hold(problems)
    assert table["a push"] == table["the next push"] == ("ci-" + run_branch, True)
    assert table["a closing push"] == ("ci-3", False) and table["a dispatch"] == ("ci-4", False)


def test_c17_companions_a_block_that_cancels_a_closing_run_or_nothing_is_named():
    workflows = tracked_workflows()
    ci, bundle = workflows["ci.yml"], workflows["bundle.yml"]
    hold(concurrency_problems("ci.yml", ci) + concurrency_problems("bundle.yml", bundle))
    rule = ci["concurrency"]

    def with_block(block: dict) -> str:
        return refused(concurrency_problems("ci.yml", dict(ci, concurrency=block)))

    not_the_rule = "not the rule's two expressions"
    assert not_the_rule in with_block({"group": "ci-${{ github.ref }}", "cancel-in-progress": True})   # cancels every run
    assert not_the_rule in with_block(bundle["concurrency"] | {"group": "ci-${{ github.ref }}-${{ github.sha }}"})  # as before
    mark_only = "contains(github.event.head_commit.message, '[ci full]')"                             # a dispatch cancellable
    assert not_the_rule in with_block({k: str(v).replace(FULL_WHEN, mark_only) for k, v in rule.items()})
    assert not_the_rule in with_block(dict(rule, **{"cancel-in-progress": str(rule["cancel-in-progress"]).replace("!", "")}))
    assert not_the_rule in with_block(dict(rule, extra="x"))
    said = refused(concurrency_problems("bundle.yml", dict(bundle, concurrency=rule)))                 # the bundle cancelling
    assert "bundle.yml: the concurrency block is" in said and "not one group per commit, never cancelled" in said, said


def test_c12_the_windows_suite_runs_in_shards_with_the_unsharded_jobs_own_steps_and_flags():
    ci = workflow("ci.yml")
    problems = shard_problems(ci)
    print(f"C12 the unsharded suite's systems (full, staged): {suite_systems(ci)}; problems: {problems}")
    hold(problems)
    print(f"C12 shards: {ci['jobs'][SHARD_JOB]['strategy']['matrix']['shard']}")


def test_c12_companions_each_way_the_shards_can_lose_a_test_is_named():
    ci = workflow("ci.yml")
    hold(shard_problems(ci))

    def plant(change) -> str:
        planted = planted_copy(ci)
        change(planted["jobs"])
        return refused(shard_problems(planted))

    def without(job: str, words: str):
        def change(jobs):
            jobs[job]["steps"] = [s for s in jobs[job]["steps"] if words not in step_text(s)]
        return change

    def rewrite(job: str, old: str, new: str):
        def change(jobs):
            for s in jobs[job]["steps"]:
                if old in step_text(s):
                    s["run"] = s["run"].replace(old, new)
        return change

    said = plant(lambda jobs: jobs[SHARD_JOB]["strategy"]["matrix"].update(shard=[1, 2, 4]))    # a shard index missing
    assert "the shard indices are [1, 2, 4], not exactly 1..K" in said, said
    said = plant(lambda jobs: jobs[SHARD_JOB]["strategy"]["matrix"].update(shard=[1, 2, 3]))    # a K nobody else was told
    assert "--shards 4, the matrix holds 3 shards" in said and "of 3)" in said, said
    said = plant(without(SHARD_JOB, " check "))                                                # the count check gone
    assert "0 count checks" in said, said
    said = plant(lambda jobs: jobs[TOTAL_JOB].pop("needs"))                                     # a total that waits for nothing
    assert f"the total job does not need {SHARD_JOB}" in said, said
    said = plant(lambda jobs: jobs[TOTAL_JOB].pop("if"))                                        # ... or runs only on success
    assert f"the total job does not need {SHARD_JOB} under `if: always()`" in said, said
    back = '["ubuntu-latest", "windows-latest", "macos-latest"]'
    said = plant(lambda jobs: jobs[SUITE_JOB]["strategy"]["matrix"].update(                    # windows back, unsharded
        os=jobs[SUITE_JOB]["strategy"]["matrix"]["os"].replace('["ubuntu-latest", "macos-latest"]', back)))
    assert f"windows also runs pytest in the job {SUITE_JOB}" in said, said
    # W4 part 4: a second windows job running pytest under another name, and
    # through a launcher the rule's source does not spell.
    second = {"runs-on": "windows-2022", "steps": [{"uses": "actions/checkout@v4"},
                                                   {"run": "py -3 -m pytest tests/test_ci_shard.py -q"}]}
    said = plant(lambda jobs: jobs.update({"nightly-regressions": second}))
    assert "windows also runs pytest in the job nightly-regressions" in said, said
    said = plant(rewrite(SHARD_JOB, " -rfE 2>&1", " 2>&1"))                                     # a flag dropped from the run
    assert "the same words after the target are required" in said, said
    said = plant(rewrite(SHARD_JOB, '"${files[@]}" -q', '"${files[@]}" -x -q'))                 # ... or one added
    assert "the same words after the target are required" in said, said
    said = plant(without(SHARD_JOB, "git status --porcelain"))                                 # a sentinel dropped
    assert "lacks the suite job's step" in said and "and worktree sentinels (after the suite)'" in said, said
    said = plant(rewrite(TOTAL_JOB, "--shards 4", "--shards 3"))                                # the total told another K
    assert "the total is called with --shards 3" in said, said
    said = plant(rewrite(SHARD_JOB, "tests --collect-only", "tests/test_w3_ci.py --collect-only"))   # a partial collection
    assert "not a --collect-only -rs of the unsharded target 'tests'" in said, said
    said = plant(lambda jobs: jobs[SHARD_JOB].update({"if": "contains(github.event.head_commit.message, '[ci full]')"}))
    assert "the shard job is conditional" in said, said
    said = plant(lambda jobs: jobs[SHARD_JOB]["strategy"].update({"fail-fast": True}))
    assert "not `fail-fast: false`" in said, said
    for plant_text in ("[1, 2, 4]", "-x", "git status", back, "nightly-regressions", "windows-2022", "py -3"):
        assert plant_text not in rules_source(), f"the rules' source names the plant {plant_text!r}"


def test_c12b_the_suite_runs_through_a_windows_venv_as_the_operators_gate_runs_it():
    """W4b (run/specs/W4b.md D5): the venv shards are the windows shards
    through `.venv/Scripts/python.exe`, staged at [ci full] and dispatch,
    with their own total. Pre-change (law 16): the base ci.yml has no such
    job (venv_problems names that), and the base C12 rule names the new job
    as a second windows pytest job (the change below is recorded)."""
    ci = workflow("ci.yml")
    problems = venv_problems(ci)
    print(f"C12b venv shards {ci['jobs'].get(VENV_JOB, {}).get('strategy')}; problems {problems}")
    hold(problems)
    hold(shard_problems(ci))


def test_c12b_companions_each_way_the_venv_suite_can_stop_being_the_gates_is_named():
    base = workflow("ci.yml")

    def said(change) -> str:
        planted = planted_copy(base)
        change(planted["jobs"])
        return refused(venv_problems(planted) + shard_problems(planted))

    def run_line(jobs, old, new):
        for step in jobs[VENV_JOB]["steps"]:
            if old in str(step.get("run", "")):
                step["run"] = step["run"].replace(old, new, 1)
                return
        raise AssertionError(f"no step holds {old!r}")

    got = said(lambda jobs: run_line(jobs, f'{VENV_PYTHON} -m pytest "${{files[@]}}"', 'python -m pytest "${files[@]}"'))
    assert "the venv suite starts 'python'" in got, got                  # the runner's interpreter, not the venv's
    got = said(lambda jobs: run_line(jobs, f'{VENV_PYTHON} -m pytest "${{files[@]}}"', 'py -3.11 -m pytest "${files[@]}"'))
    assert "the venv suite starts 'py'" in got, got
    got = said(lambda jobs: run_line(jobs, VENV_MAKE + "\n", ""))
    assert "does not make the venv first" in got, got
    got = said(lambda jobs: jobs[VENV_JOB].pop("if"))                     # on every push
    assert "the venv suite runs `if: None`" in got, got
    got = said(lambda jobs: jobs[VENV_JOB]["steps"].insert(3, {"name": "aider", "run": f"{VENV_PYTHON} -m pip install aider-chat"}))
    assert "not the windows shard job's" in got, got
    got = said(lambda jobs: jobs[VENV_JOB]["strategy"]["matrix"].update(shard=[1, 2, 3]))
    assert "not the windows shards'" in got, got
    got = said(lambda jobs: jobs[VENV_TOTAL_JOB].update(needs=SHARD_JOB))
    assert "the venv total needs 'suite-windows'" in got, got
    got = said(lambda jobs: jobs.pop(VENV_TOTAL_JOB))
    assert f"not {VENV_JOB} and {VENV_TOTAL_JOB}" in got, got
    got = said(lambda jobs: jobs.update({"suite-windows-venv-2": dict(jobs[VENV_JOB], name="another")}))
    assert "windows also runs pytest in the job suite-windows-venv-2" in got, got


def test_c12b_pre_change_the_base_rule_and_the_base_workflow():
    """Law 16, the recorded change's two sides, on this checkout's ci.yml
    with the venv jobs taken out (the base's shape): venv_problems names
    their absence; and the base's C12 loop (no exemption) names the venv
    job as a second windows pytest job."""
    base = planted_copy(workflow("ci.yml"))
    base["jobs"].pop(VENV_JOB)
    base["jobs"].pop(VENV_TOTAL_JOB)
    assert venv_problems(base) and f"not {VENV_JOB}" in venv_problems(base)[0]
    ci = workflow("ci.yml")
    jobs = ci["jobs"]
    base_rule = [name for name, job in jobs.items() if name != SHARD_JOB and on_windows(job) and runs_pytest(job)]
    print(f"C12b pre-change: the base rule names {base_rule}")
    assert base_rule == [VENV_JOB], base_rule


def test_c12_a_windows_job_that_runs_no_pytest_is_not_the_suite():
    """W4 part 4, the recorded change's other side: a windows job that runs
    a script and no pytest (the simulated UAT's shape, planted here under a
    name and a script the rule's source does not name) is not refused; the
    base's rule ("windows is in no other job") refused it. The real ci.yml's
    windows jobs other than the shards are listed, and none runs pytest."""
    ci = workflow("ci.yml")
    hold(shard_problems(ci))
    planted = planted_copy(ci)
    planted["jobs"]["docs-render"] = {"runs-on": "windows-latest", "steps": [
        {"uses": "actions/checkout@v4"}, {"run": "python scripts/build_bundle.py --check"}]}
    hold(shard_problems(planted))
    # W4b: beside the shards and the venv shards (C12b holds those).
    others = sorted(n for n, j in ci["jobs"].items() if n not in (SHARD_JOB, VENV_JOB) and on_windows(j))
    print(f"C12 windows jobs beside the shards: {others}; running pytest: "
          f"{[n for n in others if runs_pytest(ci['jobs'][n])]}")
    assert others and not [n for n in others if runs_pytest(ci["jobs"][n])], others
    for plant_text in ("docs-render", "build_bundle"):
        assert plant_text not in rules_source(), f"the rules' source names the plant {plant_text!r}"


CLOSING_MESSAGE = "W9: the item's closing commit [ci full]"
# What an item's closing run covers, and covered before the shards: the
# suite on ubuntu and macos unsharded, windows (now every shard), the
# Postgres job, both bundles, both deploy-smoke profiles.
CLOSING_COVERAGE = {
    "the suite, unsharded": ["macos-latest", "ubuntu-latest"],
    "the suite on windows": "every shard",
    "the suite on Postgres": True,
    "the bundle": ["macos-latest", "windows-latest"],
    "the deploy smoke": ["hosted", "stub"],
    # W4 part 4 (D23): the simulated UAT on the three systems at the close,
    # and its hosted section on Postgres beside the suite's service.
    "the simulated UAT": ["macos-latest", "ubuntu-latest", "windows-latest"],
    "the simulated UAT on Postgres": True,
    # W4b (D5): the suite through a windows venv's interpreter, every shard.
    "the suite through a windows venv": "every shard",
}
# What an unmarked push covers of the same (the staging, RUN_CHARTER.md §7).
STAGED_COVERAGE = dict(CLOSING_COVERAGE, **{"the suite, unsharded": ["ubuntu-latest"], "the bundle": [],
                                            "the deploy smoke": [],
                                            "the simulated UAT": ["ubuntu-latest", "windows-latest"],
                                            "the suite through a windows venv": "not run",
                                            # Amendment 6: a push that changed no store file
                                            "the suite on Postgres": False})


def head_commit() -> str:
    """This checkout's HEAD: as a push's `before`, the push changed no file,
    so nothing but the mark or a dispatch can make a plan run."""
    proc = _proc.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)
    if proc.returncode != 0 or not proc.stdout.strip():
        pytest.skip("no git HEAD here: the plan steps diff against a commit")
    return proc.stdout.strip()


def coverage_problems(got: dict, expected: dict) -> list[str]:
    return [f"{what}: {got.get(what)!r}, not {wanted!r}" for what, wanted in expected.items() if got.get(what) != wanted]


def test_c13_an_items_closing_run_covers_what_it_covered(tmp_path):
    workflows, before = tracked_workflows(), head_commit()
    out = tmp_path / "plan.txt"
    closing = run_coverage(workflows, event="push", message=CLOSING_MESSAGE, before=before, out=out)
    dispatch = run_coverage(workflows, event="workflow_dispatch", message="", before="", out=out)
    plain = run_coverage(workflows, event="push", message="W9 (part 2): a sub-commit", before=before, out=out)
    print(f"C13 a push marked [ci full]: {closing}\nC13 a dispatch: {dispatch}\nC13 the same push unmarked: {plain}")
    hold(coverage_problems(closing, CLOSING_COVERAGE)
         + [f"on a dispatch, {p}" for p in coverage_problems(dispatch, CLOSING_COVERAGE)])
    # The control: unmarked, a push that changed none of the plans' files
    # builds no bundle and smokes nothing, and the suite and the simulated
    # UAT run staged. So the mark is what the coverage above turns on, and
    # the plans were read.
    hold(coverage_problems(plain, STAGED_COVERAGE))
    # Since Amendment 7 a push starts none of the three (C18): the dispatch,
    # whose coverage is held above, is the one run there is. The push cases
    # exercise the plan steps' own branches, which stay in the files.


def test_c13_companions_a_closing_run_that_covers_less_is_named(tmp_path):
    workflows, before = tracked_workflows(), head_commit()
    out = tmp_path / "plan.txt"
    hold(coverage_problems(run_coverage(workflows, event="push", message=CLOSING_MESSAGE, before=before, out=out),
                           CLOSING_COVERAGE))

    def covered(name: str, change) -> str:
        planted = dict(workflows, **{name: planted_copy(workflows[name])})
        change(planted[name]["jobs"])
        got = run_coverage(planted, event="push", message=CLOSING_MESSAGE, before=before, out=out)
        return refused(coverage_problems(got, CLOSING_COVERAGE))

    def plan_without(old: str, new: str):
        def change(jobs):
            step = next(s for s in jobs["plan"]["steps"] if s.get("id") == "plan")
            assert old in step["run"], old
            step["run"] = step["run"].replace(old, new)
        return change

    both = '["ubuntu-latest", "macos-latest"]'
    said = covered("ci.yml", lambda jobs: jobs[SUITE_JOB]["strategy"]["matrix"].update(
        os=jobs[SUITE_JOB]["strategy"]["matrix"]["os"].replace(both, '["ubuntu-latest"]')))        # macos dropped at the close
    assert "the suite, unsharded: ['ubuntu-latest'], not" in said, said
    said = covered("ci.yml", lambda jobs: jobs[SHARD_JOB]["strategy"]["matrix"].update(shard=[1, 2, 3, 5]))
    assert "the suite on windows: 'not every shard'" in said, said
    said = covered("ci.yml", lambda jobs: jobs.pop(POSTGRES_JOB))
    assert "the suite on Postgres: False" in said, said
    said = covered("bundle.yml", plan_without('\'["windows-latest", "macos-latest"]\'', '\'["windows-latest"]\''))
    assert "the bundle: ['windows-latest'], not" in said, said
    said = covered("deploy-smoke.yml", plan_without('or "[ci full]" in message', 'or "[ci everything]" in message'))
    assert "the deploy smoke: [], not" in said, said
    said = covered("deploy-smoke.yml", lambda jobs: jobs["smoke"]["strategy"]["matrix"].update(profile=["stub"]))
    assert "the deploy smoke: ['stub'], not" in said, said
    # W4 part 4: a system dropped from each of the simulated UAT's rows.
    three = '["ubuntu-latest", "windows-latest", "macos-latest"]'
    said = covered("ci.yml", lambda jobs: jobs[UAT_JOB]["strategy"]["matrix"].update(               # macos at the close
        os=jobs[UAT_JOB]["strategy"]["matrix"]["os"].replace(three, '["ubuntu-latest", "windows-latest"]')))
    assert "the simulated UAT: ['ubuntu-latest', 'windows-latest'], not" in said, said
    said = covered("ci.yml", lambda jobs: jobs[UAT_POSTGRES_JOB].update({"runs-on": "macos-latest"}))  # off the service's system
    assert "the simulated UAT on Postgres: False, not True" in said, said
    # W4b: the venv shards lost at the close, or gone.
    said = covered("ci.yml", lambda jobs: jobs[VENV_JOB]["strategy"]["matrix"].update(shard=[1, 2, 4]))
    assert "the suite through a windows venv: 'not every shard', not 'every shard'" in said, said
    said = covered("ci.yml", lambda jobs: jobs.pop(VENV_JOB))
    assert "the suite through a windows venv: 'not run', not 'every shard'" in said, said
    planted = dict(workflows, **{"ci.yml": planted_copy(workflows["ci.yml"])})                      # windows on a push
    matrix = planted["ci.yml"]["jobs"][UAT_JOB]["strategy"]["matrix"]
    assert matrix["os"].endswith("|| '[\"ubuntu-latest\", \"windows-latest\"]') }}"), matrix["os"]
    matrix["os"] = matrix["os"].replace("|| '[\"ubuntu-latest\", \"windows-latest\"]'", "|| '[\"ubuntu-latest\"]'")
    got = run_coverage(planted, event="push", message="W9 (part 2): a sub-commit", before=before, out=out)
    said = refused(coverage_problems(got, STAGED_COVERAGE))
    assert "the simulated UAT: ['ubuntu-latest'], not ['ubuntu-latest', 'windows-latest']" in said, said


# --- C14 --------------------------------------------------------------------------

# The workflow the operator's instruction of 15 November names. The companions plant in it by this name, not by
# the rule's own STEP_BOUNDED, so a rule that stops reading its steps is named.
SMOKE_WORKFLOW = "deploy-smoke.yml"


def test_c14_no_job_runs_on_and_a_smoke_step_that_hangs_fails_by_name():
    workflows = tracked_workflows()
    assert SMOKE_WORKFLOW in workflows and set(STEP_BOUNDED) <= set(workflows), sorted(workflows)
    for name, wf in sorted(workflows.items()):
        print(f"C14 {name}: " + ", ".join(f"{job} {body.get('timeout-minutes')}" for job, body in wf["jobs"].items()))
    hold([p for name, wf in sorted(workflows.items()) for p in bound_problems(name, wf)])


def test_c14_companions_a_job_or_a_smoke_step_with_no_bound_is_named():
    workflows = tracked_workflows()

    def said(name: str, change) -> str:
        planted = planted_copy(workflows[name])
        change(planted["jobs"])
        return refused(bound_problems(name, planted))

    # The rule names no job and no step: what it refuses below, it refuses by the class.
    source = inspect.getsource(bound_problems) + inspect.getsource(minutes) + inspect.getsource(step_label)
    # A job with no bound: every job of every workflow, whatever it is called, whatever its `if:` and its `needs:`.
    for name, wf in sorted(workflows.items()):
        for job_name in wf["jobs"]:
            assert job_name not in source, (job_name, "the rule's source names the planted job")
            text = said(name, lambda jobs: jobs[job_name].pop("timeout-minutes"))
            assert f"{name}: job {job_name} declares no timeout-minutes GitHub reads as a number (None)" in text, text
    # A new job nobody bounded, and bounds GitHub does not read as a number of minutes or that are its own default.
    text = said("ci.yml", lambda jobs: jobs.update(nightly={"runs-on": "ubuntu-latest", "steps": [{"run": "true"}]}))
    assert "ci.yml: job nightly declares no timeout-minutes" in text, text
    for unread in ("${{ vars.BOUND }}", 0, -5, True, 7.5, "30"):
        text = said("release.yml", lambda jobs: jobs[next(iter(jobs))].update({"timeout-minutes": unread}))
        assert f"declares no timeout-minutes GitHub reads as a number ({unread!r})" in text, (unread, text)
    text = said("deploy.yml", lambda jobs: jobs[next(iter(jobs))].update({"timeout-minutes": 360}))
    assert "may run for 360 minutes, over the limit of 90" in text, text
    # The deploy smoke: a step with no bound is named by its own name, whichever step it is.
    smoke = workflows[SMOKE_WORKFLOW]["jobs"]
    for job_name, job in smoke.items():
        for n, step in enumerate(steps(job)):
            label = step_label(step, n + 1)
            assert label not in source, (label, "the rule's source names the planted step")
            text = said(SMOKE_WORKFLOW, lambda jobs: jobs[job_name]["steps"][n].pop("timeout-minutes"))
            assert f"{SMOKE_WORKFLOW}: job {job_name}, step {label!r} declares no timeout-minutes (None)" in text, text
        # Each of its jobs is held to the smoke's own limit, the first as the last.
        text = said(SMOKE_WORKFLOW, lambda jobs: jobs[job_name].update({"timeout-minutes": 45}))
        assert f"{SMOKE_WORKFLOW}: job {job_name} may run for 45 minutes, over the limit of 30" in text, text
    # A new step with no bound is named by its name, or its id, or the action it uses, or its position.
    count = len(steps(smoke["smoke"]))
    for new, label in (({"name": "Wait for the tunnel", "run": "sleep 9"}, "Wait for the tunnel"),
                       ({"id": "tunnel", "run": "sleep 9"}, "tunnel"),
                       ({"uses": "actions/cache@v4"}, "actions/cache@v4"),
                       ({"run": "sleep 9"}, f"step {count + 1}")):
        text = said(SMOKE_WORKFLOW, lambda jobs: jobs["smoke"]["steps"].append(dict(new)))
        assert f"step {label!r} declares no timeout-minutes" in text and "unnel" not in source, (label, text)
    text = said(SMOKE_WORKFLOW, lambda jobs: jobs["smoke"]["steps"][-1].update({"timeout-minutes": 45}))
    assert "may hang for 45 minutes, over the limit of 15" in text, text
    text = said(SMOKE_WORKFLOW, lambda jobs: jobs["smoke"].update({"timeout-minutes": 50}))    # the bound it had
    assert f"{SMOKE_WORKFLOW}: job smoke may run for 50 minutes, over the limit of 30" in text, text


def test_c14_pre_change_the_smoke_as_36ffb17_had_it_is_named():
    """Law 16's failing-first run, kept: the two workflows with the bounds this change added taken out again."""
    workflows = tracked_workflows()
    before = planted_copy(workflows[SMOKE_WORKFLOW])
    for job in before["jobs"].values():
        for step in steps(job):
            step.pop("timeout-minutes")
    before["jobs"]["plan"].pop("timeout-minutes")
    before["jobs"]["smoke"]["timeout-minutes"] = 50
    problems = bound_problems(SMOKE_WORKFLOW, before)
    count = sum(len(steps(job)) for job in workflows[SMOKE_WORKFLOW]["jobs"].values())
    print(f"C14 pre-change, {SMOKE_WORKFLOW}: {len(problems)} problems for {count} steps and 2 jobs")
    assert len(problems) == count + 2, problems
    text = refused(problems)
    assert "job plan declares no timeout-minutes" in text and "job smoke may run for 50 minutes" in text, text
    bundle = planted_copy(workflows["bundle.yml"])
    bundle["jobs"]["plan"].pop("timeout-minutes")
    assert "bundle.yml: job plan declares no timeout-minutes" in refused(bound_problems("bundle.yml", bundle))
