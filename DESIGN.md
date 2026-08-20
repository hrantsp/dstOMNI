# Design decisions

A running log of the architectural decisions behind the dual-stream transcription
pipeline, recorded as they are made. Each entry states the decision, the context
that forced it, the alternatives that were rejected, and what the decision costs.

Entries are append-only. When a decision is reversed, the original stays and a new
entry supersedes it.

---

## 1. Three repositories with a version-locked orchestrator

**Decision.** The system ships as three repositories cloned side by side into one
workspace:

| Repo | Role |
|---|---|
| `dstDESK` | C++/Qt desktop application. Owns the wire protocol — it is the server. |
| `dstORCH` | Chrome MV3 extension. The *orchestra*: it plays, `dstDESK` listens. |
| `dstOMNI` | Organizer. Build entry point, target descriptors, documentation. |

Cross-repo compatibility is enforced by tagging all three with the same `vX.Y.Z` in
lockstep. "Which extension works with which desktop build" is answered by "the ones
sharing a tag."

**Context.** The two halves are genuinely separate artifacts with separate
toolchains — one is loaded unpacked into a browser, the other is a compiled native
binary. They are developed, versioned, and delivered differently.

**Rejected — single repository.** Simpler to clone and marginally easier to keep the
protocol in sync. It hides the release-engineering problem rather than solving it,
and the two build systems have nothing to share.

**Rejected — two repositories, no orchestrator.** Lightest option, but there is then
no single entry point, and the reviewer must reconcile two READMEs.

**Cost.** The reviewer clones three repositories instead of one, and lockstep tagging
must actually be performed rather than assumed.

**Reaffirmed, knowing the cost.** The brief says "ship a GitHub repo link", singular, and
the honest reading is that one repository would have satisfied it with less friction for
whoever reviews this. The layout stays anyway, and the argument is not that it is free:

- The two halves are delivered by different mechanisms — one is loaded unpacked into a
  browser, the other is compiled, packaged and signed per platform. They have no build
  system, no toolchain and no release cadence in common.
- "Which extension works with which desktop build" is a real question the moment there is
  more than one of either, and lockstep tags answer it. Every tag in this workspace was
  actually cut and pushed across all three, so it is a practice rather than an intention.
- A monorepo answers the version question for free and hides the release-engineering one.
  Both are defensible; this one is chosen deliberately, and `dstOMNI` exists so that the
  cost lands on three `git clone` lines rather than on the reviewer reconciling two
  READMEs and guessing at the build order.

---

## 2. `dstDESK` owns the wire protocol

**Decision.** The normative protocol specification lives in `dstDESK/rec/`.

**Context.** The desktop application is the server: it opens the socket, accepts
connections, and defines the frame format it will accept. Ownership follows the
service boundary.

**Rejected — a fourth `dstPROTO` repository.** Gives a cleaner dependency direction
(neither component depending on the other), and is what scale would eventually
demand. A fourth repository and a fourth tag to keep in lockstep is not worth that
purity here.

**Rejected — protocol in `dstOMNI`.** The orchestrator pins versions, but it is not
party to the conversation. A contract belongs with the side that enforces it.

---

## 3. The extension receives the protocol by generation, not by symlink

**Decision.** `dstDESK/rec/` holds the specification and a generator. The build
step emits `protocol.js` into `dstORCH` as a generated, gitignored artifact.

**Context.** Both sides need the same constants — frame layout, sample rate, channel
identity — and two hand-maintained copies drift.

**Rejected — a git symlink from `dstORCH` into `dstDESK`.** Requires
`core.symlinks=true` and Developer Mode on Windows, or the file checks out as text
containing a path. Chrome loading an unpacked extension through a symlink that
escapes the extension root is additionally fragile.

**Cost.** The extension cannot be loaded straight from a fresh clone; the generator
must run first. This is a documented step in the build path.

**Revised by decision 21** for `protocol.js` only, and for one reason: that cost lands on
exactly the action the task asks a reviewer to take.

---

## 4. Windows, macOS, and Linux — not the one platform required

**Decision.** All three desktop platforms are supported and verified.

**Context.** The brief requires Windows *or* macOS. Supporting all three is close to
free here: **all audio capture happens in the browser**, so the C++ application never
touches WASAPI, CoreAudio, or ALSA. It is a WebSocket server, an STT client, and a Qt
window — three portable dependencies with no platform-conditional audio code.

**Cost.** Verification time on three platforms rather than one — and it was not free
after all. Every platform found faults the others could not: Windows needed a windowed
subsystem and an RC language nobody had enabled, macOS needed the AGL framework prised out
of Qt's link interface and a recordings directory that does not depend on a working
directory Finder never sets, and both found gaps in a README that Linux had no way to
disprove. The engineering was portable; the assumptions were not.

**Verified.** All three run the full pipeline — two live streams from a Google Meet call,
transcribed separately and interleaved in order. The per-platform record is in
[`README.md`](README.md).

---

## 5. Packaging via CPack, built natively per target

**Decision.** `targets/*.cfg` describes a build target; packaging uses CPack
generators — `DragNDrop` on macOS, `ZIP`/`NSIS` on Windows, `TGZ` on Linux.

**Context.** A target build should produce one artifact that can be handed to someone
without a toolchain.

**Rejected — bespoke packaging scripts per platform.** CPack already ships with
CMake, which is a required part of the stack.

**Constraint, not a decision.** There is no cross-compilation: `macdeployqt` and
`windeployqt` only run on their own operating system. A target is built on the machine
it targets, and the package travels.

**Known caveat.** An unsigned `.app` delivered by zip trips Gatekeeper and requires
`xattr -dr com.apple.quarantine` on the receiving machine. Code signing is out of
scope.

---

## 6. Source builds are the delivery path

**Decision.** The repositories build from source on all three platforms. Prebuilt
binaries are not attached to tags.

**Context.** The brief requires the project to be fully buildable and runnable from
the README with no missing steps. That path must be flawless regardless, so effort
goes there rather than into release plumbing.

