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
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import sys
from pathlib import Path

OMNI = Path(__file__).resolve().parent
ROOT = OMNI.parent

REPOS = ["dstDESK", "dstORCH", "dstOMNI"]
DESK = ROOT / "dstDESK"
ORCH = ROOT / "dstORCH"
VENV = ROOT / ".venv"

SANITIZE_BUILD = "Sanitize"

WINDOWS = platform.system() == "Windows"
MACOS   = platform.system() == "Darwin"


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


def workspace_env(extra_path=None):
    """The environment for anything this script spawns, with .venv's tools in front.

    setup installs cmake and ninja into .venv, and Conan builds dependencies by invoking
    cmake from PATH — so without this, a machine with no system cmake fails inside a
    dependency's build with "cmake: command not found", naming the dependency rather than
    the cause. It also means a Ninja generator finds the ninja that setup installed
    instead of depending on one happening to be present.
    """
    env = dict(os.environ)
    ahead = [str(VENV / ("Scripts" if WINDOWS else "bin"))]
    if extra_path:
        ahead = [extra_path] + ahead
    env["PATH"] = os.pathsep.join(ahead + [env.get("PATH", "")])
    return env


def run(args, cwd=None, check=True, capture=False, env=None):
    """No shell anywhere: quoting rules differ per platform and a path with a space
    in it is the most ordinary thing in the world on Windows and macOS."""
    result = subprocess.run(
        [str(a) for a in args],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture,
        env=env if env is not None else workspace_env(),
    )
    if check and result.returncode != 0:
        if capture and result.stderr:
            print(result.stderr, file=sys.stderr)
        fail(f"command failed ({result.returncode}): {' '.join(str(a) for a in args)}")
    return result


def _terminate_tree(process):
    """Ends the child and everything it started.

    Conan runs aqtinstall, which runs download threads of its own, so ending only the
    process we hold leaves the download going with nothing reading it. Windows has no
    process group to signal, hence taskkill and its /T. On POSIX the terminal has
    already delivered the interrupt to the whole foreground group, so terminate() here
    is a backstop for a child that ignored it.
    """
    if process.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def run_with_progress(args, label, cwd=None):
    """Runs a long command, showing that it is alive and what it last said.

    aqtinstall logs only when a whole archive finishes, so a large one can be quiet for
    minutes. An earlier version of this sized the directory being filled, which was
    wrong four ways at once: it summed the download and the package copy as though they
    were separate progress, matched leftover packages from previous runs, went negative
    when Conan deleted the tree its baseline was taken from, and — worst — walked tens
    of thousands of files every two seconds, which on Windows contends for the disk with
    the install it is measuring. A clock that costs nothing is better than a number that
    is wrong and slows the thing it reports on.

    Output is kept and shown only on failure, so the line is not buried under a log.
    """
    log = []
    latest = [""]   # most recent output line, shown beside the counter
    process = subprocess.Popen(
        [str(a) for a in args],
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=workspace_env(),
    )

    stop = threading.Event()
    started = time.monotonic()
    live = sys.stdout.isatty()

    def report():
        spoken, at = None, 0.0
        while not stop.wait(1.0):
            seconds = time.monotonic() - started
            elapsed = int(seconds)

            # The tool's own last line is the only honest progress signal available:
            # aqtinstall logs when an archive finishes and says nothing in between, so
            # the largest one is several quiet minutes. Everything else here would be a
            # guess about where it writes, which is the mistake this replaced.
            note = latest[0][:70]
            line = (f"  {label}: {elapsed // 60}m{elapsed % 60:02d}s"
                    + (f"  {note}" if note else ""))

            if live:
                # Overwrite in place; a terminal shows one line that keeps moving.
                print(f"\r{line:<110}", end="", flush=True)
            elif note != spoken or seconds - at >= 30:
                # Piped to a file, the same heartbeat appends. Print when the tool
                # actually says something new, and otherwise sparely, just often enough
                # to show the run is alive — a line a second buries the log it is in.
                print(line, flush=True)
                spoken, at = note, seconds

    reporter = threading.Thread(target=report, daemon=True)
    reporter.start()

    def drain():
        for line in process.stdout:
            log.append(line)
            stripped = line.strip()
            if stripped:
                latest[0] = stripped

    # Reading the pipe belongs on its own thread so the main one can sit in a timed
    # wait. Python runs a signal handler between bytecodes and never during a blocking
    # read, so a main thread parked on the pipe swallows Ctrl+C entirely: the download
    # carries on and the only way out is closing the window. Waking twice a second
    # costs nothing and makes the interrupt land.
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    try:
        while True:
            try:
                process.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                continue
    except KeyboardInterrupt:
        stop.set()
        if live:
            print()
        say(f"Stopping {label}…")
        _terminate_tree(process)
        fail(f"{label} interrupted")

    reader.join(timeout=3)
    stop.set()
    reporter.join(timeout=3)
    if live:
        print()

    if process.returncode != 0:
        print("".join(log[-40:]), file=sys.stderr)
        fail(f"{label} failed ({process.returncode})")


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
    # The third field is who supplies it. setup installs into .venv and nothing
    # system-wide, so it can provide Conan and aqtinstall and cannot provide a compiler,
    # cmake, git or Python itself. Recording that here is what stops the summary below
    # telling someone to run setup for something setup will not install.
    SETUP, YOU, OPTIONAL = "setup", "you", None
    checks = [
        ("python", [sys.executable, "--version"], YOU),
        ("cmake", [tool("cmake") or "cmake", "--version"], SETUP),
        ("conan", [tool("conan") or "conan", "--version"], SETUP),
        ("git", ["git", "--version"], YOU),
        ("node", ["node", "--version"], OPTIONAL),
    ]
    missing, yours = [], []
    for name, command, required in checks:
        if command[0] is None or (command[0] != sys.executable and shutil.which(command[0]) is None
                                  and not Path(command[0]).exists()):
            say(f"  {'MISS' if required else 'opt '}  {name}")
            if required == YOU:
                yours.append(name)
            elif required == SETUP:
                missing.append(name)
            continue
        version = run(command, check=False, capture=True).stdout.strip().splitlines()
        say(f"  ok    {name:6s} {version[0] if version else ''}")

    if WINDOWS:
        say("\nWindows")
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"SYSTEM\CurrentControlSet\Control\FileSystem")
            enabled = winreg.QueryValueEx(key, "LongPathsEnabled")[0]
        except OSError:
            enabled = 0

        if enabled:
            say("  ok    long paths are enabled")
        else:
            # Advisory, not a blocker. Measured, the installed tree reaches about 200
            # characters against the 260-character limit, so this is headroom rather
            # than a requirement — and failing a reviewer's first command over a
            # registry setting they do not need would be worse than the problem.
            say("  note  long paths are disabled. Not required: the Qt install used")
            say("        here stays within the 260-character limit. Enable it only if")
            say("        an extraction fails on a path length:")
            say(r'        reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" '
                r"/v LongPathsEnabled /t REG_DWORD /d 1 /f")

    say("\nQt package")
    conan = tool("conan")
    if conan is None:
        # Already counted by the toolchain check above; naming it twice in the summary
        # reads like two separate problems.
        say("  MISS  conan is not available, so Qt cannot be checked")
    else:
        # Deliberately name-only, and worded to match. doctor is a read-only report of
        # what is on the machine, so it does not export the recipe to work out this
        # configuration's package id — setup does that and decides precisely. Saying
        # "a build of" keeps the weaker claim honest rather than implying the cached
        # package is the one this recipe currently describes.
        cached = run([conan, "list", "qt-official/*:*"], check=False, capture=True)
        say("  ok    a build of qt-official is in the local cache"
            if "qt-official" in cached.stdout
            else "  MISS  qt-official — run: python dst.py setup")

    if yours:
        say(f"\nInstall yourself: {', '.join(dict.fromkeys(yours))} — machine "
            f"prerequisites, which setup cannot provide because it installs into .venv "
            f"and nothing system-wide.")

        # The command, not a pointer to a document. Someone reading this is at a prompt
        # with something missing, and the distance between the diagnosis and the fix is
        # the whole value of the command.
        hints = {
            "Darwin": {
                "cmake":  "brew install cmake      (or the package from cmake.org)",
                "git":    "xcode-select --install  (git and python3 come with it)",
                "python": "xcode-select --install  (git and python3 come with it)",
            },
            "Windows": {
                "cmake":  "winget install Kitware.CMake",
                "git":    "winget install Git.Git",
                "python": "winget install Python.Python.3.12",
            },
        }.get(platform.system(), {
            "cmake":  "your package manager, e.g. sudo apt install cmake",
            "git":    "your package manager, e.g. sudo apt install git",
            "python": "your package manager, e.g. sudo apt install python3",
        })

        for name in dict.fromkeys(yours):
            if name in hints:
                say(f"        {name:7s} {hints[name]}")
        say("\n        The platform notes in dstOMNI/README.md have the full list.")
    if missing:
        say(f"\nRun: python dst.py setup — it provides "
            f"{', '.join(dict.fromkeys(missing))}.")
    if yours or missing:
        return 1
    say("\nEverything required is present.")
    return 0


