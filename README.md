# quelltest

> Your code says what it should do. Quell proves it.

[![PyPI](https://img.shields.io/pypi/v/quelltest)](https://pypi.org/project/quelltest/)
[![Python](https://img.shields.io/pypi/pyversions/quelltest)](https://pypi.org/project/quelltest/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Quell reads your production code directly — every `if/raise`, null check, and guard clause is a requirement written in Python. It finds the ones with no test, generates a failing test that **proves** the gap exists, then optionally suggests a fix and verifies the fix works. No docstrings needed. No Pydantic needed. Just code.

## Why Quell is different

| Tool | Finds logic gaps | Generates failing test | Suggests fix | Verifies fix works |
|------|-----------------|----------------------|--------------|-------------------|
| Snyk | security only | ❌ | ❌ | ❌ |
| Semgrep | security only | ❌ | ❌ | ❌ |
| CodeRabbit | PR review | ❌ | ❌ | ❌ |
| Corgea | logic + auth | ❌ | auto-PR | ❌ |
| **Quell** | guard clauses | ✅ verified | ✅ LLM | ✅ two-phase |

**The exact gap Quell fills:** Nobody generates a failing test that _proves_ a logic gap exists, then suggests a fix, then verifies the test passes after the fix.

## How it works

```
STEP 1 — FIND (no LLM, no network)
  Read production code via AST
  Detect: if/raise, null checks, auth guards, bare excepts, silent failures
  Every guard clause is a requirement

STEP 2 — PROVE (no LLM for rule-based gaps)
  Generate a failing test that proves the gap exists
  Test MUST fail on current code (proves vulnerability is real)

STEP 3 — FIX (LLM, only on request with --suggest)
  LLM suggests a code change
  Shows diff — never auto-applies
  Test MUST pass after applying the fix
  If test still fails: fix is rejected
```

## Installation

```bash
pip install quelltest
```

Requires Python 3.11+. The CLI command is `quell`.

## Commands — which use the LLM and which don't

### `quell scan` — primary command (v0.6.0+)

Reads `if/raise` patterns directly. Works on any Python file.

```bash
# Find all untested guard clauses — NO LLM, no network
quell scan src/

# Generate failing tests for each gap — NO LLM for rule-based gaps
quell scan src/ --fix

# Also suggest code fixes via LLM — USES LLM (requires API key or quell auth login)
quell scan src/ --fix --suggest

# Force rule-based only, zero network calls
quell scan src/ --no-llm
```

**When does `quell scan` use the LLM?**
- `--fix` alone: **no LLM** — rule engine handles boundary, null, enum, type, auth checks
- `--fix --suggest`: **LLM used** for generating fix suggestions only
- `--no-llm`: **never** — skips any LLM step

### `quell check` — for docstring/type annotation users

For codebases that have structured docstrings or Pydantic models.

```bash
# Find requirement gaps from docstrings + types — NO LLM
quell check src/

# Generate verified tests — NO LLM (rule engine handles standard constraints)
quell check src/ --fix

# Force rule-based only
quell check src/ --no-llm

# For code without docstrings/types, use scan instead:
quell scan src/
```

### Other commands

```bash
# Reproduce a bug from a plain English description — USES LLM
quell reproduce "payment accepts zero amount"

# Show coverage score for a file — NO LLM
quell prove src/payments.py

# Project-wide Quell Score with SVG badge — NO LLM
quell score --badge

# CI mode: fail if score below threshold — NO LLM
quell ci src/ --threshold 0.8

# Analyze a GitHub PR — NO LLM
quell pr 42
quell pr 42 --comment   # post result as PR comment

# Auth (only needed for LLM features)
quell auth login
quell auth status
```

## Quick start — production code (no docstrings needed)

Given any Python file with guard clauses:

```python
def process_payment(amount, currency, user):
    if amount <= 0:
        raise ValueError("Amount must be positive")
    if user is None:
        raise ValueError("User required")
    if currency not in ["USD", "EUR", "GBP"]:
        raise ValueError("Invalid currency")
    return charge(amount)
```

```bash
# Find untested guards — no API key needed
quell scan payments.py
```

Output:
```
Quell Scan — reading guard clauses in 1 file(s)
No docstrings needed. Reading your if/raise patterns.

Logic Gaps Found (3 untested / 3 total)
┌────────────┬─────────────────┬──────────────────────────┬──────────┬──────────────────────┐
│ File       │ Function        │ Guard Clause             │ Type     │ Method               │
├────────────┼─────────────────┼──────────────────────────┼──────────┼──────────────────────┤
│ pay.py     │ process_payment │ if amount <= 0:          │ boundary │ [rule-based, no net] │
│ pay.py     │ process_payment │ if user is None:         │ not_null │ [rule-based, no net] │
│ pay.py     │ process_payment │ if currency not in [...] │ enum     │ [rule-based, no net] │
└────────────┴─────────────────┴──────────────────────────┴──────────┘

Run: quell scan payments.py --fix   → generate failing tests
```

```bash
# Generate + verify failing tests — still no API key needed
quell scan payments.py --fix
```

## Quick start — docstrings and Pydantic (legacy check command)

```python
class PaymentRequest(BaseModel):
    amount: float = Field(gt=0)
    currency: Literal["USD", "EUR", "GBP"]
```

```bash
quell check src/payments.py        # find gaps
quell check src/payments.py --fix  # generate verified tests
```

## Configuration

```bash
quell init   # adds [tool.quell] to pyproject.toml
```

```toml
[tool.quell]
llm_provider = "anthropic"          # "anthropic" | "openai" | "ollama" | "none"
llm_model    = "claude-sonnet-4-5"
enable_docstring = true
enable_types     = true
enable_mutations = false
auto_write       = false
```

**LLM API key** — only needed for `--suggest` and `quell reproduce`:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# or
export OPENAI_API_KEY=sk-...
# or login via browser (stores token locally)
quell auth login
```

For fully offline use, use Ollama or `--no-llm`:

```bash
# pyproject.toml: llm_provider = "ollama"
# ollama pull codellama
quell scan src/ --no-llm   # zero network, always works
```

## What each constraint type means

| Type | Detected from | Example |
|------|--------------|---------|
| `boundary` | `if x <= 0: raise` / `assert x > 0` | amount must be positive |
| `not_null` | `if x is None: raise` | user must not be None |
| `enum_valid` | `if x not in [...]: raise` | currency must be USD/EUR/GBP |
| `type_check` | `if not isinstance(x, T): raise` | amount must be numeric |
| `auth_check` | `if not user.is_authenticated: raise` | login required |
| `bare_except` | `except:` | catches all errors silently |
| `silent_fail` | `if not x: return None` | fails silently instead of raising |
| `magic_value` | `if status == "admin":` | hardcoded string in condition |

## Python SDK

```python
from quell import Quell

q = Quell()

# Find requirement gaps (no LLM)
result = q.check("src/")
print(f"Score: {result.score:.0%} | Gaps: {len(result.uncovered)}")

# Reproduce a bug (uses LLM)
q.reproduce("payment accepts zero amount silently")

# Project score (no LLM)
score = q.score()
print(f"Project: {score.percentage}%")
```

## Project structure

```
quell/
├── cli.py              # Typer CLI: scan, check, reproduce, prove, score, ci, pr, auth
├── sdk.py              # Python API: Quell class
├── spec/
│   ├── code_guard_reader.py   # PRIMARY: reads if/raise patterns (v0.6.0+)
│   ├── docstring_reader.py    # reads docstring Raises:/Returns: blocks
│   ├── type_reader.py         # reads Pydantic Field constraints
│   └── bug_reader.py          # reads natural language bug descriptions
├── fix/
│   └── suggester.py    # LLM fix suggester (only after test proves gap)
├── core/
│   ├── models.py       # Requirement, ConstraintKind, VerificationResult
│   ├── verifier.py     # THE MOAT — proves every test catches violations
│   └── writer.py       # libcst injection, backup/restore
├── coverage/           # AST-based coverage checker
├── synthesis/          # rule_engine.py + llm_engine.py
├── score/              # Quell Score calculator + SVG badge
└── llm/                # Anthropic / OpenAI / Ollama providers
```

## Development

```bash
git clone https://github.com/shashank7109/quelltest_lib.git
cd quelltest_lib
uv sync --dev

uv run pytest tests/ -v
uv run ruff check . --fix
uv run mypy quell/

# Test quell scan on itself (no LLM)
uv run quell scan quell/ --no-llm
```

## Related

- [Docs](https://quell.buildsbyshashank.tech/docs)
- [quell_frontend](https://github.com/shashank7109/quell_frontend) — Next.js website

## License

MIT — see [LICENSE](LICENSE)