---

## 7. Transport is a localhost WebSocket, with `dstDESK` as the server

**Decision.** `dstDESK` binds a WebSocket server on `127.0.0.1`. `dstORCH` connects to
it as a client from its offscreen document. Audio travels as binary frames; control
messages travel as text frames on the same socket.

**Context.** A Chrome extension can only reach the outside world through HTTP(S),
WebSocket, WebRTC, or Chrome Native Messaging. There is no filesystem access, no Unix
socket, no named pipe. The candidate set is therefore small and can be reasoned about
exhaustively.

**Rejected — Chrome Native Messaging.** The mechanism Chrome provides for exactly this
purpose, and still the wrong choice here:

- *Installation friction.* A native messaging host needs a manifest installed at an
  OS-specific location — a registry key under
  `HKCU\Software\Google\Chrome\NativeMessagingHosts\` on Windows, a JSON file under
  `~/Library/Application Support/Google/Chrome/` on macOS, `~/.config/google-chrome/`
  on Linux — and that manifest must name the extension ID in `allowed_origins`. An
  unpacked extension's ID changes on every reload unless a `key` is pinned in the
  manifest. That is a per-platform install step and an ID-pinning step, in a project
  graded on having no missing setup steps. It is the most likely thing to fail on a
  reviewer's machine.
- *Lifecycle inversion.* Chrome spawns the host and terminates it when the port
  closes. The desired product is a desktop application the user launches and watches,
  which outlives any particular tab.
- *JSON-only payloads.* Audio would have to be base64-encoded — roughly 33% inflation
  plus continuous encode/decode on both sides.
- *stdout is the transport.* Any stray write to stdout from C++ corrupts the stream.

Message size is *not* among the objections: the extension-to-host direction permits up
to 4 GB per message. Only the host-to-Chrome direction is capped at 1 MB.

**Rejected — WebRTC.** Purpose-built for real-time audio, but it requires a full
WebRTC stack in the C++ application plus a signalling path, which is a large cost for
a hop that never leaves the machine.

**Rejected — chunked HTTP POST.** No persistent connection; per-request overhead on
every audio frame.

**Consequences.**

- The WebSocket client lives in the **offscreen document**, not the service worker.
  An offscreen document is required for audio capture regardless, MV3 service workers
  are terminated when idle, and co-locating capture with transmission avoids
  `postMessage`-ing audio buffers between contexts on every frame.
- The server is `QWebSocketServer` from Qt's WebSockets module, so it shares the Qt
  event loop rather than gluing a foreign one to it, and adds no dependency beyond Qt.
- Qt must be built with a working TLS backend, since the STT client needs `wss://`.
  This constrains the pending Qt acquisition decision and is verified on day one.
- Any local process can reach a localhost port, so the server checks the
  `Origin: chrome-extension://<id>` header Chrome sends. Decision 19 describes what that
  check does and does not cover. An earlier version of this entry also claimed a shared
  token was required, which was never true of the defaults.
- The port is defined in `targets/*.cfg`; both halves read it from there.

---

## 8. Wire format: 16 kHz mono PCM16, chosen so nothing transcodes

**Decision.** Audio crosses the socket as 16 kHz, mono, signed 16-bit little-endian
PCM, in frames of 512 samples behind a 12-byte header. Specified in
`dstDESK/rec/PROTOCOL.md`; the reasoning is expanded in
`dstDESK/rec/AUDIO-PRIMER.md`.

**Context.** This is exactly Deepgram's `linear16` encoding. Bytes produced by the
browser's AudioWorklet cross the socket and enter the transcription connection
untouched — no resampling, no channel mixing, no format conversion anywhere in the C++
application. The desktop side routes bytes and draws a UI rather than processing audio,
which removes an entire class of bug where audio is subtly corrupted and only surfaces
as poor accuracy.

16 kHz is the rate speech models are trained at; its 8 kHz Nyquist ceiling covers
speech intelligibility. Sending the browser's native 48 kHz would triple bandwidth and
then be resampled away at the far end.

**Rejected — Opus.** Roughly 10× smaller on the wire. Bandwidth is 32 kB/s per stream
across a loopback interface that moves gigabytes per second, so the saving buys
nothing, while a codec dependency on both sides destroys the pass-through property
above.

**Frame size — 512 samples (32 ms).** An AudioWorklet processes in 128-sample quanta,
so 512 is exactly four with no partial-block bookkeeping. It contributes under 10% of
end-to-end latency, which is dominated by the few hundred milliseconds streaming
transcription takes.

**Consequence.** Raw audio is never accumulated. A one-hour session is 230 MB across
both streams; frames are forwarded and released, and only transcript text is retained.

---

## 9. Both streams are captured through one `AudioContext`

**Decision.** The microphone and tab `MediaStream`s are fed into a single
`AudioContext` in the offscreen document. Frame position is carried as `sampleIndex`,
taken from `currentFrame` — a counter global to the `AudioContext` and therefore shared
by both capture taps. A single UTC epoch for that clock is sent once in `hello`.

**Context.** "Kept separate so the conversation can be followed in a natural order" is
an explicit requirement. Ordering two independently transcribed streams requires
knowing when each utterance occurred *relative to the other*.

**Rejected — two independent capture paths with wall-clock timestamps.** Two clocks
drift relative to each other, so every cross-stream comparison needs drift estimation,
and ordering becomes an approximation.

With one clock, two frames carrying the same `sampleIndex` were captured in the same
render quantum. Cross-stream ordering reduces to an integer comparison with no
correction term.

**Limit, stated honestly.** A shared clock removes clock *drift*. It does not remove
differences in capture *path latency* — microphone input latency and the tab audio path
may differ by tens of milliseconds. No correction is attempted, because conversation
ordering operates on utterances seconds apart.

**Implementation consequence.** A Web Audio node is only processed when it is connected
toward the context destination; a capture tap wired to nothing may never have
`process()` called at all. The taps are therefore routed to `destination` through a
zero-gain node, which keeps them running without producing sound.

