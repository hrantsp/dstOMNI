#!/usr/bin/env python3
"""Workspace entry point for the dual-stream transcription pipeline.

Runs every step that spans more than one repository: preparing the toolchain,
building, testing, packaging, and tagging all three repositories in lockstep.

    python dst.py doctor        what this machine is missing
    python dst.py setup         one-time toolchain preparation
    python dst.py build         build the desktop application
    python dst.py test          unit tests, plus the browser wire check
    python dst.py run           start the desktop application
    python dst.py package       build a distributable archive
    python dst.py status        git state across the workspace
    python dst.py version       tag all repositories with one version

Python rather than a shell script because the same file has to run on all three
platforms, and Python is already required — the protocol generator, Conan and
aqtinstall all depend on it. A bash script plus a PowerShell twin would be two
implementations to keep in step, which is exactly the drift this repository exists
to prevent. See decision 15 in DESIGN.md.
"""

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

OMNI = Path(__file__).resolve().parent
ROOT = OMNI.parent

REPOS = ["dstDESK", "dstORCH", "dstOMNI"]
DESK = ROOT / "dstDESK"
ORCH = ROOT / "dstORCH"
VENV = ROOT / ".venv"

WINDOWS = platform.system() == "Windows"


# ── plumbing ─────────────────────────────────────────────────────────────────


def say(message):
    print(message, flush=True)