def qt_reference(conan):
    """Exports the recipe and returns the exact reference, revision included.

    Exporting is cheap — it copies the recipe into the cache and builds nothing — and
    it is what lets Conan itself answer the "is it already built?" question below.
    """
    exported = run([conan, "export", str(DESK / "rec" / "qt-official"),
                    "--format=json"], capture=True)
    return json.loads(exported.stdout)["reference"]


def cmd_setup(args):
    if not VENV.exists():
        say(f"Creating {VENV}")
        run([sys.executable, "-m", "venv", str(VENV)])

    # Through the interpreter, not the pip executable: on Windows pip.exe cannot
    # replace itself while running, and fails with an error telling you to do exactly
    # this instead.
    python = venv_bin("python")
    say("Installing build tooling")
    run([python, "-m", "pip", "install", "-q", "--upgrade", "pip"])
    run([python, "-m", "pip", "install", "-q", "-r", str(OMNI / "requirements.txt")])

    conan = str(venv_bin("conan"))

    # Asked, not assumed. Conan's home is CONAN_HOME when set and differs by platform
    # otherwise, so a hardcoded ~/.conan2 finds a profile that the build will not use —
    # setup then skips detection and the build fails for want of a profile.
    home = run([conan, "config", "home"], check=False, capture=True).stdout.strip()
    if not home:
        home = str(Path.home() / ".conan2")

    # --force overwrites an existing profile, and a profile is a thing people edit by
    # hand — pinning a compiler version, say. Re-running setup should not destroy that.
    profile = Path(home) / "profiles" / "default"
    if profile.exists() and not args.force:
        say(f"Using the existing Conan profile at {profile}")
    else:
        say("Detecting a Conan profile")
        run([conan, "profile", "detect", "--force"])

    # Installed by reference rather than created. `conan create` always rebuilds, so it
    # needed a guard, and the guard asked whether *a* Qt was cached — a question that
    # gives the wrong answer the moment the recipe's options change, because the package
    # already there was built from different ones. Conan compares the full package id
    # and answers exactly: present means present, and it is a no-op in about a second.
    reference = qt_reference(conan)
    say("Fetching the official Qt binaries if they are not already cached — about")
    say("200 MB, since only the modules this application links are installed.")
    say("aqtinstall reports an archive only once it has finished, and qtbase is by far")
    say("the largest, so expect several quiet minutes with only the clock moving. On")
    say("Windows, Conan then copies the tree into its package folder, which is slower")
    say("still. Neither is a hang.")
    with tempfile.TemporaryDirectory() as scratch:
        run_with_progress(
            [conan, "install", f"--requires={reference}",
             # --force means this one package, not everything it might pull in.
             "--build=qt-official/*" if args.force else "--build=missing",
             "--output-folder", scratch],
            label="Qt",
        )

    say("\nReady. Next: python dst.py build")
    return 0