**Cost.** This is the one decision here that is expensive to reverse, because the
transcript merge logic is built on top of it. Recorded explicitly for that reason.

---

## 10. Tab audio stays audible, on a playback path separate from capture

**Decision.** The captured tab `MediaStream` is played back at its native rate through
a path outside the capture graph — an `<audio>` element with `srcObject` set to the tab
stream, in the offscreen document. The offscreen document declares both `USER_MEDIA`
and `AUDIO_PLAYBACK` reasons.

**Context.** `chrome.tabCapture` silences the tab it captures. Left uncorrected, the
user stops hearing the meeting the moment transcription starts, which is unusable: the
product must be invisible to the call it observes.

**Rejected — routing playback through the capture `AudioContext`.** The obvious fix,
and wrong. That context runs at 16 kHz because that is what the transcription engine
wants. Sending playback through it would band-limit the meeting to 8 kHz for the entire
call — telephone quality — degrading the user's actual conversation in order to serve
the transcript. Capture rate and playback rate are separate concerns and are kept
separate.

**Consequences.**

- Exactly one audible copy reaches the user: the original is muted by capture, the
  replay restores it. No echo.
- Playback quality is unaffected by any future change to the capture sample rate.
- Verifying that a captured call still sounds normal is an explicit test step, not an
  assumption.

---

## 11. Qt arrives through a local Conan recipe wrapping the official binaries

**Decision.** `dstDESK/rec/qt-official/` is a Conan recipe that fetches the official
prebuilt Qt through aqtinstall and packages it. `conan install` remains the single
entry point for every dependency, and Qt is never compiled from source.

**Context — measured, not assumed.** ConanCenter publishes `qt` up to 6.11.1, so the
obvious path is `qt/6.8.3` in the conanfile. Probing it showed that path is not
available in practice:

- For `qt/6.8.3` there are **10 prebuilt binaries in total across all platforms** — two
  per platform, differing only in `shared`:
  `Linux/gcc13`, `Macos/apple-clang17` (x86_64 + armv8), `Windows/msvc194` (x86_64 +
  armv8).
- Both carry **`qtwebsockets=False`**. This project needs that module, and enabling it
  changes the package id, so no binary matches.
- Pinning a profile to gcc 13 and leaving QtWebSockets off — matching the published
  configuration exactly — *still* resolves to a missing binary. `conan graph explain`
  reports: **"This binary has same settings and options, but different
  dependencies."** ConanCenter's Qt was built against older `brotli`, `icu`, `openssl`
  and `harfbuzz`; current resolution produces a different package id and the binary is
  unreachable.

Managing Qt with ConanCenter therefore means compiling Qt and its dependency tree from
source on every machine, including a reviewer's. That is incompatible with the
requirement to be buildable from the README without broken setup.

**Rejected — invoking aqtinstall from the build script, outside Conan.** Simpler, and
what most projects do. It leaves Qt outside the dependency manager the brief asks for,
and splits acquisition across two mechanisms.

**Rejected — ConanCenter `qt` with `--build=missing`.** Correct in principle. A
multi-hour first build on the reviewer's machine is not an acceptable cost.

**Verified working.** The recipe was built and a probe compiled against it
(`kobayashi --selftest`):

```
Qt version        : 6.8.3
WebSocket server  : listening on port 45955
TLS supported     : true
TLS build version : "OpenSSL 3.0.7 1 Nov 2022"
TLS runtime ver   : "OpenSSL 3.5.5 27 Jan 2026"
```

**Implementation notes.**

- The recipe invokes `sys.executable -m aqt`, binding aqtinstall to the interpreter
  running Conan rather than to PATH ordering.
- The Qt prefix is **discovered** by globbing for `lib/cmake/Qt6`, not mapped from the
  architecture identifier: aqt installs `linux_gcc_64` into a folder named `gcc_64` and
  `clang_64` into `macos`, and a second hardcoded mapping would rot silently.
- `cmake_find_mode = "none"`, because Qt ships its own CMake package config carrying
  moc, uic and deployment tooling. A generated `Qt6Config` would shadow it.
- `package_id` erases build type and compiler version — one official binary serves any
  compiler version — but keeps the compiler, since msvc and mingw Qt builds are not
  interchangeable.

**Known costs.**

- The Conan package is **1.6 GB** per platform.
- Qt's TLS support is a `qopensslbackend` plugin that **dynamically loads the system
  OpenSSL**; Qt bundles no libssl of its own. The probe passed only because this
  machine has OpenSSL 3.5.5 installed. On a machine without a compatible OpenSSL,
  `wss://` to the transcription provider fails **at runtime, from a build that
  succeeded**.

---

## 12. OpenSSL is a Conan dependency, not a machine prerequisite

**Decision.** `openssl/3.x` is required from ConanCenter and placed on the runtime
environment, so Qt's TLS backend loads a known OpenSSL rather than whatever the host
happens to provide.

**Context.** Decision 11 established that official Qt carries no OpenSSL. Relying on
the system copy makes TLS a property of the reviewer's machine: modern Linux
distributions generally satisfy it, macOS ships LibreSSL rather than OpenSSL 3, and
Windows has no system OpenSSL at all.

**Rejected — documenting OpenSSL as a prerequisite in the README.** Cheapest, and it
converts a build-system problem into an instruction the reviewer can miss, failing at
runtime with an error that does not name the cause.

**Consequences.** ConanCenter publishes prebuilt `openssl` binaries, so this costs a
download rather than a build. Packaging via CPack must place the OpenSSL runtime beside
the executable on Windows.

**Revised by decision 17,** which stops requiring OpenSSL on Windows: Qt ships a
Schannel backend there. macOS is as described and now confirmed: Qt offers only the
OpenSSL backend, the system copy is LibreSSL, and the Conan-supplied OpenSSL is what
carries live `wss://` transcription on that platform.

---

## 13. Transcript ordering uses a finalisation watermark, not a fixed delay

