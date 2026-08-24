# 🤖 AI-Powered PR Review Bot (GitHub Action)

[![Pytest & Ruff Checks](https://github.com/your-org/pr-review-bot/actions/workflows/test.yml/badge.svg)](https://github.com/your-org/pr-review-bot)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Groq LPU](https://img.shields.io/badge/Powered%20By-Groq%20Cloud-f55036.svg)](https://groq.com)
[![Docker Container Action](https://img.shields.io/badge/GitHub%20Action-Docker-2496ED.svg)](https://github.com/features/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An autonomous, multi-step **AI code review agent packaged as a Docker-based GitHub Action**. Unlike single-prompt diff summarizers, this bot executes a **ReAct tool-calling loop** powered by Groq's high-throughput LPU inference (`llama-3.3-70b-versatile` or `llama-3.1-8b-instant`). It selectively inspects PR diffs, fetches surrounding codebase context, scans for cross-file caller regressions, and posts batch inline and summary reviews directly to GitHub Pull Requests in seconds.

---

## 📑 Table of Contents
- [Architecture & System Design](#-architecture--system-design)
- [Architectural Decisions & Technical Rationale](#-architectural-decisions--technical-rationale)
  - [1. Python + Docker Container Action vs. JavaScript Native Action](#1-python--docker-container-action-vs-javascript-native-action)
  - [2. Groq LPU Inference for CI/CD Time Horizons](#2-groq-lpu-inference-for-cicd-time-horizons)
  - [3. Multi-Step ReAct Tool-Calling vs. Monolithic Prompting](#3-multi-step-react-tool-calling-vs-monolithic-prompting)
- [Agent Tool Suite](#-agent-tool-suite)
- [Agent Reasoning Lifecycle](#-agent-reasoning-lifecycle)
- [Quick Start: Adding to Your Repository](#-quick-start-adding-to-your-repository)
- [Local Development & Offline Simulation](#-local-development--offline-simulation)
- [Testing & Quality Assurance](#-testing--quality-assurance)
- [Known Limitations & Future Roadmap](#-known-limitations--future-roadmap)

---

## 🏗️ Architecture & System Design

```
+-----------------------------------------------------------------------------------+
| GitHub PR Event (opened, synchronize)                                             |
+----------------------------------------+------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| Docker Container Action Runner (entrypoint.py)                                     |
|  - Ingests GITHUB_EVENT_PATH, secrets, and environment parameters                 |
+----------------------------------------+------------------------------------------+
                                         |
            +----------------------------+----------------------------+
            |                                                         |
            v                                                         v
+-----------------------------+                           +-------------------------+
| GitHubClient Layer          |                           | ToolRegistry            |
| - PyGithub wrapper          |                           | - JSON Schema generator |
| - Exponential Backoff       |                           | - Dispatcher & Logger   |
| - Rate Limit Sentinel       |                           +------------+------------+
+-------------+---------------+                                        |
              |                                                        |
              +----------------------------+---------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
| Agent Orchestration Loop (src/agent/loop.py)                                      |
|                                                                                   |
|   Step 1: get_pr_diff() + get_pr_metadata()                                       |
|   Step 2: get_file_content() / search_codebase() (Context Gathering)              |
|   Step 3: post_inline_comment() (Targeted Finding Staging)                        |
|   Step 4: post_summary_comment() (Risk Assessment & Review Checklist)             |
|                                                                                   |
|   Safeguards:                                                                     |
|     * Max Tool-Call Ceiling (default: 15)                                         |
|     * Non-blocking Advisory Mode (Never auto-merges or auto-approves)             |
|     * Resilient Fallback Flush on LLM / API errors                                |
+------------------------------------------+----------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
| PullRequestReview Batch Submission (Inline Comments + Summary Markdown)          |
+-----------------------------------------------------------------------------------+
```

---

## 💡 Architectural Decisions & Technical Rationale

### 1. Python + Docker Container Action vs. JavaScript Native Action
- **Context**: GitHub Actions natively executes JavaScript via Node.js with zero container startup overhead. Running Python natively in GitHub Actions requires a composite action that downloads Python and runs `pip install` on every single PR run, adding **20–45 seconds of runtime overhead** per workflow execution.
- **Solution**: Packaging the bot as a **Docker Container Action** (`runs.using: docker`). 
- **Rationale**: 
  - The container action produces an **immutable, hermetic runtime** with Python 3.11, all dependencies, and native utilities (`ripgrep`, `git`) pre-compiled into cached image layers.
  - Zero environment drift across host runners.
  - No dependency installation latency during the CI execution lifecycle.

### 2. Groq LPU Inference for CI/CD Time Horizons
- **Context**: A multi-step agent loop making 5 to 8 sequential tool calls over traditional cloud GPU providers takes **45 to 90 seconds**. In a fast-paced development workflow, slowing down the CI pipeline directly hurts developer productivity.
- **Solution**: Groq's Language Processing Units (LPUs) deliver **300 to 800 tokens/second** on tool-capable models.
- **Outcome**: The entire 4-stage agent reasoning loop finishes in **under 4 to 8 seconds**, making automated AI code review practical as a synchronous pull request check.

### 3. Multi-Step ReAct Tool-Calling vs. Monolithic Prompting
- **Context**: Monolithic prompts that dump the entire git diff into a single prompt suffer from token truncation, high hallucination rates, and lack surrounding repository awareness.
- **Solution**: The ReAct tool-calling loop enables dynamic problem decomposition:
  1. Inspects the diff summary and PR metadata.
  2. Selectively reads surrounding file lines (`get_file_content`) only when needed.
  3. Scans across repository files (`search_codebase`) to detect whether a modified signature broke callers elsewhere in the codebase.
  4. Formulates line-anchored comments iteratively.

---

## 🛠️ Agent Tool Suite

Every tool is implemented with a strict JSON Schema standard compatible with OpenAI and Groq function-calling:

| Tool Name | Parameters | Purpose |
| :--- | :--- | :--- |
| `get_pr_diff` | `max_lines` (int) | Fetches the unified Git diff for the Pull Request. |
| `get_pr_metadata` | *None* | Retrieves PR title, description, author, and branch context. |
| `get_file_content` | `path` (str), `ref` (str), `max_lines` (int) | Fetches full file contents at a given commit/branch for surrounding context. |
| `search_codebase` | `query` (str), `file_pattern` (str), `max_results` (int) | Uses `ripgrep`/regex across the checked-out repo to find symbol callers and imports. |
| `post_inline_comment` | `file` (str), `line` (int), `comment` (str), `side` (str) | Stages an inline review finding anchored to a specific file and line. |
| `post_summary_comment`| `summary_text` (str), `risk_level` (str), `checklist` (list) | Stages the final executive summary review and risk assessment (`LOW`/`MEDIUM`/`HIGH`). |

---

## 🔄 Agent Reasoning Lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant GH as GitHub REST API
    participant EP as Entrypoint
    participant AG as AgentLoop
    participant GQ as Groq LLM (LPU)
    participant TR as ToolRegistry

    EP->>GH: Get PR Metadata & SHA
    EP->>AG: Initialize Agent Session
    loop Agent Reasoning Loop (Max 15 steps)
        AG->>GQ: chat.completions.create(messages, tools)
        GQ-->>AG: Tool Calls (e.g., get_pr_diff)
        AG->>TR: execute(tool_name, arguments)
        TR->>GH: Fetch resource / search codebase
        GH-->>TR: Resource Data
        TR-->>AG: ToolResult
        AG->>AG: Append Tool Response to Messages
    end
    AG->>EP: Return AgentReviewResult (staged comments + summary)
    EP->>GH: submit_batch_review(PullRequestReview)
```

---

## 🚀 Quick Start: Adding to Your Repository

Create `.github/workflows/pr-review.yml` in your target repository:

```yaml
name: "AI Code Review"

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write
  issues: write

jobs:
  review:
    name: "Groq Agent PR Review"
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Run AI PR Review Bot
        uses: your-org/pr-review-bot@main
        with:
          groq_api_key: ${{ secrets.GROQ_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          model: "llama-3.1-8b-instant"
          max_tool_calls: "15"
          enable_inline_comments: "true"
          log_level: "INFO"
```

### Action Inputs Configuration

| Input | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `groq_api_key` | **Yes** | — | API key from [Groq Cloud Console](https://console.groq.com) |
| `github_token` | **Yes** | `${{ github.token }}` | GitHub token for posting review comments |
| `model` | No | `llama-3.1-8b-instant` | Groq model ID for agent reasoning |
| `max_tool_calls`| No | `15` | Safety cap on tool execution loop depth |
| `enable_inline_comments` | No | `true` | Post line-level comments on the diff |
| `log_level` | No | `INFO` | Verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## 💻 Local Development & Offline Simulation

The bot includes an offline simulation mode in `sample_run.py` to test diffs and demo the agent locally without requiring GitHub Actions:

```bash
# 1. Clone repository and install dependencies
git clone https://github.com/your-org/pr-review-bot.git
cd pr-review-bot
uv venv .venv
source .venv/bin/activate  # Or `.venv\Scripts\activate` on Windows
uv pip install -r requirements-dev.txt

# 2. Run offline mock demo (No API key needed)
python sample_run.py --diff tests/fixtures/buggy_pr_diff.diff --mock

# 3. List accessible Groq models for your API key
python sample_run.py --api-key YOUR_GROQ_API_KEY --list-models

# 4. Interactively choose from available models
python sample_run.py --diff tests/fixtures/buggy_pr_diff.diff --api-key YOUR_GROQ_API_KEY --select-model

# 5. Run live review with a specific model
python sample_run.py --diff tests/fixtures/buggy_pr_diff.diff --api-key YOUR_GROQ_API_KEY --model llama-3.1-8b-instant
```

---

## 🧪 Testing & Quality Assurance

The test suite contains 29 comprehensive unit and integration tests covering GitHub API rate limiting, retry backoff, JSON tool schemas, diff parsing, model listing, and agent loop safeguards:

```bash
# Run pytest with branch and statement coverage
pytest -v --cov=src --cov-report=term-missing tests/

# Run Ruff linter
ruff check .
```

### Key Test Scenarios:
- **Clean PR Scenario**: Verifies the agent detects low risk, posts 0 unnecessary inline comments, and outputs a clean markdown review.
- **Buggy PR Scenario**: Verifies detection of SQL injection, hardcoded API keys, off-by-one index errors, and posts line-anchored comments with `HIGH` risk assessment.
- **Max Tool Call Safeguard**: Enforces loop termination when reaching step ceiling, flushing partial review comments gracefully.
- **GitHub API Rate Limit Backoff**: Tests exponential backoff and retry when receiving HTTP 403 / 429 status codes.
- **Dynamic Model Discovery & Validation**: Tests SDK model listing and error handling on unavailable models.

---

## ⚠️ Known Limitations & Future Roadmap

- **Static Analysis vs. Runtime Execution**: The agent reviews code via static diff and codebase inspection; it does not execute test suites or build binaries dynamically in an isolated sandbox.
- **Cross-Repository Context**: The bot currently inspects files within the checked-out repository; external microservice dependencies are not traversed.
- **Diff Parsing Boundary**: Very large diffs (>5000 lines) are automatically truncated to fit within model context windows; use `get_file_content` for granular file exploration.
