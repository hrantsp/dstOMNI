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

**Decision.** The normative protocol specification lives in `dstDESK/protocol/`.

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

**Decision.** `dstDESK/protocol/` holds the specification and a generator. The build
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

## Pending decisions

- **Extension → desktop transport.** Localhost WebSocket versus Chrome Native
  Messaging. Native Messaging is the Chrome-native mechanism but frames everything as
  JSON over stdio with a 1 MB message ceiling, which is a poor fit for continuous
  audio. To be recorded once settled.
- **Audio format on the wire.** Sample rate, channel count, sample encoding, and frame
  size.
- **Timestamp and ordering model.** How two independently transcribed streams are
  merged into a conversation that reads in natural order.
- **Qt acquisition strategy.** Conan-built Qt versus system Qt, and the effect on
  cold-clone build time.