**Decision.** Each stream reports how far it has been finalised. Everything below the
minimum across open streams is committed, sorted by start time; everything above stays
in a live zone that may still change. There is no fixed hold time.

```
finalizedUpTo[stream] = max(start + duration) over that stream's final results
watermark             = min(finalizedUpTo) across open, responding streams
commit                = every non-empty final with start < watermark, sorted by start
```

**The test is on `start`, not `end`.** Every stream has finalised everything before the
watermark, so any utterance still to come must *begin* at or after it — which means
anything pending that began earlier can never be preceded, whatever its own end. Waiting
for `end <= watermark` instead holds a long utterance behind a shorter, later one from
the other stream, and then has to place it before lines already committed: precisely the
scrambling this exists to prevent. An earlier version of this entry specified `end`; the
implementation has always used `start`, and the implementation is right.

**Context — measured, not assumed.** Two concurrent Deepgram connections were driven in
real time from a shared clock, one carrying a monologue and one a dialogue starting four
seconds later:

```
transcription lag (arrival - end of the audio it covers)
  min 1.29s   median 1.36s   p90 1.45s   max 2.57s
ordering
  15 utterances, 1 arrived out of audio order, worst by 0.19s
```

Both connections show nearly identical, stable lag, so arrival order almost matches
audio order already. Reordering is real but small.

**The property that makes a watermark possible.** The engine emits final results for
silence as well as speech — those carry `start` and `duration` but an empty transcript.
They are filtered from display, yet they still prove how far that stream has been
finalised. Both streams therefore report progress continuously, whether or not anyone
is speaking, and the watermark advances on its own.

**Rejected — hold every utterance for a fixed N seconds.** The obvious approach, and
what was originally planned. Choosing N means trading correctness against latency with
no way to be right: too small and the transcript still scrambles, too large and it stops
feeling live. A watermark needs no such constant, and its latency is whatever the engine
actually costs — about 1.4 s here — rather than a guess.

**Rejected — commit in arrival order.** Simplest, and wrong for exactly the case the
requirement names: when two people speak close together, the transcript reverses them.

**Consequences.**

- Below the watermark, ordering is complete **while both streams are responding**:
  neither can still produce anything earlier, so committed text never has to be
  rewritten.
- Latency adapts by itself. A degraded network slows the watermark instead of corrupting
  the order.
- A closed stream must be removed from the minimum, or the watermark freezes forever.
- **A stalled stream is the one hole in the guarantee, and it is deliberate.** A stream
  whose transcription connection has died is dropped from the minimum after 20 s of
  total silence, or the transcript stops advancing for the rest of the call while the
  other stream keeps producing text — indistinguishable, on screen, from the
  application being broken. The cost is that if that stream ever recovers and delivers
  a final from before the point the other stream has now committed past, the text is
  appended after lines it should have preceded. A transcript briefly out of order is
  recoverable; one frozen for the rest of the meeting is not. The threshold sits far
  above any working connection precisely so this is reached only when something is
  genuinely broken.
- A stream closed and reopened on the same connection resumes at the position it had
  reached, rather than restarting at zero. Restarting would pull the watermark back to
  the beginning of the session and stop anything committing until it had caught up.
- Interim results must never be promoted directly into committed text. Finals were
  observed to be *shorter* than the interim preceding them, and to revise words at the
  start: `"Finding people is my specialty. So, naturally, I work for the"` finalised as
  `"Finding people is my specialty, so, naturally, I work"`, with `"for the"` moving into
  the next segment and then being dropped from it.

---

## 14. Speaker diarisation is offered, not promised

**Decision.** Diarisation stays off by default and is exposed as an option. The two
streams remain the primary separation.

**Context.** The meeting stream carries every remote participant mixed together, so it
is N speakers rather than one. Deepgram can label them, and on test material it split a
two-person scene correctly — but on a second scene with two similar voices it collapsed
both into one speaker.

**Consequence.** The brief's requirement is met by the two streams, which are separated
by construction rather than by inference. Diarisation refines the meeting side when it
works and misleads when it does not, so it is not load-bearing.

---

## 15. The workspace entry point is Python, not a shell script

**Decision.** `dstOMNI/dst.py` runs every step that spans more than one repository:
toolchain preparation, building, testing, packaging, and lockstep tagging.

**Context.** The project targets Windows, macOS and Linux, so the entry point has to
run on all three. Python is already a hard requirement — the protocol generator runs
under it, and Conan and aqtinstall are Python packages — so choosing it adds no
dependency that is not already installed before anything can be built at all.

**Rejected — a shell script with a PowerShell twin.** The familiar shape, and two
implementations of the same logic to keep in step. Drift between them is exactly the
failure this repository exists to prevent, and it would show up as "works on Linux,
subtly wrong on Windows" — the hardest kind to notice.

**Rejected — driving everything through CMake.** Natural for building and useless for
the rest: tagging three git repositories and reporting workspace state are not build
steps, and expressing them in CMake would be a worse language for the job.

**Consequences.**

- Every subprocess is invoked as an argument list with no shell, because quoting rules
  differ per platform and a path containing a space is entirely ordinary on Windows and
  macOS.
- Paths go through `pathlib`, and the virtual environment's executables are looked up in
  `Scripts` on Windows and `bin` elsewhere.
- `doctor` exists so a machine can be told what it is missing before a 1.6 GB download
  begins rather than after it fails.

---

## 16. A target descriptor holds only what differs

**Decision.** `targets/*.cfg` carries the build type, the CPack generator, and the
loopback port — and nothing else. The build commands are identical on every platform.

**Context.** The temptation with per-target files is to give each platform its own
command set. There is nothing to put there: CMake and Conan already abstract the
compiler and the dependencies, so the only genuine differences are which archive format
a platform's users expect and what to bind.

**Rejected — a richer descriptor per platform.** It would be ceremony imported from a
system that needed it, describing differences this project does not have. A file that
exists to look thorough teaches a reader something untrue about where the complexity is.

