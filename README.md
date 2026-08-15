# dstOMNI

Organizer for the dual-stream transcription pipeline: a Chrome extension that captures
microphone and tab audio separately from a Google Meet call, and a cross-platform C++
desktop application that transcribes both streams and displays them as a live,
correctly ordered conversation.

This repository holds the build entry point, target descriptors, and documentation.
The implementation lives in two sibling repositories.

## Components

| Repo | Role |
|---|---|
| [`dstDESK`](https://github.com/hrantsp/dstDESK) | C++/Qt desktop application. Receives both streams, transcribes them, renders the conversation. Owns the wire protocol. |
| [`dstORCH`](https://github.com/hrantsp/dstORCH) | Chrome MV3 extension. Captures microphone and tab audio and sends them to the desktop app. |
| `dstOMNI` | This repository. Builds, packages, and versions the other two. |

`dstORCH` is the *orchestra* — it plays, and `dstDESK` listens.

## Workspace layout

The three repositories are cloned side by side. `dstOMNI` locates the others
relative to its own parent directory, so the directory names matter.

```
workspace/
├── dstDESK/
├── dstORCH/
└── dstOMNI/
```

```bash
mkdir workspace && cd workspace
git clone https://github.com/hrantsp/dstDESK.git
git clone https://github.com/hrantsp/dstORCH.git
git clone https://github.com/hrantsp/dstOMNI.git
```

## Versioning

All three repositories carry the same version tag. A given `vX.Y.Z` of `dstORCH` is
built and verified against exactly that `vX.Y.Z` of `dstDESK`; checking out matching
tags across the workspace yields a combination known to work.

## Design

Architectural decisions, the alternatives considered, and what each one costs are
recorded in [DESIGN.md](DESIGN.md).

## Status

Under active development. Build and run instructions are added to this README as each
part of the pipeline lands, so that every step documented here is a step that works.