def fail(message):
    print(f"error: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def venv_bin(name):
    """Executables live in Scripts on Windows and bin everywhere else."""
    folder = VENV / ("Scripts" if WINDOWS else "bin")
    suffix = ".exe" if WINDOWS else ""
    return folder / f"{name}{suffix}"


def tool(name):
    """Prefers the workspace virtual environment, falls back to PATH."""
    local = venv_bin(name)
    if local.exists():
        return str(local)
    found = shutil.which(name)
    return found if found else None


def run(args, cwd=None, check=True, capture=False):
    """No shell anywhere: quoting rules differ per platform and a path with a space
    in it is the most ordinary thing in the world on Windows and macOS."""
    result = subprocess.run(
        [str(a) for a in args],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture,
    )
    if check and result.returncode != 0:
        if capture and result.stderr:
            print(result.stderr, file=sys.stderr)
        fail(f"command failed ({result.returncode}): {' '.join(str(a) for a in args)}")
    return result


def git(repo, *args, check=True, capture=True):
    return run(["git", "-C", ROOT / repo, *args], check=check, capture=capture)


# ── targets ──────────────────────────────────────────────────────────────────


def default_target():
    system = platform.system()
    return {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}.get(system, "linux")


def load_target(name):
    """A target describes what genuinely varies between platforms, and nothing more.

    There is no cross-compilation: the deployment tools Qt ships only run on their own
    operating system, so a target is built on the machine it targets and the package
    travels. See decision 5 in DESIGN.md.
    """
    path = OMNI / "targets" / f"{name}.cfg"
    if not path.exists():
        available = sorted(p.stem for p in (OMNI / "targets").glob("*.cfg"))
        fail(f"unknown target '{name}'. Available: {', '.join(available)}")

    values = {}
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    values["_name"] = name
    return values


def require_host_target(target):
    """Building or packaging for another platform is not merely unsupported, it cannot
    work: the Qt deployment tools run only on their own operating system. Refusing here
    is better than failing partway through with a confusing error from cpack."""
    wanted = target.get("DST_PLATFORM")
    if wanted and wanted != platform.system():
        fail(f"target '{target['_name']}' builds on {wanted}, this machine is "
             f"{platform.system()}. There is no cross-compilation — build it there and "
             "let the package travel.")


# ── commands ─────────────────────────────────────────────────────────────────


def cmd_doctor(args):
    say("Workspace")
    for repo in REPOS:
        path = ROOT / repo
        mark = "ok  " if (path / ".git").is_dir() else "MISS"
        say(f"  {mark}  {repo:9s} {path}")

    say("\nToolchain")
    checks = [
        ("python", [sys.executable, "--version"], True),
        ("cmake", ["cmake", "--version"], True),
        ("conan", [tool("conan") or "conan", "--version"], True),
        ("git", ["git", "--version"], True),
        ("node", ["node", "--version"], False),
    ]
    missing = []
    for name, command, required in checks:
        if command[0] is None or (command[0] != sys.executable and shutil.which(command[0]) is None
                                  and not Path(command[0]).exists()):
            say(f"  {'MISS' if required else 'opt '}  {name}")
            if required:
                missing.append(name)
            continue
        version = run(command, check=False, capture=True).stdout.strip().splitlines()
        say(f"  ok    {name:6s} {version[0] if version else ''}")

    say("\nQt package")
    conan = tool("conan")
    if conan is None:
        say("  MISS  conan is not available, so Qt cannot be checked")
        missing.append("conan")
    else:
        found = run([conan, "list", "qt-official/*", "-c"], check=False, capture=True)
        say("  ok    qt-official is in the local cache" if "qt-official" in found.stdout
            else "  MISS  qt-official — run: python dst.py setup")

    if missing:
        say(f"\nMissing: {', '.join(missing)}. Run: python dst.py setup")
        return 1
    say("\nEverything required is present.")
    return 0


def cmd_setup(args):
    if not VENV.exists():
        say(f"Creating {VENV}")
        run([sys.executable, "-m", "venv", str(VENV)])

    pip = venv_bin("pip")
    say("Installing build tooling")
    run([pip, "install", "-q", "--upgrade", "pip"])
    run([pip, "install", "-q", "-r", str(OMNI / "requirements.txt")])

    conan = str(venv_bin("conan"))
    say("Detecting a Conan profile")
    run([conan, "profile", "detect", "--force"])

    say("Building the Qt package (downloads official binaries; slow the first time)")
    run([conan, "create", str(DESK / "rec" / "qt-official")])

    say("\nReady. Next: python dst.py build")
    return 0


def _preset(target):
    return f"conan-{target.get('DST_BUILD_TYPE', 'Release').lower()}"


def cmd_build(args):
    target = load_target(args.target)
    require_host_target(target)
    build_type = target.get("DST_BUILD_TYPE", "Release")
    conan = tool("conan")
    if conan is None:
        fail("conan not found — run: python dst.py setup")

    say(f"Resolving dependencies ({build_type})")
    run([conan, "install", ".", "-s", f"build_type={build_type}", "--build=missing"], cwd=DESK)

    say("Configuring")
    run(["cmake", "--preset", _preset(target)], cwd=DESK)

    say("Building")
    run(["cmake", "--build", "--preset", _preset(target)], cwd=DESK)

    say(f"\nBuilt into {DESK / 'bin' / build_type}")
    return 0


def cmd_test(args):
    target = load_target(args.target)

    say("Desktop unit tests")
    run(["ctest", "--preset", _preset(target), "--output-on-failure"], cwd=DESK)

    if shutil.which("node") is None:
        say("\nSkipping the browser wire check: node is not installed.")
        return 0

    say("\nBrowser wire check (needs the desktop app running)")
    say("  Start it in another terminal:  python dst.py run")
    say(f"  Then:  node {ORCH / 'tst' / 'wire-check.mjs'}")
    return 0


def _binary(target, name):
    build_type = target.get("DST_BUILD_TYPE", "Release")
    suffix = ".exe" if WINDOWS else ""
    return DESK / "bin" / build_type / f"{name}{suffix}"


def cmd_run(args):
    target = load_target(args.target)
    require_host_target(target)
    binary = _binary(target, "dstdesk")
    if not binary.exists():
        fail(f"{binary} does not exist — run: python dst.py build")

    command = [binary, "--port", target.get("DST_WS_PORT", "8765")]
    if args.extra:
        command += args.extra

    say(f"Running {binary.name}")
    return run(command, check=False).returncode


def cmd_package(args):
    target = load_target(args.target)
    require_host_target(target)
    build_type = target.get("DST_BUILD_TYPE", "Release")
    generator = target.get("DST_PACKAGE_GENERATOR", "TGZ")

    build_dir = DESK / "bin" / build_type
    if not build_dir.exists():
        fail("nothing built yet — run: python dst.py build")

    say(f"Packaging with CPack ({generator})")
    run(["cpack", "-G", generator, "-C", build_type], cwd=build_dir)
    say(f"\nArtifacts in {build_dir}")
    return 0


def _unpushed(repo):
    """Branches pushed without -u have no upstream, so asking for one fails rather
    than returning zero. Falling back to origin/<branch> keeps the answer truthful
    instead of reporting a confident nothing."""
    ahead = git(repo, "rev-list", "--count", "@{upstream}..HEAD", check=False).stdout.strip()
    if ahead:
        return ahead

    branch = git(repo, "branch", "--show-current", check=False).stdout.strip()
    ahead = git(repo, "rev-list", "--count", f"origin/{branch}..HEAD",
                check=False).stdout.strip()
    return f"{ahead} (no upstream)" if ahead else "unknown"


def cmd_status(args):
    for repo in REPOS:
        head = git(repo, "log", "-1", "--format=%h %s", check=False).stdout.strip()
        dirty = git(repo, "status", "--short", check=False).stdout.strip().splitlines()
        tag = git(repo, "describe", "--tags", "--abbrev=0", check=False).stdout.strip()
        say(f"{repo}")
        say(f"  {head}")
        say(f"  tag: {tag or '(none)':10s}  unpushed: {_unpushed(repo):8s}  "
            f"uncommitted: {len(dirty)}")
    return 0


def _highest_version():
    """The highest vX.Y.Z across every repository, so one that missed a tag cannot
    silently restart the numbering."""
    best = (0, 0, 0)
    for repo in REPOS:
        for line in git(repo, "tag", "--list", "v*", check=False).stdout.split():
            match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", line.strip())
            if match:
                best = max(best, tuple(int(g) for g in match.groups()))
    return best


def cmd_version(args):
    # Lockstep tagging is the whole reason this is three repositories rather than one:
    # "which extension works with which desktop build" has to have an answer.
    blocked = []
    for repo in REPOS:
        if git(repo, "status", "--short", check=False).stdout.strip():
            blocked.append(f"{repo} has uncommitted changes")
        if not git(repo, "remote", "get-url", "origin", check=False, capture=True).stdout.strip():
            blocked.append(f"{repo} has no origin remote")
    if blocked:
        fail("cannot tag:\n  " + "\n  ".join(blocked))

    if args.version:
        version = args.version if args.version.startswith("v") else f"v{args.version}"
        if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
            fail(f"'{version}' is not vMAJOR.MINOR.PATCH")
    else:
        major, minor, patch = _highest_version()
        version = f"v{major}.{minor}.{patch + 1}"

    message = args.message or f"Release {version}"
    say(f"Tagging {version} across {', '.join(REPOS)}")

    for repo in REPOS:
        existing = git(repo, "tag", "--list", version, check=False).stdout.strip()
        if existing:
            fail(f"{repo} already has {version}")

    for repo in REPOS:
        git(repo, "tag", "-a", version, "-m", message)
        say(f"  tagged {repo}")

    if args.push:
        for repo in REPOS:
            git(repo, "push", "origin", version, capture=False)
            say(f"  pushed {repo}")
    else:
        say("\nNot pushed. To publish:")
        for repo in REPOS:
            say(f"  git -C {repo} push origin {version}")
    return 0


# ── overview ─────────────────────────────────────────────────────────────────


def _target_summary():
    """One line per target: where it builds, what it produces, what it binds."""
    rows = []
    for path in sorted((OMNI / "targets").glob("*.cfg")):
        values = {}
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()

        rows.append((
            f"targets/{path.name}",
            values.get("DST_PLATFORM", "?"),
            values.get("DST_PACKAGE_GENERATOR", "?"),
            values.get("DST_WS_PORT", "?"),
            values.get("DST_PLATFORM") == platform.system(),
        ))
    return rows


def cmd_overview(parser):
    """Printed when the tool is run with no command.

    A bare invocation should show what there is to work with rather than an error: the
    targets that exist, which of them is this machine, and what every command does.
    """
    version = git("dstOMNI", "describe", "--tags", "--always", check=False).stdout.strip()
    say(f"dst — dual-stream transcription pipeline{'  ·  ' + version if version else ''}")

    # Which repositories are present, and whether they are clean. A missing sibling is
    # the first thing that goes wrong on a fresh clone, so it belongs on the first
    # screen rather than inside a subcommand.
    states = []
    for repo in REPOS:
        if not (ROOT / repo / ".git").is_dir():
            states.append(f"{repo} MISSING")
            continue
        dirty = git(repo, "status", "--short", check=False).stdout.strip()
        states.append(f"{repo}{'*' if dirty else ''}")

    say(f"      {ROOT}  ·  {'  '.join(states)}\n")

    targets = _target_summary()
    name_width = max(len(row[0]) for row in targets)
    host_width = max(len(row[1]) for row in targets)
    pack_width = max(len(row[2]) for row in targets)

    for name, host, package, port, is_host in targets:
        say(f"  {name:<{name_width}}  {host:<{host_width}}  {package:<{pack_width}}  "
            f"port {port}" + ("   ← this machine" if is_host else ""))
    say("")

    # Read back out of the parser, so a command cannot exist without appearing here.
    entries = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            entries = [(choice.dest, choice.help or "") for choice in action._choices_actions]

    width = max((len(name) for name, _ in entries), default=0)
    width = max(width, name_width)
    for name, help_text in entries:
        say(f"  {name:<{width}}  {help_text}")

    say("\n  python3 dst.py <command> [--target NAME]")
    return 0


# ── shell completion ─────────────────────────────────────────────────────────


def _completion_spec(parser):
    """Reads the command surface back out of the parser that defines it.

    argparse exposes this only through private attributes, which is a small ugliness
    bought for a large one avoided: hand-written completion scripts are a second
    description of the same CLI, and they drift the moment a flag is added. Generating
    them means the completion cannot disagree with the tool.
    """
    globals_ = []
    commands = {}

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                options = [opt for a in sub._actions for opt in a.option_strings]
                commands[name] = sorted(set(options))
        else:
            globals_ += action.option_strings

    return sorted(set(globals_)), commands


def _targets():
    return sorted(p.stem for p in (OMNI / "targets").glob("*.cfg"))


def _bash_completion(parser):
    globals_, commands = _completion_spec(parser)
    names = " ".join(sorted(commands))
    # Merged and deduplicated here rather than concatenated in the script, or the
    # global flags would appear twice in every per-command completion.
    cases = "\n".join(
        f'        {name}) opts="{" ".join(sorted(set(opts) | set(globals_)))}" ;;'
        for name, opts in sorted(commands.items()))

    return f"""# dst.py completion for bash. Generated by `dst.py completion bash`.
_dst_py_complete() {{
    local cur prev cmd opts ii
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"

    if [[ "$prev" == "--target" ]]; then
        COMPREPLY=($(compgen -W "{' '.join(_targets())}" -- "$cur"))
        return
    fi

    cmd=""
    for ((ii = 1; ii < COMP_CWORD; ii++)); do
        case "${{COMP_WORDS[ii]}}" in
            {'|'.join(sorted(commands))}) cmd="${{COMP_WORDS[ii]}}"; break ;;
        esac
    done

    if [[ -z "$cmd" ]]; then
        # Flags only once a dash has been typed. Mixing them into the first Tab buries
        # the commands, which are what a bare invocation is almost always reaching for.
        if [[ "$cur" == -* ]]; then
            COMPREPLY=($(compgen -W "{' '.join(globals_)}" -- "$cur"))
        else
            COMPREPLY=($(compgen -W "{names}" -- "$cur"))
        fi
        return
    fi

    case "$cmd" in
{cases}
        *) opts="" ;;
    esac
    COMPREPLY=($(compgen -W "$opts" -- "$cur"))
}}

# Completion binds to a command name, so this covers the ways the tool is invoked.
complete -F _dst_py_complete dst.py ./dst.py dst
"""


def _zsh_completion(parser):
    globals_, commands = _completion_spec(parser)
    described = " ".join(f"'{name}'" for name in sorted(commands))

    return f"""#compdef dst.py
# dst.py completion for zsh. Generated by `dst.py completion zsh`.
_dst_py() {{
  local -a commands targets
  commands=({described})
  targets=({' '.join(f"'{t}'" for t in _targets())})

  _arguments -C \\
    '--target[target descriptor]:target:->target' \\
    '1:command:->command' \\
    '*::arg:->args'

  case $state in
    target)  _describe 'target' targets ;;
    command) _describe 'command' commands ;;
  esac
}}
compdef _dst_py dst.py
"""


def _powershell_completion(parser):
    _, commands = _completion_spec(parser)
    names = ", ".join(f"'{name}'" for name in sorted(commands))

    return f"""# dst.py completion for PowerShell. Generated by `dst.py completion powershell`.
Register-ArgumentCompleter -Native -CommandName dst.py -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)
    $commands = @({names})
    $targets  = @({', '.join(f"'{t}'" for t in _targets())})

    if ($commandAst.ToString() -match '--target\\s+\\S*$') {{
        $targets | Where-Object {{ $_ -like "$wordToComplete*" }} |
            ForEach-Object {{ [System.Management.Automation.CompletionResult]::new($_) }}
        return
    }}

    $commands | Where-Object {{ $_ -like "$wordToComplete*" }} |
        ForEach-Object {{ [System.Management.Automation.CompletionResult]::new($_) }}
}}
"""


def cmd_completion(args):
    parser = build_parser()
    emit = {"bash": _bash_completion, "zsh": _zsh_completion,
            "powershell": _powershell_completion}[args.shell]
    print(emit(parser), end="")
    return 0


# ── entry point ──────────────────────────────────────────────────────────────


def build_parser():
    parser = argparse.ArgumentParser(
        prog="dst.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--target", default=default_target(),
                        help="target descriptor in targets/ (default: this machine)")

    # Not required: with no command the tool prints an overview rather than a usage
    # error, which is more useful than being told what one did wrong.
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor", help="report what this machine is missing").set_defaults(fn=cmd_doctor)
    sub.add_parser("setup", help="prepare the toolchain, once per machine").set_defaults(fn=cmd_setup)
    sub.add_parser("build", help="build the desktop application").set_defaults(fn=cmd_build)
    sub.add_parser("test", help="run the tests").set_defaults(fn=cmd_test)
    # No passthrough argument is declared: everything after `run` is collected in
    # order by parse_known_args below. Declaring a positional would split the tokens
    # between two lists and lose their order, so a flag could arrive without its value.
    run_parser = sub.add_parser("run", help="start the desktop application "
                                            "(unrecognised arguments are passed to it)")
    run_parser.set_defaults(fn=cmd_run)

    sub.add_parser("package", help="build a distributable archive").set_defaults(fn=cmd_package)
    sub.add_parser("status", help="git state across the workspace").set_defaults(fn=cmd_status)

    version_parser = sub.add_parser("version", help="tag every repository with one version")
    version_parser.add_argument("version", nargs="?", help="vX.Y.Z (default: next patch)")
    version_parser.add_argument("--message", help="annotation for the tag")
    version_parser.add_argument("--push", action="store_true", help="push the tags to origin")
    version_parser.set_defaults(fn=cmd_version)

    completion_parser = sub.add_parser("completion", help="print a shell completion script")
    completion_parser.add_argument("shell", choices=["bash", "zsh", "powershell"])
    completion_parser.set_defaults(fn=cmd_completion)

    return parser


def main():
    parser = build_parser()

    # Unknown arguments are forwarded to the application by `run`, and are an error
    # everywhere else. argparse cannot express this on its own: REMAINDER only starts
    # capturing at a positional, so `dst.py run --headless` has its first token matched
    # as a flag of this tool and rejected before it can be passed on.
    args, unknown = parser.parse_known_args()

    if args.command is None:
        raise SystemExit(cmd_overview(parser))

    if args.command == "run":
        args.extra = [a for a in unknown if a != "--"]
    elif unknown:
        parser.error(f"unrecognized arguments: {' '.join(unknown)}")

    raise SystemExit(args.fn(args))


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # Piping into head or less closes the pipe as soon as it has enough, which is
        # ordinary use rather than a fault. Python would otherwise print a traceback
        # and flush again on exit, printing a second one.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        raise SystemExit(0)
    except KeyboardInterrupt:
        raise SystemExit(130)