**Consequence.** The default target is detected from the host, so the common case needs
no flag at all.

---

## 17. OpenSSL is required per platform, not everywhere

**Decision.** `openssl` is required on Linux and macOS and not on Windows, where Qt's
Schannel backend uses the platform's own TLS stack. This revises decision 12, which
required it unconditionally.

**Context.** Decision 12 left one thing to verify on hardware: which TLS backends the
official Qt packages actually ship. Windows `qtbase` does carry `qschannelbackend.dll`
alongside `qopensslbackend.dll` — read from the installed package on a Windows machine,
not inferred — so nothing needs shipping and nothing needs building. Which backend Qt
selects at runtime is reported by `--selftest`. It also
predicted that OpenSSL "costs a download rather than a build", and that prediction was
wrong on Windows: ConanCenter publishes no prebuilt binary for every MSVC release, so
`--build=missing` compiled OpenSSL from source — thousands of files, and the largest
single cost in a first build by a wide margin.

**Rejected — pinning `compiler.version` to one with prebuilt binaries.** It would buy a
download by lying about the compiler in use, and the lie surfaces later as an ABI
mismatch that names nothing relevant.

**Rejected — keeping OpenSSL everywhere for uniformity.** One dependency graph across
platforms is genuinely easier to reason about, but it charges every Windows reviewer a
long from-source build for a library the platform will not even load in preference to
Schannel.

**Consequences.** The dependency graph now differs by platform, so a build failure on
one target can be invisible on another — the argument for testing on hardware rather
than reasoning about it. `--selftest` reports the active backend by name, and on Windows
10.0.19045 with no OpenSSL present it reads `Secure Channel`, which is how this was
confirmed rather than assumed.

**Corrected later — TLS is a requirement of transcription, not of the application.**
There is exactly one `wss://` in this project: the connection to the transcription
service. The link the extension uses is plain `ws://` on loopback and needs no TLS at
all, and transcription is optional — the application records without a key by design
(decision 22). Despite that, a missing TLS backend was a fatal self-test check, so a
machine without a usable OpenSSL was refused a start rather than allowed to do the half
of the job that did not depend on it. That is now judged against what the run will
actually do: fatal when transcription is on, a warning when it is not. The output
directory is treated the same way against `--no-record`. A precondition should stop
only the feature that needs it.

---

## 18. The build tree is made runnable on Windows, not just packaged

**Decision.** A post-build step runs `windeployqt` on the Windows build, so the
executable in `bin/Release` starts on its own.

**Context.** ELF and Mach-O binaries carry a search path and start straight from the
build tree. Windows binaries do not, and the first thing anyone does with a freshly
built application is double-click it. Two weaker fixes were tried first and are worth
recording because each fails in a way that points away from the cause: putting Qt on
`PATH` fixes `dst.py run` and leaves double-clicking broken, and copying the linked DLLs
beside the executable moves the failure from a missing `Qt6Widgets.dll` to a missing
platform plugin, because Qt resolves plugins relative to `Qt6Core.dll` and that copy
changes where it looks.

**Rejected — telling the reviewer to run the packaged build instead.** Accurate, and it
answers a reasonable action with an instruction rather than making the action work. The
build tree is where a reviewer spends their time.

**Consequences.** Windows builds carry a copy of the Qt runtime in the build tree, which
costs disk and a few seconds on each relink. The `PATH` mechanism stays as the fallback
for a Qt install without `windeployqt`.

---

## 19. The origin check is the security boundary, and the extension id is pinned

**Decision.** The desktop application accepts browser connections only from Verbal's own
origin, and Verbal pins its id with a `key` in its manifest so that origin can be stated
in advance. A client sending no `Origin` header at all is accepted.

**Context.** A WebSocket to loopback needs no CORS preflight, so **any page the user
visits can open a socket to the desktop application** while a meeting is being recorded.
It could displace the live session and stream its own audio in, to be recorded and billed
to the user's transcription account. This is the only remotely reachable surface the
project has, and it was open: the origin list defaulted to empty, which accepted
everything.

The check works because a browser sets `Origin` itself and page script can neither forge
nor suppress it. What made it unusable before is that an unpacked extension's id changes
on every load, so there was no stable origin to name. Pinning the public key in the
manifest fixes the id, and that id lives in `protocol.json` because it is the one piece
of identity both halves must agree on.

**Rejected — requiring an `Origin` from every client.** It closes nothing: a native
process sends whatever headers it likes, and one already running as this user does not
need a socket to do harm. It would break `kobayashi-sim` and the wire check, which are
what make the protocol testable without a browser.

**Rejected — a shared token on by default.** It authenticates native clients, which the
paragraph above argues is not the threat, and it needs provisioning: the same secret
typed in two places, or generated into a file the extension can somehow read. `--token`
stays available for anyone who wants it.

**Consequences.** Reloading the extension from another directory no longer changes its
id. Changing the manifest `key` now means changing `protocol.json` too, and the generator
carries it to both sides.

**Known gaps, deliberately left.** Each is marked `HP:TODO` at the place it applies,
rather than only listed here:

| Gap | Where | Why it is left |
|---|---|---|
| Recordings written unencrypted | `Core/StreamRecorder.hpp` | Needs retention and key management to mean anything |
| API key in plain text, owner-only | `App/Settings.hpp` | A real fix is three platform keychains and a fallback |
| An accepted client displaces a live session | `IO/WsServer.cpp` | Harmless while the only accepted origin is the extension's |
| No token by default | `IO/WsServer.cpp` | The origin check covers the reachable threat |
| Transport is `ws://`, not `wss://` | `dstORCH/src/offscreen.js` | Nothing leaves the machine; a loopback certificate costs more than it buys |
| Audio goes to a third party | Deepgram, by design | Inherent to the brief; stated in the README |

---

## 20. setup provides the build tools, not only the libraries