def _presets(build_type):
    """What Conan actually generated, rather than what it generates on Linux.

    Preset names depend on the CMake generator, not on us. A single-config generator
    (Ninja, Unix Makefiles) yields one configure preset per build type — conan-release.
    A multi-config one (Visual Studio, Xcode) yields a single shared configure preset
    called conan-default plus separate build presets, and puts the build tree one level
    up. Assuming the single-config shape is how a Windows build fails on its first
    command.

    Returns (configurePreset, buildPreset, binaryDir).
    """
    import json

    user = DESK / "CMakeUserPresets.json"
    if not user.exists():
        fail("no CMake presets yet — run: python dst.py build")

    documents = []
    root = json.loads(user.read_text())
    for included in root.get("include", []):
        path = (DESK / included) if not Path(included).is_absolute() else Path(included)
        if path.exists():
            documents.append(json.loads(path.read_text()))
    if not documents:
        documents.append(root)

    wanted = f"conan-{build_type.lower()}"
    configures, builds = {}, set()
    for document in documents:
        for preset in document.get("configurePresets", []):
            configures[preset["name"]] = preset.get("binaryDir")
        for preset in document.get("buildPresets", []):
            builds.add(preset["name"])

    configure = (wanted if wanted in configures
                 else "conan-default" if "conan-default" in configures
                 else next(iter(configures), None))
    if configure is None:
        fail("Conan generated no configure preset; try: python dst.py build --refresh")

    build = wanted if wanted in builds else configure
    return configure, build, configures.get(configure)


def cmd_build(args):
    target = load_target(args.target)
    require_host_target(target)
    build_type = target.get("DST_BUILD_TYPE", "Release")
    conan = tool("conan")
    if conan is None:
        fail("conan not found — run: python dst.py setup")

    # Checked here and not only in doctor, because without it the failure arrives some
    # seconds later from inside a dependency's own build — "/bin/sh: cmake: command not
    # found" while compiling zlib — which names the wrong thing entirely. Conan pulls a
    # cmake of its own for recipes that ask for one, so its presence in the log is not
    # evidence that this machine has one.
    cmake = tool("cmake")
    if cmake is None:
        fail("cmake not found — run: python dst.py setup")

    def step(label, argv):
        """Quiet by default.

        A dependency Conan has to build from source prints thousands of compiler lines,
        and only the last one says where it has got to. The whole log is kept and shown
        if the step fails, which is when it is worth reading; --verbose streams it.
        """
        if args.verbose:
            run(argv, cwd=DESK)
        else:
            run_with_progress(argv, label=label, cwd=DESK)

    say(f"Resolving dependencies ({build_type})")
    step("Dependencies",
         [conan, "install", ".", "-s", f"build_type={build_type}", "--build=missing"])

    configure, build, _ = _presets(build_type)

    say(f"Configuring ({configure})")
    step("Configure", [cmake, "--preset", configure])

    say(f"Building ({build})")
    step("Compile", [cmake, "--build", "--preset", build])

    say(f"\nBuilt into {DESK / 'bin' / build_type}")

    if getattr(args, "sanitize", False):
        _build_sanitized(cmake, build_type, step)
    return 0


def _sanitize_dir():
    return DESK / "bin" / SANITIZE_BUILD


def _build_sanitized(cmake, build_type, step):
    """A second build tree, instrumented, beside the ordinary one.

    A separate directory rather than an option on the release tree, for two reasons.
    A sanitized binary is two to three times slower and links a runtime that must not
    reach a user, so it must not be possible to package one by forgetting to
    reconfigure. And it reuses the dependencies Conan already resolved for the release
    build, so this costs one compile of this project's own sources rather than a second
    Qt — which is what makes it cheap enough to actually run.
    """
    _, _, binary_dir = _presets(build_type)
    toolchain = Path(binary_dir or (DESK / "bin" / build_type)) / "generators" / "conan_toolchain.cmake"
    if not toolchain.exists():
        fail(f"no Conan toolchain at {toolchain} — run: python dst.py build")

    target_dir = _sanitize_dir()
    say(f"\nConfiguring the sanitizer build ({target_dir.name})")
    step("Configure", [cmake, "-S", DESK, "-B", target_dir,
                       f"-DCMAKE_TOOLCHAIN_FILE={toolchain}",
                       f"-DCMAKE_BUILD_TYPE={build_type}",
                       "-DKOBAYASHI_SANITIZE=ON"])

    say("Building the sanitizer build")
    step("Compile", [cmake, "--build", target_dir])
    say(f"\nBuilt into {target_dir}\n"
        "Run the suite against it with: python dst.py test --sanitize")


