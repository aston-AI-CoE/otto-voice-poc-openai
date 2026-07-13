# Consolidation Plan

## TL;DR

This repository is a standalone OpenAI Realtime voice POC for Otto. It is not ready for redistribution until a license decision is recorded and the absolute Otto dependency paths are replaced or wrapped inside `aston-mono`.

No files should be copied yet. The proposed destination root for retained runtime files is:

`aston-mono/apps/otto/voice/openai-realtime-poc/`

Documentation that is useful beyond the POC should move under:

`aston-mono/docs/otto/voice/openai-realtime-poc/`

## File Map

| Current file | Proposed `aston-mono` destination | Keep? | Notes |
| --- | --- | --- | --- |
| `.env.example` | `apps/otto/voice/openai-realtime-poc/.env.example` | Yes | Keep as names-only local configuration template. Do not include credential values. |
| `.gitignore` | `apps/otto/voice/openai-realtime-poc/.gitignore` | Yes | Keep local ignores for `.env`, virtualenvs, caches, and OS files. |
| `CONSOLIDATION.md` | `docs/otto/voice/openai-realtime-poc/CONSOLIDATION.md` | Yes | Keep as migration checklist until consolidation is complete. |
| `TOOL_CALLING.md` | `docs/otto/voice/openai-realtime-poc/TOOL_CALLING.md` | Yes | Retains the Realtime tool-call architecture notes. |
| `client.html` | `apps/otto/voice/openai-realtime-poc/client.html` | Yes | Browser client for WebRTC audio, OpenAI data-channel events, and tool-result round trips. |
| `prompt.txt` | `apps/otto/voice/openai-realtime-poc/prompt.txt` | Yes | POC system prompt. Review safety posture before reuse. |
| `requirements.txt` | `apps/otto/voice/openai-realtime-poc/requirements.txt` | Yes | POC Python dependencies. Convert to the destination repo's dependency system during consolidation. |
| `server.py` | `apps/otto/voice/openai-realtime-poc/server.py` | Yes | FastAPI server for session creation and local tool execution. Requires path cleanup before production use. |
| `start.sh` | `apps/otto/voice/openai-realtime-poc/start.sh` | Yes | Local start helper. Replace with repo-native task runner if `aston-mono` has one. |
| `stubs/claude_agent_sdk/__init__.py` | `apps/otto/voice/openai-realtime-poc/stubs/claude_agent_sdk/__init__.py` | Yes | Temporary compatibility stub for importing Otto memory tools outside the full Otto runtime. Remove if `aston-mono` supplies the real package. |
| `stubs/claude_agent_sdk/types/__init__.py` | `apps/otto/voice/openai-realtime-poc/stubs/claude_agent_sdk/types/__init__.py` | Yes | Temporary compatibility stub paired with the file above. |
| `tests/offline_smoke_test.py` | `apps/otto/voice/openai-realtime-poc/tests/offline_smoke_test.py` | Yes | Credential-free smoke test for static validation and consolidation coverage. |

## Setup

Local setup for the current standalone POC:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set local environment values in `.env`. Keep `.env` private and untracked.

The current server imports Otto runtime modules from absolute paths:

- `/root/otto/library/bash`
- `/root/otto/services/worker`

During consolidation, replace those absolute paths with `aston-mono` package imports, workspace-relative paths, or an explicit adapter. Do not preserve `/root/otto/...` as a long-term dependency contract.

## Required Secrets

Required secret names:

- `OPENAI_API_KEY`

Non-secret runtime configuration names:

- `OPENAI_REALTIME_MODEL`
- `OPENAI_VOICE`

Do not commit `.env`, API keys, database credentials, Redis credentials, or generated client secrets.

## Offline Validation

Credential-free validation:

```bash
python3 tests/offline_smoke_test.py
```

The smoke test intentionally does not import `server.py` because importing it requires external Otto runtime paths. Instead it:

- parses Python files for syntax errors;
- parses `client.html` with the standard-library HTML parser;
- confirms every tracked source/doc/config file appears in the consolidation map;
- confirms `.env` is not tracked;
- confirms `.gitignore` still ignores `.env`;
- checks that `.env.example` does not contain key-shaped placeholder credentials.

Full live validation requires `OPENAI_API_KEY`, browser microphone permission, network access to OpenAI Realtime, and the Otto runtime dependencies listed above. Live tool execution is intentionally outside the offline smoke test.

## Tool-Execution Safety Assumptions

This POC exposes tool definitions that let the model request local shell commands through the browser-mediated data channel. The FastAPI server executes those requests with `BashExecutor`.

Safety assumptions before any live use:

- Run only in a disposable or tightly controlled development environment.
- Treat model-requested shell commands as untrusted until a human has accepted the POC risk.
- Do not point the POC at repositories, home directories, or systems containing credentials unless tool execution is constrained.
- Do not use live memory tools unless their backing database and Redis instance are explicitly intended for POC data.
- Do not expose the FastAPI server beyond localhost without authentication and command-execution controls.

## Credential Tracking Confirmation

Current repository hygiene requirements:

- `.gitignore` includes `.env`.
- `.env` must not appear in `git ls-files`.
- `.env.example` must contain variable names and non-secret defaults only.

Run the offline smoke test before consolidation to re-check these requirements.

## License Decision Required

No license file is present in this standalone POC. Before redistribution, publication, or copying into a repository with broader access, record the intended license and confirm compatibility with:

- `aston-mono` repository policy;
- OpenAI API terms for any included sample code or docs;
- Otto source dependencies referenced by the POC;
- Python and browser-side dependency licenses.

Redistribution should remain blocked until that license decision is documented.