**Decision.** `setup` installs CMake and Ninja into `.venv` from PyPI, alongside Conan and
aqtinstall, and everything this script spawns runs with `.venv` first on `PATH`. The
machine is expected to supply a C++ compiler, Git and Python, and nothing else.

**Context.** `doctor` reported CMake missing and then said to run `setup`, which did not
install CMake and by design never would. Worse, `build` did not check for it at all, so on
a machine without one the failure surfaced from inside a dependency's own build —
`/bin/sh: cmake: command not found` while compiling zlib, twenty-five seconds in, naming
the dependency rather than the cause. Conan pulls a CMake of its own for recipes that ask
for one, so its appearance in the log is not evidence the machine has one, which makes the
output actively misleading.

CMake on PyPI ships Kitware's own binaries for all three platforms, and Ninja was already
installed this way — so this is an existing decision applied consistently rather than a
new dependency. It also fixed a latent fault: nothing put `.venv` on the `PATH` of spawned
processes, so the Ninja that `setup` installed was only ever found when the caller had
already activated the environment.

**Rejected — leaving CMake a prerequisite and only fixing the message.** Honest, and
cheaper. But the brief asks for a project that builds from the README with no missing
steps, and every prerequisite is a step that can be missing. macOS ships no CMake at all,
so this was the difference between two commands and a Homebrew detour.

**Rejected — using a system CMake when one is present.** It saves a download and makes the
toolchain differ per machine, which is the opposite of what a reproducible build wants.
The version in use is now the same everywhere.

**Consequences.** `setup` downloads roughly 40 MB more. The build uses CMake 4.x, which
rejects recipes declaring very old minimums — verified by building zlib from source under
it, and Conan tool-requires its own CMake for recipes that demand a particular one. A
system CMake is now ignored, so someone debugging a version question should look in
`.venv` first.

---

## 21. protocol.js is committed, and should not be

**Decision.** `dstORCH/src/generated/protocol.js` is committed. Every other generated file
stays out of version control, `dstDESK/src/Core/Protocol.hpp` included.

**Context.** The brief asks for an extension that is loadable unpacked. A Chrome extension
has no build step, so a reviewer who clones `dstORCH` and loads it — the first thing the
task describes — gets an extension whose service worker dies on an import for a file
nothing in their sequence was going to produce. `dstDESK` does not have this problem: its
own build regenerates the header before compiling anything, so the rule costs it nothing.
The rule was right; it was only ever paid for on one side.

**This is a concession to the task, not a design improvement.** A generated file in version
control is a second copy of the truth, free to drift from `protocol.json`, and the only
thing standing against that is `generate.py --check` running in `dstDESK`'s build — which
a reviewer touching only the extension never runs. The file says so itself, at the top,
where someone tempted to edit it will read it.

**Rejected — leaving it generated and documenting the step.** What decision 3 already did.
It works if the README is read in order, and turns the task's own first instruction into a
failure for anyone who does not.

**Rejected — giving the extension a build step.** A bundler would make the generated file
an ordinary build artifact and settle this properly. It also adds Node, a package manifest
and a lockfile to a repository that currently needs no toolchain at all, to produce a file
that is 40 lines of constants.

**What would undo it.** A build step on the extension side, or publishing the extension as
a package rather than a directory. Either makes the file an artifact again, and it comes
back out of git.

---

## 22. Recording to disk was never asked for, and is kept anyway

**Decision.** Both streams are written to WAV files by default, and `--no-record` turns
that off while keeping the frame accounting.

**Context.** The brief asks for capture, transcription and a live transcript. It does not
ask for recordings. They exist because they came first: before there was a transcription
key, writing WAV files was the only way to show that frames were arriving intact and in
order, and nothing went back to ask whether the scaffolding should stay once transcription
worked. The naming still shows it — `StreamRecorder`, `--output`, "Recording into …" — an
application that describes itself as a recorder which also transcribes, which is backwards
from the task.

Two arguments kept it. It is the evidence layer: `0 gaps (0 padded samples) 0 rejected`
means something only because the recorder reconstructs a continuous timeline from frame
indices, and that is how the transcript-ordering bug was found and how the endurance run
was judged. And it makes the application do something useful with no account at all, which
serves "runnable from the README" for a reviewer who has no Deepgram key.

**Rejected — removing it.** Honest to the brief, and it would delete the only mechanism
that makes frame-level correctness falsifiable.

**Rejected — leaving it mandatory.** It is the project's largest privacy exposure: meeting
audio, unencrypted, kept until someone deletes it. An unrequested feature that cannot be
switched off is worse than the same feature with a flag.

**Consequences.** `--no-record` keeps the counting and writes nothing, so the session
summary still reports gaps and rejects with no audio on disk. The default remains on,
because a reviewer without a key would otherwise see an application that appears to do
nothing at all.

**A recorder that fails must say so.** Calling this the evidence layer only means
something if a failure is visible: a full disk, a removed drive or an unwritable folder
used to leave the frame counters climbing and the session summary reporting a clean run
over a file that had stopped growing. Both are now surfaced — a write that fails marks
the stream and the summary says `WRITE FAILED`, and a stream whose file cannot be opened
at all says so in the window rather than in a console line a double-clicked application
does not have. The failing direction was verified against `/dev/full` and a read-only
directory; the unit suite guards the other direction, that a healthy recording never
raises it.

---

## 23. Echo cancellation is the acoustic half of "kept separate"

**Decision.** The microphone is captured with `echoCancellation: true`, and the browser
is asked afterwards whether it actually applied it. `noiseSuppression` and
`autoGainControl` stay on, named as inherited defaults rather than measured choices.

**Context.** Decision 10 replays the captured meeting audio so the user can still hear
the call. On a laptop that audio leaves a speaker a few centimetres from the microphone
and comes straight back into it. The brief's requirement is two streams "kept separate
so the conversation can be followed in a natural order" — and stream identity only
keeps them separate *logically*. Acoustically, an uncancelled echo path puts the remote
participants' words into the microphone stream, where they are transcribed and shown as
something the local user said. The two transcripts then agree with each other, a second
apart, and the conversation reads as one person repeating the other.

