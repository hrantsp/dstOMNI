# dstOMNI

Organizer for the dual-stream transcription pipeline: a Chrome extension that captures
microphone and meeting-tab audio **separately**, and a cross-platform C++/Qt desktop
application that transcribes both and shows them as one live conversation with the two
speakers kept apart.

This repository holds the build entry point, the target descriptors, and the design
record. The implementation lives in two sibling repositories.

| Repo | Role |
|---|---|
| [`dstDESK`](https://github.com/hrantsp/dstDESK) | Desktop application. Receives both streams, records and transcribes them, renders the conversation. Owns the wire protocol. |
| [`dstORCH`](https://github.com/hrantsp/dstORCH) | Chrome MV3 extension. Captures microphone and tab audio and sends them onward. |
| `dstOMNI` | This repository. Builds, packages and versions the other two. |

`dstORCH` is the *orchestra* — it plays, and `dstDESK` listens.

---

## Getting it running

### 1. Clone all three, side by side

The directory names matter: `dstOMNI` locates the others relative to its own parent.

```bash
mkdir dst && cd dst
git clone https://github.com/hrantsp/dstDESK.git
git clone https://github.com/hrantsp/dstORCH.git
git clone https://github.com/hrantsp/dstOMNI.git
```

### 2. Prepare the toolchain — once per machine

Needs **Python 3.9+**, **CMake 3.24+**, **Git**, and a C++20 compiler.

<details>
<summary><strong>Windows</strong> — exactly what to install</summary>

Build natively, not under WSL: Chrome runs on Windows and talks to the desktop app over
loopback, and WSL2 puts the app in a different network namespace. The Qt this project
uses is `win64_msvc2022_64`, which needs MSVC in any case.

| Install | Why |
|---|---|
| [Build Tools for Visual Studio 2022](https://visualstudio.microsoft.com/downloads/), workload **"Desktop development with C++"** | MSVC v143 and the Windows SDK. The full IDE works too but is not needed. |
| [Python 3.9+](https://www.python.org/downloads/windows/) — tick **"Add python.exe to PATH"** | Runs `dst.py`, Conan, aqtinstall and the protocol generator |
| [Git for Windows](https://git-scm.com/download/win) | Cloning |
| [CMake 3.24+](https://cmake.org/download/) | Visual Studio bundles one, but not on PATH outside its own prompt |
| Google Chrome | Loading the extension |

Nothing else is needed — in particular, Windows long paths do **not** have to be
enabled. Only the Qt modules this application links are installed, which keeps the
tree well inside the 260-character `MAX_PATH` limit.

Then, from an **"x64 Native Tools Command Prompt for VS 2022"** (Start menu — this is
what puts MSVC on PATH):

```
python dstOMNI\dst.py doctor
python dstOMNI\dst.py setup
python dstOMNI\dst.py build
python dstOMNI\dst.py run
```

Node.js is optional, needed only for `dstORCH\tst\wire-check.mjs`.

</details>

<details>
<summary><strong>macOS</strong> — exactly what to install</summary>

```bash
xcode-select --install     # Apple clang and the SDK
brew install cmake python git
```

Then the same three commands as everywhere else. Qt is fetched as a universal binary,
so Apple silicon and Intel are both covered.

</details>

```bash
python3 dstOMNI/dst.py doctor    # says what is missing before anything is downloaded
python3 dstOMNI/dst.py setup     # creates .venv, installs Conan, fetches Qt
```

`setup` downloads the official Qt binaries into the local Conan cache. Only the modules
this application links are fetched — about 200 MB rather than the 1.6 GB of a full
install, since Quick and QML account for most of Qt's size and this is a Widgets
application.

**Expect it to look idle, twice.** `aqtinstall` reports an archive only once it has
finished, and `qtbase` is far the largest, so several minutes pass with nothing but the
clock moving; Conan then copies the tree into its package folder, which on Windows means
tens of thousands of small file creations with a virus scanner watching. Ten to fifteen
minutes there is normal, a couple of minutes on Linux and macOS. Neither quiet stretch
is a hang.

It is slow once. Afterwards `setup` takes about a second: it asks Conan whether this
recipe's exact package is already built rather than whether some Qt is lying around, so
it re-fetches when the recipe changes and does nothing when it has not. An existing
Conan profile is left alone. `--force` rebuilds the Qt package and re-detects the
profile. Everything it installs lives in `.venv/` at the workspace root; nothing is
installed system-wide.

### 3. Build and run

```bash
python3 dstOMNI/dst.py build
python3 dstOMNI/dst.py run
```

A window opens and waits for the extension.

### 4. Load the extension

`chrome://extensions` → enable **Developer mode** → **Load unpacked** → select
`dstORCH/`.

Then open a meeting tab and click the extension's toolbar button. The badge turns red
while capturing; click again to stop. The first capture opens a page asking for
microphone access — Chrome will not let an extension record until that has been
granted once, and the prompt cannot be raised from the background context where
capture actually runs.

### 5. Transcription

Recording works with no account. For transcripts, provide a
[Deepgram](https://deepgram.com) key either way:

```bash
export DEEPGRAM_API_KEY=...          # a terminal launch
```

or, for a double-clicked launch, use **Settings…** in the window, which writes to a
config file. A launch from a file manager inherits no shell environment, so the
variable that works in a terminal is simply absent there — the window says so plainly
rather than recording in silence.

---

## Commands

```
python3 dstOMNI/dst.py <command> [--target NAME]
```

| Command | Does |
|---|---|
| `doctor` | Reports what this machine is missing, and stops there |
| `setup` | Creates the virtual environment, installs Conan and aqtinstall, fetches Qt |
| `build` | Builds the desktop application |
| `test` | Runs the unit tests, and explains the browser wire check |
| `run` | Starts the desktop application |
| `package` | Produces a distributable archive with CPack |
| `status` | Commit, tag, unpushed and uncommitted counts for all three repositories |
| `version` | Tags every repository with one version |

`--target` selects a descriptor from `targets/`, defaulting to this machine. A target
declares the platform it builds on, and `build`, `run` and `package` refuse a target
that is not this machine — Qt's deployment tools only run on their own operating
system, so there is nothing to gain from failing halfway instead.

### Shell completion

The tool generates its own, from the same parser that defines the commands, so it
cannot describe a flag the tool does not have:

```bash
eval "$(./dst.py completion bash)"          # bash
./dst.py completion zsh  > ~/.zsh/_dst.py   # zsh, on your fpath
./dst.py completion powershell >> $PROFILE  # PowerShell
```

Completion attaches to a command name, so invoke the tool as `./dst.py` rather than
`python3 dst.py` for it to apply.

---

## How it works

```
   Chrome                              dstDESK
┌───────────────┐                  ┌──────────────────────────────┐
│ offscreen doc │                  │  Core   frames, WAV, ordering│  no Qt
│  ├ microphone │ ─┐               │  IO     server, transcription│
│  └ meeting tab│  │ 16 kHz PCM16  │  Sim    synthetic client     │
│               │  ├──────────────►│  App    window, self-test    │
│  one          │  │  ws://127.0.0.1
│  AudioContext │ ─┘               └──────────────────────────────┘
└───────────────┘
```

Four decisions carry most of the design. All of them, with the alternatives rejected
and what each costs, are in [DESIGN.md](DESIGN.md).

**Both captures share one `AudioContext`** (decision 9). They then share one clock, so
ordering the two transcripts is an integer comparison rather than a drift estimate.
This is the choice everything downstream rests on.

**Nothing transcodes** (decision 8). 16 kHz mono `linear16` is what the transcription
engine wants, so bytes leave the browser's audio worklet and reach the engine untouched.
The desktop application routes audio and draws a UI; it never processes it.

**Ordering uses a finalisation watermark, not a fixed delay** (decision 13). Both
streams report how far they have been finalised — silence included — and everything
starting below the minimum can be committed in order, because nothing earlier can still
arrive. Latency is therefore whatever the engine actually costs, measured at about
1.4 s, rather than a guessed constant.

**Qt comes from a local Conan recipe** (decision 11). ConanCenter has no usable
prebuilt Qt for this project: every published binary is built without the WebSockets
module, and even matching settings exactly they are unreachable because they were built
against older transitive dependencies. `dstDESK/rec/qt-official` fetches the official
binaries instead, so `conan install` remains the single entry point and Qt is never
compiled from source.

---

## Targets and packaging

A target descriptor holds only what genuinely differs between platforms — build type,
CPack generator, and the loopback port. The build commands are identical everywhere,
which is what CMake and Conan are for.

```bash
python3 dstOMNI/dst.py package    # this machine, target detected
```

Each platform packages itself. Asking for another one is refused rather than attempted:

```
$ python3 dstOMNI/dst.py --target macos package
error: target 'macos' builds on Darwin, this machine is Linux. There is no
cross-compilation — build it there and let the package travel.
```

| Target | Package |
|---|---|
| `linux` | `.tar.gz` |
| `windows` | `.zip` |
| `macos` | `.dmg` |

**There is no cross-compilation.** Qt's deployment tools only run on their own
operating system, so each target is built on the machine it targets and the package
travels. An unsigned macOS bundle arriving by download is quarantined by Gatekeeper and
needs `xattr -dr com.apple.quarantine`; code signing is out of scope.

---

## Versioning

All three repositories carry the same tag. A given `vX.Y.Z` of `dstORCH` was built and
verified against exactly that `vX.Y.Z` of `dstDESK`, so checking out matching tags
across the workspace yields a combination known to work — which is the reason this is
three repositories rather than one.

```bash
python3 dstOMNI/dst.py version           # next patch, from the highest tag anywhere
python3 dstOMNI/dst.py version v0.2.0 --message "..." --push
```

It refuses to tag while any repository has uncommitted changes or no origin remote,
because a tag that does not describe a pushed, reproducible state is worse than none.

---

## Checking it without a browser

The desktop half can be exercised end to end with no extension and no call.

```bash
python3 dstOMNI/dst.py run                              # terminal 1
dstDESK/bin/Release/dstsim --mic a.wav --meeting b.wav --offset 4   # terminal 2
```

`dstsim` speaks the real protocol over a real socket. Without files it sends 440 Hz on
the microphone stream and 660 Hz on the meeting stream, so the recordings are checkable
by ear: one clean tone per file means capture and routing are correct. `--gap` drops a
run of frames so gap padding can be observed rather than assumed.

`dstORCH/tst/wire-check.mjs` runs the extension's own encoder under Node against a
running desktop app, which is what proves the JavaScript and C++ agree byte for byte
without involving a browser.

---

## Status

Built and verified on **Linux**. The code is portable by construction — all audio
capture happens in the browser, so the desktop application contains no
platform-specific audio code at all — but it has not yet been compiled on Windows or
macOS. `dstdesk --selftest` answers the open question there in one run per machine: Qt
loads its TLS backend by name at runtime, so a machine without a usable OpenSSL builds
and starts cleanly and then fails on the first connection to the transcription service.

---

## Troubleshooting

**The window says "Recording only — no API key".** A launch outside a terminal inherits
no environment. Use **Settings…** in the window, or export `DEEPGRAM_API_KEY` and start
it from a shell.

**Windows: "cmake: command not found" or MSVC not detected.** Use the *x64 Native Tools
Command Prompt for VS 2022* rather than a plain terminal; a normal prompt has neither
the compiler nor Visual Studio's CMake on PATH.

**"cannot bind 127.0.0.1:8765".** Another copy is already running, or something else
holds the port. `dstdesk --selftest` reports it; change the port in Settings or with
`--port`.

**The extension badge shows `!`.** Hover it for the reason. A red `!` after the desktop
app closes is expected; clicking the button stops capture cleanly.

**No transcript, but the recordings are fine.** Check `out/<session>/` — if the WAVs
contain audio, capture and transport are working and the problem is the key or the
network, not the pipeline.
