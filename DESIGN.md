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

---

## 4. Windows, macOS, and Linux — not the one platform required

**Decision.** All three desktop platforms are supported and verified.

**Context.** The brief requires Windows *or* macOS. Supporting all three is close to
free here: **all audio capture happens in the browser**, so the C++ application never
touches WASAPI, CoreAudio, or ALSA. It is a WebSocket server, an STT client, and a Qt
window — three portable dependencies with no platform-conditional audio code.

**Cost.** Verification time on three platforms rather than one. No new engineering.

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
  `Origin: chrome-extension://<id>` header Chrome sends and requires a shared token
  from the target configuration.
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
(`dstdesk --selftest`):

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

**Revised by decision 17,** which stops requiring OpenSSL on Windows on the expectation
that Qt ships a Schannel backend there. macOS remains as described: Qt offers only the
OpenSSL backend, and the system copy is LibreSSL.

---

## 13. Transcript ordering uses a finalisation watermark, not a fixed delay

**Decision.** Each stream reports how far it has been finalised. Everything below the
minimum across open streams is committed, sorted by start time; everything above stays
in a live zone that may still change. There is no fixed hold time.

```
finalizedUpTo[stream] = max(start + duration) over that stream's final results
watermark             = min(finalizedUpTo) across open streams
commit                = every non-empty final with end <= watermark, sorted by start
```

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

- Below the watermark, ordering is provably complete: neither stream can still produce
  anything earlier, so committed text never has to be rewritten.
- Latency adapts by itself. A degraded network slows the watermark instead of corrupting
  the order.
- A closed stream must be removed from the minimum, or the watermark freezes forever.
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
official Qt packages actually ship. Windows `qtbase` is expected to carry a Schannel
backend alongside the OpenSSL one, so nothing needs shipping and nothing needs building
— `--selftest` naming `schannel` on the target machine is what confirms it, and until
that has been read on hardware this rests on how Qt configures its Windows packages
rather than on measurement. It also
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
than reasoning about it. `--selftest` reports the active backend by name, which is how
this was confirmed rather than assumed.

---

## Pending decisions

None currently open.