def _wait_for_port(port, process, what, timeout=30):
    """Waits for something to accept connections, rather than sleeping a guessed
    interval: a cold start behind a virus scanner takes far longer than a warm one, and
    a fixed sleep is either slow every time or flaky on the machine that needed it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            fail(f"{what} exited before it was ready ({process.returncode})")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    fail(f"{what} did not start listening on {port} within {timeout}s")


_HANDED_OUT_PORTS = set()


def _free_port():
    """A port nobody else is on, so the checks below cannot collide with a running app
    or with each other.

    The socket is closed before the number is handed out — it has to be, or the process
    that is meant to bind it could not — so the kernel is free to offer the same one to
    the next call. It usually rotates, which is why this held together, but "usually" in
    a check that starts two servers is a flake waiting for a slow machine. Numbers
    already given away are refused here rather than being discovered as "the desktop
    application exited before it was ready"."""
    for _ in range(64):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ss:
            ss.bind(("127.0.0.1", 0))
            port = ss.getsockname()[1]
        if port not in _HANDED_OUT_PORTS:
            _HANDED_OUT_PORTS.add(port)
            return port
    fail("could not find a free loopback port")


def _protocol_checks(target):
    """Runs the two socket-level checks against a server this function starts itself.

    They were previously a printed instruction — "start the app in another terminal,
    then run this" — which meant they were run when someone remembered to, which was
    almost never. abuse.mjs is the only thing that checks the MUSTs in PROTOCOL.md §5.3
    are implemented at all, and it sat unreferenced by any document or command for its
    whole life. A check that is not in a command is not a check.
    """
    if shutil.which("node") is None:
        say("\nSkipping the protocol checks: node is not installed.")
        return 0

    binary = _binary(target, "kobayashi")
    if not binary.exists():
        say(f"\nSkipping the protocol checks: {binary.name} is not built.")
        return 0

    port = _free_port()
    output = Path(tempfile.mkdtemp(prefix="dst-protocol-"))

    environment = workspace_env()
    # Headless needs no display, and offscreen keeps it that way on a machine that has
    # one but is running this over ssh.
    environment["QT_QPA_PLATFORM"] = "offscreen"

    say(f"\nProtocol checks (server on 127.0.0.1:{port})")
    diagnostics = _diagnostics(target, "protocol-server")
    server = subprocess.Popen(
        [str(binary), "--headless", "--no-record", "--no-transcribe",
         "--port", str(port), "--output", str(output)],
        stdout=subprocess.DEVNULL, stderr=diagnostics, env=environment)

    failures = 0
    try:
        # Wait for the port rather than sleeping a guessed interval: a cold start behind
        # a virus scanner takes far longer than a warm one, and a fixed sleep is either
        # slow every time or flaky on the machine that needed it.
        _wait_for_port(port, server, "the desktop application")

        for label, script in (("server conformance", DESK / "tst" / "abuse.mjs"),
                              ("browser wire format", ORCH / "tst" / "wire-check.mjs")):
            say(f"  {label}")
            result = run(["node", str(script), "--port", str(port)], check=False)
            if result.returncode != 0:
                failures += 1
    finally:
        _terminate_tree(server)
        _close_diagnostics(diagnostics)
        shutil.rmtree(output, ignore_errors=True)

    return failures



def _session_checks(target):
    """Runs tst/sessions.mjs against a server that records and transcribes.

    The protocol checks above start the server with --no-record --no-transcribe, which
    is exactly the configuration in which the two faults this covers cannot happen: one
    needs files on disk to overwrite, the other needs a transcription connection
    outstanding so that teardown waits rather than completing inside the call. So this
    is a second server, deliberately configured the way a reviewer runs one.
    """
    if shutil.which("node") is None:
        say("\nSkipping the session checks: node is not installed.")
        return 0

    kobayashi = _binary(target, "kobayashi")
    mock      = _binary(target, "kobayashi-mockstt")
    for tool_path in (kobayashi, mock):
        if not tool_path.exists():
            say(f"\nSkipping the session checks: {tool_path.name} is not built.")
            return 0

    stt_port = _free_port()
    ws_port  = _free_port()
    output   = Path(tempfile.mkdtemp(prefix="dst-sessions-"))

    environment = workspace_env()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["DEEPGRAM_API_KEY"] = "mock-key"

    say(f"\nSession lifetime (server on 127.0.0.1:{ws_port}, recording on)")
    engine_diagnostics = _diagnostics(target, "sessions-engine")
    app_diagnostics     = _diagnostics(target, "sessions-server")
    engine = subprocess.Popen([str(mock), "--port", str(stt_port)],
                              stdout=subprocess.DEVNULL, stderr=engine_diagnostics,
                              env=environment)
    app = None
    try:
        _wait_for_port(stt_port, engine, "the mock transcription service")
        app = subprocess.Popen(
            [str(kobayashi), "--headless", "--port", str(ws_port),
             "--output", str(output), "--stt-endpoint", f"ws://127.0.0.1:{stt_port}"],
            stdout=subprocess.DEVNULL, stderr=app_diagnostics, env=environment)
        _wait_for_port(ws_port, app, "the desktop application")

        result = run(["node", str(DESK / "tst" / "sessions.mjs"),
                      "--port", str(ws_port), "--output", str(output)], check=False)
        return 1 if result.returncode != 0 else 0
    finally:
        if app is not None:
            _terminate_tree(app)
        _terminate_tree(engine)
        _close_diagnostics(app_diagnostics)
        _close_diagnostics(engine_diagnostics)
        shutil.rmtree(output, ignore_errors=True)


