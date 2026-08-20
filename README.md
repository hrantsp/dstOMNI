# dstOMNI

Organizer for the dual-stream transcription pipeline: a Chrome extension that captures
microphone and meeting-tab audio **separately**, and a cross-platform C++/Qt desktop
application that transcribes both and shows them as one live conversation with the two
speakers kept apart.

This repository holds the build entry point, the target descriptors, and the design
record. The implementation lives in two sibling repositories.

| Repository | Application | Role |
|---|---|---|
| [`dstDESK`](https://github.com/hrantsp/dstDESK) | **Kobayashi** | Desktop application. Receives both streams, records and transcribes them, renders the conversation. Owns the wire protocol. |
| [`dstORCH`](https://github.com/hrantsp/dstORCH) | **Verbal** | Chrome MV3 extension. Captures microphone and tab audio and sends them onward. |
| `dstOMNI` | — | This repository. Builds, packages and versions the other two. |

Verbal does the talking; Kobayashi listens and writes it down. The repositories keep
their `dst*` names — they are where the work lives; the applications are what a user
meets.

The names come from *The Usual Suspects* (1995), where Verbal Kint does the talking and
Kobayashi is who it goes to.

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

Needs **Python 3.9+**, **Git**, and a C++20 compiler. CMake and Ninja are not in that
list: `setup` installs them into `.venv` along with Conan, so the versions used are
the same on every machine and one less thing has to be right before you start.

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
xcode-select --install     # Apple clang, the SDK, git and python3
```

That is all of it — no Homebrew needed. CMake, which macOS does not ship, is installed by
`setup` into `.venv` along with Conan and Ninja.

Then the same three commands as everywhere else. Qt is fetched as a universal binary, so
Apple silicon and Intel are both covered. OpenSSL is required here, unlike on Windows —
Qt offers only its OpenSSL backend on macOS — so expect `setup` to compile it from source
if ConanCenter has no binary for your Xcode version.

</details>

```bash
python3 dstOMNI/dst.py doctor    # says what is missing before anything is downloaded
python3 dstOMNI/dst.py setup     # creates .venv, installs Conan, fetches Qt
```

`setup` installs Conan, aqtinstall, CMake and Ninja into `.venv`, then downloads the
official Qt binaries into the local Conan cache. Only the modules
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

A window opens and waits for the extension. `build` prints one progress line rather than
the compiler's output, which is kept and shown if a step fails; `--verbose` streams it.

On Windows the build copies the Qt runtime beside `bin\Release\kobayashi.exe`, so it can
also be started by double-clicking it. Windows resolves DLLs from the executable's own
directory and then `PATH`, with no equivalent of the search path that ELF and Mach-O
binaries carry, so without that copy the binary reports `Qt6Widgets.dll was not found`.
`dst.py run` works either way — it puts Qt's directory on `PATH` itself.

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

## What has been verified, and where

Every platform below ran the whole pipeline: a Google Meet call, microphone and tab audio
captured separately by Verbal, both transcribed by Kobayashi, and the two transcripts
interleaved in order.

| | Linux | Windows 10 | macOS (Apple silicon) |
|---|---|---|---|
| `setup`, `build`, unit tests | yes | yes | yes |
| Whole suite under AddressSanitizer + UBSan | yes | — | — |
| `--selftest` | yes | yes | yes |
| Live call, both streams transcribed | yes | yes | yes |
| Microphone free of the meeting's audio, on speakers | yes | yes | yes |
| Application icon and window identity | yes | yes | yes |
| Starts by double-click, no console | n/a | yes | yes |
| TLS backend in use | OpenSSL | Secure Channel | OpenSSL |
| Packaged artifact runs with no toolchain | yes | — | — |
| Extension origin refused when it does not match | yes | yes | — |
| `--no-record`: nothing written, accounting kept | yes | — | — |
| A disk that stops accepting audio, reported not silent | yes | — | — |

Nothing above is inferred: each cell was run on that machine. The gaps are honest — the
packaged artifact has only been exercised on Linux, where it was unpacked into a clean
environment and started with `env -i`, and the last three rows were added after the fact
and have only been run there.

The last two are worth a word, because both are failure paths rather than features and
neither had ever been exercised. `--no-record` writes no files and still reports frames,
gaps and rejects, so the accounting that shows the pipeline working survives turning the
recording off. A disk that stops accepting audio is reached without root by capping the
file size and ignoring the signal that goes with it:

```bash
bash -c 'ulimit -f 100; trap "" XFSZ; exec kobayashi --headless --no-transcribe \
    --port 8899 --output /tmp/full'
```

The application says so once per stream rather than 31 times a second, keeps counting
frames, and flags the recording in the summary:

```
Recording stopped for Microphone — the disk would not accept it. Transcription is unaffected.
  mic  187 frames  5.98 s  0 gaps (0 padded samples)  0 rejected  WRITE FAILED — the recording is incomplete
```

---

## What it records, and what leaves the machine

Worth knowing before pointing it at a real meeting.

**Both streams are written to disk unencrypted**, as WAV files under the output
directory, and nothing removes them afterwards. `--no-record` transcribes without writing
any audio at all; the session summary still reports frames, gaps and rejects, so nothing
diagnostic is lost. Recording is not something the task asked for — decision 22 in
[`DESIGN.md`](DESIGN.md) says why it is here and why it defaults on. They are meeting audio, so treat them as
you would a recording made any other way.

**Audio is sent to Deepgram for transcription.** That is a third-party service over
`wss://`, and it only happens once a key is configured — without one the application
records and does not transcribe, which is the whole of the difference.

**Nothing is exposed off the machine.** The socket binds `127.0.0.1`, and the desktop
application accepts browser connections only from Verbal's own extension origin. That
check matters more than it sounds: a WebSocket to loopback needs no CORS preflight, so
without it any web page open in any browser could connect to a running Kobayashi while
you were in a meeting. `--origin` overrides the list; `--token` adds a shared secret on
top. The reasoning, and the gaps deliberately left, are in decision 19 of
[`DESIGN.md`](DESIGN.md).

---

## Known limits

Things this does not do, or does badly, that are worth knowing before you rely on it.
Each is a deliberate stopping point rather than an oversight, and each says what would
have to change.

| Limit | What happens | Why it stays |
|---|---|---|
| **A capture longer than ~74 hours** | `sampleIndex` is a `u32` at 16 kHz, so it wraps. Past the wrap every frame reads as going backwards, is discarded under `PROTOCOL.md` §5.3, and that stream stops recording and transcribing. The discarded count in the session summary is what shows it. | The field is part of the wire format, so widening it is a protocol version bump shipped on both sides. The clock belongs to one `AudioContext`, created when capture starts, so this is 74 hours of one unbroken capture — not an install lifetime. |
| **A recording longer than ~37 hours** | RIFF sizes are `u32`, so the WAV header wraps past 4 GB and the file's declared length stops matching its contents. Reached before the limit above. | A larger container — RF64 or WAV64 — for a duration no meeting reaches. |
| **A transcription connection that recovers after a long outage** | While a stream is stalled the other commits past it. If it then returns with text from before that point, the text is appended after lines it should have preceded. | The alternative is a transcript frozen for the rest of the call, which is worse and looks like the application being broken. Decision 13 has the reasoning. |
| **More than about thirty seconds of lost audio in one gap** | Padding stops and the stream re-bases, so its timeline has a step in it. Reported, not silent. | The bound exists because the gap length arrives from the client and is used as a write length. Without it one frame wrote 6.3 GB. |
| **Sustained double-talk on loud external speakers** | Echo cancellation is what keeps the remote participants out of the microphone transcript, and it is relaxed during double-talk so the near-end speaker is not clipped. Live calls on all three platforms show the separation holding on laptop speakers; harder conditions are untested. | Correcting it properly means an echo canceller in the desktop application, against a reference that has crossed a socket. Decision 23. |
| **Speaker labelling does nothing** | `--diarize`, and the matching checkbox, ask the engine to label the voices inside the meeting stream. The engine does it and returns a speaker index on every word; nothing reads it. The option changes nothing you can see. | Finishing it means carrying the index through to the view, which is a feature rather than a fix to the two-stream separation the task asks for. Stated in the interface, the help text and decision 14 rather than left to be discovered. |
| **`--stt-endpoint` ships in the released binary** | A flag exists that points transcription at any endpoint. | Without it the transcription and merge path can only be exercised against a paid third party, so it had no test at all. Decision 25 argues the trade. |

The security posture — unencrypted recordings, a plaintext API key, no token by default,
`ws://` on loopback — is a separate list, with its reasoning, in decision 19 of
[`DESIGN.md`](DESIGN.md).

---

## What is not here, and why

Everything below could be built, and several would take an afternoon. None of it is here,
and that is a decision rather than a backlog. Decision 26 in [`DESIGN.md`](DESIGN.md) has
the reasoning; the short version is that this project was written with heavy AI assistance,
so code is the cheap part. What is not cheap is the part that can be attributed to me —
the architecture, the trade-offs, and the evidence that each was tested rather than
assumed. More features would add more that I did not decide, and would make this longer to
review without making it better.

| # | Considered | Why it is not here |
|---|---|---|
| 1 | Transcript export — text, SRT, VTT, JSON | The transcript is shown and never written. Only the audio is saved. The most conspicuous absence here, and the first thing that would be built |
| 2 | Transcript persistence between sessions | Same: nothing survives the window closing |
| 3 | LLM summaries or action items | The task permits an LLM. A summary is a different product from a transcript that keeps two speakers apart |
| 4 | Renaming a stream — "Meeting" to "John" | Cosmetic over a model that is fixed at two streams |
| 5 | Naming individual remote participants | `--diarize` separates voices within the meeting stream; identifying them is a different problem |
| 6 | Dark mode | One light palette on all three platforms **for now** — a stopping point, not a position |
| 7 | Always-on-top window | Polish |
| 8 | Custom disappearing title bar | Polish |
| 9 | Background blur and opacity | Polish |
| 10 | Tray icon with actions | Polish |
| 11 | Global hotkeys | Polish |
| 12 | Recordings browser | Product surface, not an engineering question |
| 13 | Playing and re-transcribing an existing recording | Product surface |
| 14 | Manual reconnect button | The transcription link already reconnects on its own (decision 25). A button would offer to do what is happening anyway |
| 15 | Sharing a recording | Product |
| 16 | Cloud sync for recordings | Product, and a privacy question this deliberately does not open |
| 17 | Ring buffer keeping only the last N minutes | Product |
| 18 | An installer per platform — MSI or NSIS, a signed `.dmg`, an AppImage | CPack produces an archive per platform today. One installer for all three is impossible: Qt's deployment tools run only on their own operating system (decision 5), so this means one each, built on it |
| 19 | Portable build | Same packaging work as 18 |
| 20 | Syncing capture with the Google Meet microphone button | **Built once and removed.** It depended on the structure of Meet's page and broke when that changed. A feature that silently stops working is worse than one that is absent — but this belongs in the product, and is second in the order below |
| 21 | Meeting metadata — title, participants, start time — from the tab | Unbuilt |
| 22 | Plugins for Zoom, Teams and other meeting platforms | Unbuilt. The capture side is written against Chrome's tab capture, so another platform means another extension or a native equivalent, not a setting |
| 23 | Transcription inside the extension, with no desktop application | Contradicts the task, which asks for a C++ desktop application |
| 24 | Accounts and subscriptions | Product, not engineering |
| 25 | Continuous integration | All three platforms were verified by hand; the table above says which checks ran where |
| 25a | Automated tests for the extension | `wire.js` is covered by `tst/wire-check.mjs`, and it is the one file in the extension that decides nothing. The parts that actually break — MV3 service-worker termination, offscreen document lifetime, a real `getUserMedia` — are the parts a `chrome.*` mock does not reach, so a mock suite would test the wrong half convincingly. Doing it properly means a headless-Chrome harness, which is a project rather than an afternoon |
| 25b | A byte-level check that the recording matches what was sent | `kobayashi-sim` sends one tone per stream so the result can be checked **by ear**, which is a human step this README asks a reviewer to perform. `tst/sessions.mjs` reads sample values back out of the WAV for the two cases it covers; generalising that to "send a known pattern, assert the file contains it" is small and is the first thing to add here |
| 25c | A test for the 74-hour `sampleIndex` wrap | The behaviour past it is stated in the limits above and reached by no meeting, so it is a prediction rather than an observation. It is testable without a socket or a clock — `StreamRecorder` alone — and that is exactly why leaving it untested is a choice rather than a constraint |
| 26 | Code signing and notarization | Out of scope, and noted where it bites: an unsigned bundle arriving by download is quarantined (`targets/macos.cfg`) |
| 27 | Translations | `tr()` is used throughout, so the strings are ready; no catalogues exist |
| 28 | Microphone-only transcription — one input, no meeting, no extension | The desktop application has no audio capture of its own. Every stream reaches it from the browser, which is what keeps it clear of WASAPI, CoreAudio and ALSA and made all three platforms nearly free (decision 4). A standalone dictation mode means adding exactly the platform audio code that decision exists to avoid. The microphone stream is already transcribed by itself if capture is started on any tab; what is missing is doing it without a browser at all |
| 29 | Microphone selection | The system default device is used |
| 30 | More than one session at a time | A second connection replaces the first, deliberately (decision 19) |
| 31 | More than two streams | The protocol's `stream` field is a `u8`, so 256 are addressable; two are defined |

### What would come first

Not a wish list; an order.

1. **Persisting and exporting the transcript.** The most conspicuous absence, the smallest
   change, and the one that makes the recordings useful to anyone who was not in the room.
2. **Syncing with the Meet microphone button.** Not polish — a muted microphone that is
   still transcribed puts words on the wrong side of a conversation, which is the one
   thing this application exists to get right. It needs an approach that does not depend
   on someone else's DOM.
3. **Continuous integration.** Three platforms are verified by hand today, and the table
   above is only true on the day it was written.

---

## How this was built

The task permits AI assistance provided the candidate orchestrates it. This was written
that way, and it is worth being exact about what that means rather than leaving it to be
inferred.

Most of the code here was written by an AI agent. The architecture, the scope, and every
trade-off in [`DESIGN.md`](DESIGN.md) were decided by me, and the parts of this repository
that took the longest are the parts that are not code: deciding that the protocol should be
owned by one side and generated into the other, that ordering should rest on a
finalisation watermark rather than a fixed delay, that the origin check is the security
boundary, and that this list of features should stay unbuilt.

The evidence for that is deliberately in the repositories rather than in this paragraph:

- **[`DESIGN.md`](DESIGN.md)** records thirty decisions with the alternatives rejected
  and what each costs. Two of them supersede earlier ones, and say so; three correct claims
  that turned out to be false, and say which sentence was wrong and what replaced it.
  Decision 28 is a decision *about* that: three separate fixes here turned out to have
  stopped one level short of the fault they were fixing, and finding the shape they shared
  was worth more than any of the three individually.
- **The verification table** above states which platform ran which check, including the
  cells that were never run.
- **The known limits** table states where this breaks and why the break is tolerated.

Several of the bugs found late appeared only by running the thing, in states nobody had
thought to try. A framework Apple deleted. A working directory Finder never sets. A first
capture lost to a module that had not finished loading. A use-after-free that crashed the
application on teardown whenever the transcription socket had not finished connecting —
reached by a wrong API key, no network, or simply stopping early. Saving the settings
dialog, which rebuilt the server's configuration from defaults and so emptied the origin
allowlist, locking the extension out for the rest of the run — on the exact path this
README tells a reviewer to take. One frame with an implausible position, which made the
recorder write six gigabytes of silence to disk before anyone could stop it.

None of those is visible in the code. Each was found by putting the application in a state
it had not been in before and watching what it did. That is the part no amount of prompting
produces, and it is most of what took the time.

---

## Commands

```
python3 dstOMNI/dst.py <command> [--target NAME]
```

| Command | Does |
|---|---|
| `doctor` | Reports what this machine is missing, and stops there |
| `setup` | Creates the virtual environment, installs Conan and aqtinstall, fetches Qt |
| `build` | Builds the desktop application. `--sanitize` also produces an instrumented tree in `bin/Sanitize` |
| `test` | Runs the unit tests, then the protocol, wire, session and reconnect checks against servers it starts itself. `--sanitize` runs all of it against `bin/Sanitize` and fails on any sanitizer report |
| `run` | Starts the desktop application |
| `package` | Produces a distributable archive with CPack |
| `clean` | Removes build trees and generated files, returning the workspace to what a fresh clone gives. `--recordings` and `--toolchain` extend it; `--dry-run` only lists |
| `status` | Commit, tag, unpushed and uncommitted counts for all three repositories |
| `version` | Tags every repository with one version |

`--target` selects a descriptor from `targets/`, defaulting to this machine. A target
declares the platform it builds on, and `build`, `run` and `package` refuse a target
that is not this machine — Qt's deployment tools only run on their own operating
system, so there is nothing to gain from failing halfway instead.

`clean` exists so the claim this README makes can be tested rather than assumed. The brief
asks for a project that builds from a clean checkout with no missing steps, and there was
no way back to a clean checkout without deleting directories by hand and hoping that was
all of them. It asks git what a fresh clone would lack rather than carrying a list, so it
cannot fall behind a build that learns to emit something new — and it will not touch
`dstORCH/src/generated/protocol.js`, which is generated but committed on purpose so the
extension loads from a bare clone.

Recordings and the toolchain are kept unless asked for: one is meeting audio, the other is
a 1.6 GB download. The Conan cache is machine-wide and is never touched, so a build after
`clean` is a clean *workspace* build rather than a clean *machine* build — the command says
so, and says how to close the gap.

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

## Where the time goes

A frame is 512 samples, so one arrives every **32 ms** per stream — 62.5 a second across
both. That is a generous budget, and it is the trap: anything the receive path does in
microseconds is invisible, so per-frame cost is the wrong thing to watch.

The two costs that actually mattered here were not per-frame costs. One grew with the
number of lines already on screen; the other grew with an integer the client supplied.
Neither shows up in a profile of a short session, which is why both survived until
something was run long enough or fed something odd enough to expose them.

| Measured | Before | After |
|---|---|---|
| Appending a transcript line, at 1200 lines | 21.6 ms, and rising with every line | **0.21 ms, flat** |
| Laying out an hour of meeting | 14,310 ms | **247 ms** |
| Clearing a search over 1200 lines | 1,325 ms | **2 ms** |
| Writing an hour of audio to disk | 614 ms | **30 ms** |
| One frame with an implausible position | 6.3 GB written, event loop blocked | **2 kB, and reported** |
| A transcription link dropping once a second | 34 connections in 28 s | **12, then it stops** |

The first three are one change: the transcript stopped being a widget per utterance and
became a model, a filter and a delegate, so the view renders the rows on screen and
nothing else. `kobayashi-bench` guards it — as a **shape**, not a millisecond threshold:
it fails if appending gets slower as the transcript grows, and it was confirmed against
the implementation it replaced, which fails it by 7×.

Nothing else on the audio path was tuned, because nothing else needed it. The frame that
leaves the browser is the frame the transcription engine receives — 16 kHz mono PCM16, no
resampling, no channel mixing, no codec anywhere in between — so the desktop application
routes bytes and draws a UI rather than processing audio. Decisions 8, 24 and 27 have the
reasoning and the numbers, including the two allocations still on the path and why removing
them would be the same mistake as profiling a two-minute session.

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
dstDESK/bin/Release/kobayashi-sim --mic a.wav --meeting b.wav --offset 4   # terminal 2
```

`kobayashi-sim` speaks the real protocol over a real socket. Without files it sends 440 Hz on
the microphone stream and 660 Hz on the meeting stream, so the recordings are checkable
by ear: one clean tone per file means capture and routing are correct. `--gap` drops a
run of frames so gap padding can be observed rather than assumed.

`dstORCH/tst/wire-check.mjs` runs the extension's own encoder under Node against a
running desktop app, which is what proves the JavaScript and C++ agree byte for byte
without involving a browser. `dstDESK/tst/abuse.mjs` is its counterpart for the server:
it sends the malformed, misplaced and out-of-order frames a real client eventually will,
and checks each is answered the way `PROTOCOL.md` §5.3 says. Both are run by
`dst.py test`, which starts a server for them — needing Node, and skipped without it.

---

## Troubleshooting

**The window says "Recording only — no API key".** A launch outside a terminal inherits
no environment. Use **Settings…** in the window, or export `DEEPGRAM_API_KEY` and start
it from a shell.

**Windows: "cmake: command not found" or MSVC not detected.** Use the *x64 Native Tools
Command Prompt for VS 2022* rather than a plain terminal; a normal prompt has neither
the compiler nor Visual Studio's CMake on PATH.

**"cannot bind 127.0.0.1:8765".** Another copy is already running, or something else
holds the port. `kobayashi --selftest` reports it; change the port in Settings or with
`--port`.

**The extension badge shows `!`.** Hover it for the reason. A red `!` after the desktop
app closes is expected; clicking the button stops capture cleanly.

**No transcript, but the recordings are fine.** Check `out/<session>/` — if the WAVs
contain audio, capture and transport are working and the problem is the key or the
network, not the pipeline.