So the separation this project is graded on rests on two mechanisms, not one: the stream
id, which is exact and was designed; and echo cancellation, which is statistical and was
inherited from a snippet. Only the first of those was written down until now.

**What is actually relied on.** Chrome's AEC3 runs inside `getUserMedia`, before the
track reaches the capture `AudioContext`, and takes its reference from the browser's own
render mix — which the `<audio>` element in the offscreen document is part of. So the
mechanism is present by construction. What it does not promise is completeness: a linear
canceller plus residual suppression leaves some echo, most of it during **double-talk**,
when suppression is relaxed so the near-end speaker is not clipped. Double-talk is also
exactly when a meeting transcript is hardest to order, so the failure lands where it
costs most.

**Rejected — dropping the playback so there is no echo path.** Removes the problem
entirely and makes the product unusable: the user stops hearing their own meeting.

**Rejected — capturing the microphone raw and cancelling in the desktop application.**
Full control, and it means implementing an adaptive filter with delay estimation against
a reference stream that has crossed a socket. That is a project, and it is the browser's
job here.

**Rejected — deduplicating in `TranscriptMerger`.** Both streams share a clock, so
microphone text that matches meeting text a moment earlier could be detected and
suppressed. It is a real option and the natural next step if leakage turns out to
matter. It is not a substitute: it corrects the transcript after the fact and does
nothing for the recordings, and a rule that deletes text because the other stream said
something similar will eventually delete someone agreeing out loud.

**What has been run.** Live Google Meet calls on all three platforms, repeatedly, with the
laptop's built-in speakers rather than headphones — which is the exposed configuration, not
the safe one: on a laptop the speaker sits centimetres from the microphone, so the echo path
is as strong as it gets. The microphone transcript did not carry the remote participants.
That is the property, and it has held every time it was looked at.

**What has not.** No measurement of *how much* echo survives, only of whether enough
survives to be transcribed. External speakers at volume, a reverberant room, and sustained
double-talk — where cancellation is deliberately relaxed so the near-end speaker is not
clipped — are the conditions that would test it hardest, and none of them has been tried on
purpose. The check added here reports when the browser declines the constraint; it cannot
report what a browser that accepted it actually achieved.

**Consequences.** A microphone that comes back without echo cancellation now says so on
the extension badge instead of producing a quietly wrong transcript. `noiseSuppression`
and `autoGainControl` are named constants with the reasoning attached, so turning them
off is a one-line experiment rather than an archaeology exercise.

---

## 24. The transcript is a model and a view, not a widget per line

**Decision.** Committed utterances live in a `QAbstractListModel` over a
`std::vector<Core::Utterance>`, are filtered by a `QSortFilterProxyModel`, and are drawn
by a delegate into a `QListView`. The previous version built one `QWidget`, one
`QHBoxLayout` and three `QLabel`s per utterance and appended them to a `QVBoxLayout`.

**Context — measured, not assumed.** The widget version was written for a demo and
verified in sessions of one to two minutes, where nothing it does badly is visible. A
harness that appends utterances and forces the layout and paint that a real window would
do reports this, at 1200 lines — roughly an hour of meeting:

```
                        widget per line      model and view
append, per line        2.7 ms rising           0.21 ms flat
                          to 21.6 ms
1200 appends, total        14310 ms                247 ms
first search keystroke       199 ms                  3 ms
clearing the search         1325 ms                  2 ms
stream filter                 55 ms                  1 ms
```

The number that matters is not the ratio, it is the shape. The widget version's
per-append cost **grows with the number of lines already shown**, so an hour-long meeting
gets slower the longer it runs — 21.6 ms per line at 1200, still climbing past 53 ms at
3400, where the run was abandoned. Lines arrive faster than that during an argument.

Two things caused it. Appending to a `QVBoxLayout` invalidates the layout, and solving it
means asking every child for its size — and word-wrapped `QLabel`s answer through
`heightForWidth`, which is the expensive kind of question. Separately, `append()` called
`applyFilters()`, which walked every row on every line.

Clearing a search was the worst single moment: a second and a third of a frozen window at
1200 lines, because a thousand hidden widgets became visible at once and the layout had to
solve all of them.

**Rejected — fixing only the sweep.** `append()` need not re-render rows it did not
change, and that is a twenty-line change. It removes the smaller of the two costs and
leaves the layout solve, which is most of it. Worth doing if nothing else were possible;
it is not a fix, it is a discount.

**Rejected — a single `QTextEdit` or `QPlainTextEdit`.** A transcript is a document, and
`QPlainTextEdit` is built for exactly this shape — ever-growing, append-only, with text
selection and copying for free. Genuinely the smaller change. It was rejected because
filtering by stream and by search would mean rebuilding the document on every keystroke,
where a proxy model filters without touching the presentation at all; and because the
per-row layout — timestamp column, coloured speaker, dimmed low-confidence text — would
have to be rebuilt as generated HTML over arbitrary speech, which is an escaping bug
waiting to be written.

**Rejected — keeping only the last N lines.** Instant, and it throws away the meeting.

**Consequences.**

- Cost stops depending on history. At 20,000 lines appends are 0.47 ms and clearing a
  search is 5 ms — some growth remains, because `QListView` still walks its rows to size
  the scrollbar, but it walks a cached integer per row rather than solving a layout.
- Measurement and painting share one `QTextLayout` code path, so the height a row is
  given is by construction the height its text is drawn at. Two code paths drift.
- Row heights are cached per row and discarded when the viewport **width** changes; a
  height-only resize keeps them, since it changes nothing about how text wraps.
- Search highlighting is a `QTextLayout::FormatRange`, not markup. The old version built
  HTML out of speech and escaped it by hand — correct as written, and one missed call
  from swallowing a line containing a `<`.
- The stored type is `Core::Utterance`, the same Qt-free struct the merger emits, so the
  window holds the transcript rather than a display-shaped copy of it.