TRANSCRIPT_LINE = re.compile(r"^\s+(\d+):(\d+\.\d+)\s+(Microphone|Meeting)\s+(.*)$")


def _run_session(target, mock_args, seconds, label, sim_args=()):
    """Runs one recorded session against the mock transcription service and returns its
    output, the mock's output, and the parsed transcript."""
    kobayashi = _binary(target, "kobayashi")
    sim       = _binary(target, "kobayashi-sim")
    mock      = _binary(target, "kobayashi-mockstt")

    for tool_path in (kobayashi, sim, mock):
        if not tool_path.exists():
            say(f"\nSkipping the reconnect check: {tool_path.name} is not built.")
            return None

    stt_port = _free_port()
    ws_port  = _free_port()
    output   = Path(tempfile.mkdtemp(prefix="dst-reconnect-"))

    environment = workspace_env()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    # Any non-empty value: the mock does not check it, and transcription follows the key
    # rather than a flag, so without one the application would record and nothing else.
    environment["DEEPGRAM_API_KEY"] = "mock-key"

    say(f"  {label}")
    engine = subprocess.Popen(
        [str(mock), "--port", str(stt_port)] + mock_args,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment)

    app = None
    try:
        _wait_for_port(stt_port, engine, "the mock transcription service")

        app = subprocess.Popen(
            [str(kobayashi), "--headless", "--no-record", "--port", str(ws_port),
             "--output", str(output), "--stt-endpoint", f"ws://127.0.0.1:{stt_port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment)
        _wait_for_port(ws_port, app, "the desktop application")

        run([str(sim), "--port", str(ws_port), "--seconds", str(seconds), *sim_args],
            check=False, capture=True)

        # The engine reports a second of audio only once it has all of it, and a retry
        # sequence outlives the audio that provoked it, so the tail needs room.
        time.sleep(6)
    finally:
        if app is not None:
            _terminate_tree(app)
        _terminate_tree(engine)
        shutil.rmtree(output, ignore_errors=True)

    app_output    = app.stdout.read() if app is not None else ""
    engine_output = engine.stdout.read()

    # This one merges stderr into stdout and reads it here, so there is nothing to
    # redirect — but a sanitizer report is still in that text, and _collect_sanitizer_
    # reports only looks at files. Keep a copy where it will be found.
    sanitizer_dir = target.get("DST_SANITIZER_DIR")
    if sanitizer_dir:
        stem = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        (Path(sanitizer_dir) / f"session-{stem}-app.stderr").write_text(app_output)
        (Path(sanitizer_dir) / f"session-{stem}-engine.stderr").write_text(engine_output)

    transcript = []
    for line in app_output.splitlines():
        hit = TRANSCRIPT_LINE.match(line)
        if hit:
            minutes, seconds_part, stream, text = hit.groups()
            transcript.append((int(minutes) * 60 + float(seconds_part), stream, text))

    return app_output, engine_output, transcript


def _reconnect_check(target):
    """Drives the transcription client against a service that drops connections.

    SttClient's reconnection only runs when a connection dies unexpectedly, and the live
    service will not do that to order — so this is the only place that code is exercised
    at all. Three separate faults in it were found this way and none of them by reading
    it: an origin taken from after the buffered audio rather than its front, a drop
    treated as a close so the stream never came back, and a retry budget reset by any
    result so a flapping link reconnected forever.
    """
    problems = []

    # ── a single blip: it must come back, and land on the same clock ────────────
    result = _run_session(target, ["--drop-after", "3"], 12, "recovering from one drop")
    if result is None:
        return 0
    app_output, engine_output, transcript = result

    mic     = [entry for entry in transcript if entry[1] == "Microphone"]
    meeting = [entry for entry in transcript if entry[1] == "Meeting"]

    # The mock numbers connections across both streams, so "which connection" has to be
    # read out of the text rather than assumed.
    def connection_of(text):
        hit = re.search(r"connection (\d+)", text)
        return int(hit.group(1)) if hit else None

    mic_connections = [connection_of(entry[2]) for entry in mic]
    mic_connections = [cc for cc in mic_connections if cc is not None]

    if engine_output.count("opened") < 3:
        problems.append("the client never opened a replacement connection after the drop")
    if not any("resumed" in line for line in app_output.splitlines()):
        problems.append("reconnection was not reported to the user")
    if "gave up" in app_output:
        problems.append("the client gave up on a single recoverable drop")

    first = mic_connections[0] if mic_connections else None
    after = [entry for entry in mic if connection_of(entry[2]) not in (None, first)]

    if len(set(mic_connections)) < 2:
        problems.append("the microphone transcript never resumed on a new connection")
    elif not after:
        problems.append("no transcript arrived on the reconnected microphone stream")
    if len(meeting) < 5:
        problems.append(f"the uninterrupted stream produced only {len(meeting)} lines — "
                        "an outage on one stream must not stall the other")

    # Ordering is the property the whole application rests on, so it is checked over the
    # merged output rather than per stream.
    starts = [entry[0] for entry in transcript]
    if starts != sorted(starts):
        problems.append("committed transcript went backwards in time")

    # And the alignment. Both streams carry the same audio through the same machinery, so
    # their results sit on the same one-second grid; a reconnected stream whose origin is
    # taken from the wrong place lands off it by however long the outage lasted. This is
    # the check that catches that, and nothing else here would.
    if after and meeting:
        grid = meeting[0][0] % 1.0
        for position, _, text in after:
            offset = abs((position % 1.0) - grid)
            offset = min(offset, 1.0 - offset)
            if offset > 0.25:
                problems.append(
                    f"reconnected stream is {offset:.2f}s off the shared clock "
                    f"({text!r} at {position:.2f}s, grid at {grid:.2f}s)")
                break

    # ── a link that will not stay up: it must stop, and say so ──────────────────
    # 1.5 s, not 1 s: the mock reports a result at each whole second and drops after the
    # threshold, so a connection that dies at exactly 1 s never delivers anything. It has
    # to deliver something and *then* die, or the case this guards — a budget reset by any
    # result, so a flapping link reconnects forever — is never reached and the check
    # passes over the bug it exists for.
    result = _run_session(target, ["--drop-after", "1.5", "--drop-all"], 30,
                          "giving up on a link that keeps dropping")
    if result is None:
        return 0
    app_output, engine_output, _ = result

    if "gave up after" not in app_output:
        problems.append("the client never gave up on a link that dropped every second")

    opened = engine_output.count("opened")
    # One initial connection plus five retries, per stream, and a little slack for the
    # session teardown. The failure this bounds was measured at 34 in 28 seconds.
    if opened > 16:
        problems.append(f"reconnect storm: {opened} connections were opened")

    for problem in problems:
        say(f"    FAIL  {problem}")

    if problems:
        return 1

    say("    ok    recovers from a drop, stays on the shared clock, and stops when it should")
    return 0


def _silent_stream_check(target):
    """One stream open and silent must not hold the other's transcript back.

    The extension opens both streams as soon as the handshake completes, whether or not
    the tab has any audio to give. A stream that is open is part of the merge watermark;
    a stream that never sends a frame can never advance it — so the microphone was
    transcribed perfectly and not one line of it reached the screen until capture
    stopped, at which point the whole call arrived at once. Measured at 25 s of nothing
    followed by 24 lines in 110 ms.

    The check is on *when*, not on *whether*: the end-of-session flush produces the same
    lines either way, so a test that only counts them passes over the fault.
    """
    result = _run_session(target, [], 20, "a stream that opens and never speaks",
                          sim_args=["--quiet-meeting"])
    if result is None:
        return 0
    app_output, _, transcript = result

    lines = app_output.splitlines()
    stopped = next((ii for ii, line in enumerate(lines)
                    if "Stream Microphone closed" in line), len(lines))
    during = [ii for ii, line in enumerate(lines[:stopped]) if TRANSCRIPT_LINE.match(line)]

    if not transcript:
        say("    FAIL  nothing was transcribed at all")
        return 1
    if not during:
        say(f"    FAIL  all {len(transcript)} lines arrived after capture stopped — a "
            "silent open stream froze the transcript for the whole session")
        return 1

    say(f"    ok    {len(during)} of {len(transcript)} lines were committed during the "
        "session, not held to the end")
    return 0


def _diagnostics(target, label):
    """Where a child process's stderr goes.

    /dev/null normally — a background server's stderr is noise. When sanitizing it is
    the opposite: a sanitizer report is the entire point of the run, and most of what
    runs below is started detached with nowhere else to put one. Returns a file object
    the caller must close.
    """
    directory = target.get("DST_SANITIZER_DIR")
    if not directory:
        return subprocess.DEVNULL
    return open(Path(directory) / f"{label}.stderr", "w")


def _close_diagnostics(handle):
    if handle is not subprocess.DEVNULL:
        handle.close()


# What a sanitizer says when it has something to say. Matched as text because the two
# runtimes do not agree on where to put it — see _arm_sanitizer_reports.
SANITIZER_MARKERS = ("AddressSanitizer", "LeakSanitizer", "ThreadSanitizer",
                     "runtime error:")


def _arm_sanitizer_reports():
    """Sends every sanitizer report to a file, so a finding cannot be lost.

    By default both runtimes write to stderr, and most of what runs below is started
    detached with its stderr discarded — a background server, the mock engine, the
    simulator. A run could therefore report "all checks passed" over a use-after-free
    nobody saw, which is the failure mode this option exists to remove.

    Two mechanisms, because the runtimes do not behave the same way and the difference
    is not documented anywhere useful. **ASan honours log_path; GCC's libubsan ignores
    it and writes to stderr regardless.** Measured, after a deliberately injected
    out-of-bounds read produced a UBSan report that this function's first version
    missed entirely — and UBSan is what fires first for exactly that fault. So the
    environment is set for ASan's benefit, and every child's stderr is captured for
    UBSan's.

    Not on Windows: ASAN_OPTIONS separates its settings with colons and a Windows path
    carries one after the drive letter, so log_path cannot be expressed there. The
    captured stderr still works, which is why it is the mechanism that covers both.
    """
    directory = Path(tempfile.mkdtemp(prefix="dst-sanitizer-"))
    if not WINDOWS:
        os.environ["ASAN_OPTIONS"] = f"detect_leaks=1:log_path={directory / 'asan'}"
    os.environ["UBSAN_OPTIONS"] = "print_stacktrace=1"
    return directory


def _collect_sanitizer_reports(directory):
    """Every file here is a sanitizer report from something this run started."""
    if directory is None:
        return 0

    guilty = []
    for report in sorted(directory.glob("*")):
        text = report.read_text(errors="replace")
        # An ASan log file exists only when there is something in it. A captured stderr
        # exists either way, so it counts only if a runtime actually said something.
        if report.suffix == ".stderr" and not any(m in text for m in SANITIZER_MARKERS):
            continue
        guilty.append((report, text))

    if not guilty:
        say("    ok    no sanitizer reports from any process this run started")
        shutil.rmtree(directory, ignore_errors=True)
        return 0

    # One fault seen by four processes is one fault. Grouped by the line the runtime
    # actually printed, so the summary counts problems rather than witnesses.
    faults = {}
    for report, text in guilty:
        lines = text.splitlines()
        headline = next((line for line in lines
                         if any(m in line for m in SANITIZER_MARKERS)), report.name)
        # Addresses differ between processes; the fault does not. Grouping on the
        # address would report one bug five times.
        key = re.sub(r"0x[0-9a-f]+", "0x…", headline)
        faults.setdefault(key, []).append((report.name, lines))

    say(f"    FAIL  {len(faults)} sanitizer finding(s) across {len(guilty)} process(es):")
    for headline, seen in faults.items():
        names = ", ".join(name for name, _ in seen)
        say(f"      {headline}")
        # Only this project's own frames. The rest is Qt's template machinery and the
        # event loop, which is the same twenty lines under every finding.
        for line in seen[0][1]:
            if line.lstrip().startswith("#") and str(DESK / "src") in line:
                say(f"        {line.strip()}")
        say(f"        seen by: {names}")
        say("")
    say(f"    full reports kept in {directory}")
    return len(faults)


def cmd_test(args):
    target = load_target(args.target)
    sanitize = getattr(args, "sanitize", False)

    _, build, _ = _presets(target.get("DST_BUILD_TYPE", "Release"))

    reports = None
    if sanitize:
        directory = _sanitize_dir()
        if not directory.exists():
            fail(f"no sanitizer build at {directory} — run: python dst.py build --sanitize")
        # Every check below resolves its binaries through _binary, which reads this and
        # then looks nowhere else.
        reports = _arm_sanitizer_reports()
        target = dict(target, DST_BINARY_DIR=str(directory),
                              DST_SANITIZER_DIR=str(reports))
        say("Desktop unit tests (sanitized)")
        run([tool("ctest") or "ctest", "--test-dir", directory, "--output-on-failure"],
            cwd=DESK)
    else:
        say("Desktop unit tests")
        run([tool("ctest") or "ctest", "--preset", build, "--output-on-failure"], cwd=DESK)

    failures = _protocol_checks(target)
    failures += _session_checks(target)

    say("\nTranscription reconnect (against the mock service)")
    failures += _reconnect_check(target)
    failures += _silent_stream_check(target)

    if sanitize:
        say("\nSanitizers")
        failures += _collect_sanitizer_reports(reports)

    if failures:
        fail(f"{failures} check(s) failed")
    return 0


def _binary(target, name):
    """Multi-config generators put executables in a per-config subdirectory of the
    build tree; single-config ones put them at its root. Both are checked rather than
    guessed at from the platform, because the generator is what decides."""
    build_type = target.get("DST_BUILD_TYPE", "Release")
    _, _, binary_dir = _presets(build_type)
    suffix = ".exe" if WINDOWS else ""

    # A sanitizer build lives in a tree of its own (see cmd_build). When one is asked
    # for, it is the *only* place looked in: falling back to bin/Release would run the
    # ordinary binaries and report a clean sanitizer run over a build that was never
    # instrumented, which is worse than not having the option at all.
    override = target.get("DST_BINARY_DIR")
    if override:
        roots = [Path(override)]
    else:
        roots = [Path(binary_dir)] if binary_dir else []
        roots += [DESK / "bin" / build_type, DESK / "bin"]

    # kobayashi is a bundle on macOS, so the executable is buried inside it rather than
    # sitting in the build directory. Listed alongside the plain names rather than
    # branching on the platform: kobayashi-sim is not a bundle, and a future target might not
    # be either, so what is looked for is a file that exists.
    names = [f"{name}{suffix}"]
    if MACOS:
        names.append(f"{name}.app/Contents/MacOS/{name}")

    for root in roots:
        for leaf in names:
            for candidate in (root / leaf, root / build_type / leaf):
                if candidate.exists():
                    return candidate
    return roots[0] / f"{name}{suffix}"


def cmd_run(args):
    target = load_target(args.target)
    require_host_target(target)
    binary = _binary(target, "kobayashi")
    if not binary.exists():
        fail(f"{binary} does not exist — run: python dst.py build")

    command = [binary, "--port", target.get("DST_WS_PORT", "8765")]
    if args.extra:
        command += args.extra

    # Windows resolves DLLs from the executable's directory and then PATH, with no
    # equivalent of the search path baked into ELF and Mach-O binaries, so a build-tree
    # binary cannot find Qt on its own. CMake recorded the directory beside it.
    environment = None
    marker = binary.parent / "qt-runtime-dir.txt"
    if WINDOWS:
        if marker.exists():
            environment = workspace_env(extra_path=marker.read_text().strip())
        else:
            say(f"  note  {marker.name} is missing, so Qt may not be found. It is"
                " written by the build; re-run: python dst.py build")

    say(f"Running {binary.name}")
    return run(command, check=False, env=environment).returncode


def cmd_package(args):
    target = load_target(args.target)
    require_host_target(target)
    build_type = target.get("DST_BUILD_TYPE", "Release")
    generator = target.get("DST_PACKAGE_GENERATOR", "TGZ")

    _, _, binary_dir = _presets(build_type)
    build_dir = Path(binary_dir) if binary_dir else (DESK / "bin" / build_type)

    if not (build_dir / "CMakeCache.txt").exists():
        fail(f"nothing configured in {build_dir} — run: python dst.py build")

    say(f"Packaging with CPack ({generator})")
    run([tool("cpack") or "cpack", "-G", generator, "-C", build_type], cwd=build_dir)
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



def _clean_targets(repo):
    """What a fresh clone of `repo` would not have.

    Asked of git rather than listed here, and `-X` rather than `-x`: only files the
    repository already declares ignored, never merely untracked ones. Two reasons that
    matters. A hand-written list rots the moment the build learns to emit something new —
    `bin/Sanitize` was exactly that case. And `dstORCH/src/generated/protocol.js` is
    committed on purpose (decision 21) so the extension loads from a bare clone; git knows
    it is tracked and will not offer it, where any rule about "generated files" would
    delete the one generated file that must survive.
    """
    listing = git(repo, "clean", "-Xdn", check=False).stdout
    prefix = "Would remove "
    return [line[len(prefix):].strip()
            for line in listing.splitlines() if line.startswith(prefix)]


def _is_recording(path):
    """Recordings are meeting audio, not build output. They are ignored by git and would
    not survive a clone, but deleting someone's recordings because they asked to clean a
    build tree is not a trade this should make silently."""
    return path == "out" or path.startswith("out/") or path.startswith("out\\")


def cmd_clean(args):
    """Returns the workspace to what a fresh clone would give, minus what it must ask
    about first.

    This exists to make the README's central claim testable. The brief requires a project
    that builds from a clean checkout with no missing steps, and there was no way to get
    back to a clean checkout without deleting three directories by hand and hoping that
    was all of them.
    """
    plans, kept = [], []

    for repo in REPOS:
        for path in _clean_targets(repo):
            note = None
            if _is_recording(path):
                note = "recordings"
                if not args.recordings:
                    kept.append((f"{repo}/{path}", "recordings — pass --recordings"))
                    continue
            plans.append((Path(repo) / path, f"{repo}/{path}", note))

    venv = ROOT / ".venv"
    if venv.exists():
        if args.toolchain:
            plans.append((venv, ".venv", "toolchain"))
        else:
            kept.append((".venv", "toolchain — pass --toolchain, then setup again"))

    root_out = ROOT / "out"
    if root_out.exists():
        if args.recordings:
            plans.append((root_out, "out", "recordings"))
        else:
            kept.append(("out", "recordings — pass --recordings"))

    if not plans and not kept:
        say("Already clean.")
        return 0

    say("Would remove:" if args.dry_run else "Removing:")
    for _, shown, note in plans:
        say(f"  {shown}{'' if note is None else f'   ({note})'}")
    if not plans:
        say("  (nothing)")

    for shown, why in kept:
        say(f"  keeping {shown}   ({why})")

    if args.dry_run:
        return 0

    for path, shown, _ in plans:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)

    # The one thing this cannot reach, and the one that decides whether a rebuild from
    # here resembles a reviewer's first run. Qt lives in Conan's cache, which is shared
    # across every project on the machine and is not the workspace's to delete — so a
    # build after this is a clean *workspace* build, not a clean *machine* build. Said
    # out loud because the difference is fifteen minutes and 1.6 GB.
    say("\nThe Conan cache is untouched: it is machine-wide, not part of this workspace,")
    say("so `setup` will find Qt already built and finish in about a second. To rehearse")
    say("what a reviewer actually sees, remove the qt-official package as well:")
    say("  conan remove 'qt-official/*' --confirm")

    say("\nFrom here: python dst.py setup && python dst.py build && python dst.py test")
    return 0


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
    setup_parser = sub.add_parser("setup", help="prepare the toolchain, once per machine")
    setup_parser.add_argument("--force", action="store_true",
                              help="redo work already done: re-detect the Conan profile "
                                   "and fetch Qt again")
    setup_parser.set_defaults(fn=cmd_setup)
    build_parser = sub.add_parser("build", help="build the desktop application")
    build_parser.add_argument("--sanitize", action="store_true",
                              help="also build an instrumented tree in bin/Sanitize "
                                   "(AddressSanitizer + UndefinedBehaviorSanitizer)")
    build_parser.add_argument("-v", "--verbose", action="store_true",
                              help="stream compiler output instead of a progress line")
    build_parser.set_defaults(fn=cmd_build)
    test_parser = sub.add_parser("test", help="run the tests")
    test_parser.add_argument("--sanitize", action="store_true",
                             help="run the whole suite against bin/Sanitize and fail on "
                                  "any sanitizer report")
    test_parser.set_defaults(fn=cmd_test)
    # No passthrough argument is declared: everything after `run` is collected in
    # order by parse_known_args below. Declaring a positional would split the tokens
    # between two lists and lose their order, so a flag could arrive without its value.
    run_parser = sub.add_parser("run", help="start the desktop application "
                                            "(unrecognised arguments are passed to it)")
    run_parser.set_defaults(fn=cmd_run)

    sub.add_parser("package", help="build a distributable archive").set_defaults(fn=cmd_package)

    clean_parser = sub.add_parser("clean", help="remove build trees and generated files")
    clean_parser.add_argument("--recordings", action="store_true",
                              help="also delete recorded audio under out/")
    clean_parser.add_argument("--toolchain", action="store_true",
                              help="also delete .venv, so setup must run again")
    clean_parser.add_argument("--dry-run", action="store_true",
                              help="list what would go, remove nothing")
    clean_parser.set_defaults(fn=cmd_clean)
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