- `TranscriptView`'s public interface is unchanged, so `MainWindow` did not move.

**Cost.** Two new files and a delegate, against a widget tree anyone can read at a
glance. A delegate is the harder thing to modify — there is no widget to inspect, and
getting a column wrong means reading paint code rather than looking at a layout. That is
the trade a view makes, and it is worth it here only because the row count is unbounded.

---

## 25. A dropped transcription connection is retried, not mourned

**Decision.** `SttClient` reconnects on an unexpected drop, with exponential backoff, a
budget of five attempts, and no retry at all for an answer that will not change. It owns
the mapping between the engine's clock and the shared capture clock, so a replacement
connection is re-based rather than believed.

**Context.** A dropped connection was indistinguishable from a finished one: both reached
`onDisconnected`, which emitted `finished`, which closed the stream in the merger. The
consequences were not proportionate to the cause. A momentary network blip — the sort a
laptop produces by changing wifi bands — ended transcription for that stream **for the
rest of the meeting**. Audio kept arriving and kept being recorded, so the recording was
perfect and half the transcript simply stopped, with nothing said anywhere.

**What made it correct rather than merely present.** Three things, each of which was wrong
in a first attempt and found by running it against a mock that misbehaves on purpose:

- *A new connection starts the engine's clock at zero.* The origin must come from the
  **front of the buffered audio**, established before that buffer is sent. Taking it from
  the next frame to arrive instead dates every result on the reconnected stream by however
  long the outage lasted — measured as a 1.65 s hole where the real one was 0.17 s.
- *Interrupted is not closed.* The stream keeps its place in the merger so its text still
  lands in order, but it is marked stalled so it stops holding the watermark down; the
  other stream must not freeze for the length of the outage. This is the mechanism
  decision 13 already needed for a dead engine, used for a live one that will be back.
- *The retry budget resets on health, not on success.* Resetting it whenever a result
  arrived looked right and was not: a connection that comes up, delivers a second of
  transcript and dies has "worked" by that measure, so a link flapping once a second reset
  the budget every time and reconnected forever — **34 sessions in 28 seconds**, each one
  separately billable. Judged on how long the connection lasted instead, the same mock
  produces six connections and then a clear refusal.

**Rejected — reconnecting in `WsServer` by rebuilding the client.** Where the first
version accidentally was, since `isFinished()` became true on any drop. It reconnects the
socket and loses everything: the merger's stream is already closed, so `addFinal` discards
every result the new connection produces. The clock mapping and the socket belong to the
same object or neither works.

**Rejected — retrying a 401 or a 403.** They are answers, not accidents: the key is wrong
or the model is refused. Retrying spends fifteen seconds to say the same thing.

**Rejected — buffering the whole outage.** Three seconds are kept, so an ordinary blip
loses nothing; past that the audio is counted and dropped, and the count is reported on
resumption. Holding minutes of audio to replay into an engine that times from its first
byte trades a small gap for a large distortion.

**Cost.** A local mock had to exist before any of this could be believed, which is why
`kobayashi-mockstt` and `--stt-endpoint` now do. That flag is testing scaffolding in the
shipped binary, and it earns its place: without it the transcription and merge path — the
hardest part of this project to get right — could only ever be exercised against a paid
third party, so it had no test at all.

**Verified, and the verification is verified.** `dst.py test` runs two sessions against the
mock. A single drop must resume, report itself, leave the other stream untouched, and land
back on the same one-second grid the uninterrupted stream sits on. A link that drops
repeatedly must back off 500 ms, 1 s, 2 s, 4 s, 8 s and then refuse, without opening
further connections.

Both assertions were confirmed against the faults they exist for, by putting each fault
back and watching the check fail:

| Fault reintroduced | What the check said |
|---|---|
| Origin taken after the buffered audio | `reconnected stream is 0.49s off the shared clock` |
| Retry budget reset by any result | `never gave up` · `reconnect storm: 40 connections were opened` |

The second of those took two attempts to catch. The first flapping scenario dropped the
connection at exactly one second, which is before the mock reports anything — so no result
arrived, the faulty reset never ran, and the check passed over the bug it was written for.
A regression test that has not been shown to fail is decoration.

---

## 26. The feature list is closed, and written down instead

**Decision.** No further features. The ones considered and not built are listed in
[`README.md`](README.md), each with its reason, and the three that would come first are
named in order.

**Context.** The obvious way to make a submission look substantial is to add to it. A
transcript exporter, dark mode, a tray icon, hotkeys, a recordings browser, cloud sync —
each is an afternoon, and with AI assistance rather less than that.

That is exactly why they are not here. This project was written with heavy AI assistance,
which the task permits on the condition that the candidate orchestrates it. Under that
condition code is the cheap part, and the scarce part is what can be attributed to a
person: the architecture, the trade-offs, and the evidence that each was tested rather than
assumed. A feature generated from a prompt adds nothing to that column. It adds a great
deal to the other one — more surface for a reviewer to read, more places for something to
be subtly wrong, and a longer path between them and the decisions that are actually mine.

There is a second cost, less obvious. Every feature is a claim that it works, and this
project's claims are backed by having run them on three machines. Twenty features means
twenty more things to verify on each, or a submission whose verification table has quietly
become aspirational.

**Rejected — building the small ones anyway.** Transcript export in particular is a real
absence and a genuinely small change. Left out because the boundary is only worth anything
if it holds: a list of things not built, minus the easy ones, is just a backlog.

**Rejected — saying nothing about it.** Then every absence reads as an oversight, and the
most interesting ones — no LLM though the task allows it, no CI, a transcript that is never
saved — look like things that were not thought about.

**Consequences.** The submission is smaller than it could be. The features are recorded
rather than built, which means the reasoning is reviewable even though the code is not.
Several entries are stopping points rather than positions — the single light palette, and
syncing with the meeting's microphone button, which was built once and removed when it
proved to depend on the structure of someone else's page.

---

## Pending decisions

None currently open.
