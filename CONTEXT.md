# Quell — AI Handoff Context

> Read this before touching any code. It covers what Quell is, the full
> version history, current architecture, every module, key design decisions,
> and where the sharp edges are. Written for AI tools and developers
> picking up mid-session.

---

## What Quell Is (Current — v0.5.0)

**Tagline:** "Your docstrings say what your code should do. Quell proves it."

Quell reads specifications that already exist in your codebase — docstrings,
Pydantic models, PySpark schemas, bug reports — extracts every testable
requirement, checks which ones have no test, generates a verified test for
each gap, then writes it to disk. Every test is proven in two phases before
it touches your files.

```
Old tools:  LLM → test → ✓ green (coverage achieved, requirement unknown)
Quell:      spec → requirement → test → PASS original + FAIL violated → write
```

---

## Version History (from git)

### v0.1.0 (commit f077cab)
Initial release. Mutation-testing proof-of-concept.
- `MutmutAdapter` — reads `.mutmut-cache` SQLite, auto-detects v2.x vs v3.x
- `MutationAnalyzer` — classifies operator from AST diff
- `TestGenerator` — rule-based for 9 operators, LLM fallback for UNKNOWN
- `MutantVerifier` — apply mutant → run test → confirm kill → restore (finally)
- `TestWriter` — libcst injection, backup + validate before write
- Basic CLI: `quell scan`, `quell fix`, `quell auto`

### v0.2.0 (commit 3556c86)
CI, Score, Repair, MCP, and SDK.
- `quell ci` — CI/CD threshold enforcement, `--diff-only` PR mode (2-3 min vs 15-30 min), JSON output
- `quell score` — per-file score table, SVG badge generation, history tracking
- `quell repair` — repair AI-generated test suites
- `quell-mcp` — MCP server exposing 4 tools to AI agents (verify_test, get_survivors, generate_killing_test, get_quell_score)
- `quell.sdk.Quell` — clean Python programmatic API
- Files added: `quell/ci/`, `quell/score/`, `quell/repair/`, `quell/mcp_server.py`, `quell/sdk.py`

### v0.3.0 (commit 8c5c8ad)
GitHub integration and VS Code extension.
- `quell github-comment` — post/update Quell score as idempotent PR comment
- GitHub App webhook server (`quell/github/app.py`) for automatic PR comments
- GitHub auth helper (`quell/github/auth.py`)
- PR comment formatter (`quell/github/formatter.py`, `quell/github/pr_commenter.py`)
- VS Code extension (`vscode-quell/`) — inline score annotations, status bar badge
- Published to PyPI

### v0.4.0 (commit a76b215) — ARCHITECTURAL PIVOT
**Full pivot from mutation-testing to spec-first architecture.**
Quell no longer requires mutmut. Instead it reads specifications.

- **Pipeline rewritten**: spec readers → `list[Requirement]` → coverage checker → rule engine → verifier → writer
- `quell/spec/docstring_reader.py` — extracts MUST_RAISE, BOUNDARY, ENUM_VALID, MUST_RETURN from docstrings
- `quell/spec/type_reader.py` — extracts BOUNDARY and ENUM_VALID from Pydantic Field validators and Literal annotations
- `quell/spec/bug_reader.py` — LLM-powered BUG_REPRO requirement generation from plain-English
- `quell/spec/mutation_reader.py` — reads mutmut 3.x / Stryker results (optional bridge)
- `quell/coverage/checker.py` — AST-based test coverage scan, no test execution required
- `quell/synthesis/rule_engine.py` — deterministic test generation per ConstraintKind
- `quell/synthesis/llm_engine.py` — LLM fallback for complex cases only
- `Requirement` unified model: `target_file`, `target_function`, `description`, `constraint_kind`, `source`, `is_covered`
- `ConstraintKind` enum: MUST_RAISE, BOUNDARY, ENUM_VALID, MUST_RETURN, BUG_REPRO
- `SpecSource` enum: DOCSTRING, TYPE_ANNOTATION, BUG_REPORT, MUTATION
- Score now measures requirement coverage (not mutation score)
- Removed: `quell scan`, `quell fix`, `quell auto` (replaced by `quell check`)
- Added: `quell check`, `quell reproduce`, `quell prove`
- Tests: deleted mutation-testing tests, added spec reader + coverage checker + rule engine tests
- `tests/fixtures/sample_project/` — reference project (payments.py) for integration tests

### v0.4.1 (commit f0a9922)
Privacy-safe diagnostic report and real callable test generation.
- `quell/report/generator.py` — writes `.quell/report.json` after every `--fix` run. Records success/failure per requirement, unknown types, failure reasons. No source code, no full paths.
- `quell/synthesis/sig_inspector.py` — AST-based signature inspection, builds real callable tests with correct argument types
- Type stubs: str, int, float, bool, Path (→ tmp_path), Optional[X], List[X], Dict, name-based inference
- `sdk._fix_gaps()` implemented — `quell check --fix` now actually runs rule engine, verifier, writer
- File scanner excludes `.venv` and `site-packages`
- `quell/core/verifier.py` — resolves pytest working directory by walking up to `pyproject.toml` / `setup.py`

### v0.4.2 (commit 246b094)
Targeted violation injection and optional-return detection.
- Violation injector targets ONLY lines inside the named function using AST line ranges (prevents violating a different function with same pattern)
- `must_return` skips generation when return annotation is `Optional[X]` or `X | None` (avoids false failures on cache-miss / empty inputs)

### v0.4.3 (commit defd9a1)
`quell --version` and version sync.
- Added `--version` / `-V` flag to CLI
- Synced `quell.__version__` with `pyproject.toml` (was stale at 0.1.0)

### v0.4.4 (commit 2a5f1c5 + 1cf84e2)
Rule engine improvements — 75% score on real-world projects.
- `must_raise` tests use non-existent paths for Path parameters (triggers FileNotFoundError)
- `sig_inspector.py` — added stubs for `Callable[..., T]`, `logging.LogRecord`, `datetime.datetime`, `datetime.date`, `re.Pattern`
- Violation injector replaces ALL non-None `return` statements (not just the first) — fixes `doesnt_catch_violation` for early-return functions
- `doesnt_catch_violation` count: 6 → 0 on reference project
- `unknown_type_frequency` cleared — no more unknown stubs for common stdlib types
- CI: lint, type check, and all tests green (90 pass, 1 skipped)

### v0.5.0 (commit 13781cb) — CURRENT
OAuth auth, PySpark reader, `quell pr`, `--no-llm`, GitHub Actions installer.

**New modules:**
- `quell/auth/__init__.py`, `quell/auth/oauth.py` — OAuth 2.0 PKCE browser login
  - `login()` — local HTTP server port 7642, 120s timeout, browser auto-open
  - `logout()` — revoke on server + delete `~/.quell/credentials.json`
  - `load_credentials()` — reads file or `QUELL_API_KEY` env var
  - `get_valid_token()` — refreshes if expired (60s buffer)
  - `_save_credentials()` — chmod 600 (stat.S_IRUSR | stat.S_IWUSR)
  - Single-session enforcement server-side (session_id in token)
- `quell/github/pr_runner.py` — `GitHubPRRunner`
  - Auto-detects repo from git remote (HTTPS + SSH)
  - Fetches PR diff from GitHub API, writes changed files to temp dir
  - Runs DocstringReader/TypeReader/PySparkReader on changed files
  - Checks coverage against local project test files
  - Posts/updates Quell report as PR comment (upserts existing)
  - Score emoji: 🟢 ≥80% / 🟡 ≥50% / 🔴 <50%
- `quell/spec/pyspark_reader.py` — `PySparkReader`
  - Pure AST analysis — never imports pyspark at scan time
  - Fast exit: if "StructType" not in source text, return []
  - Parses `StructField(name, Type(), nullable=bool)` from assignments and return statements
  - Generates NOT_NULL when nullable=False, TYPE_CHECK for all columns
- `quell/synthesis/pyspark_rule_engine.py` — `PySparkRuleEngine`
  - `_not_null()` → `pytest.raises((AnalysisException, Exception)): spark.createDataFrame([Row(col=None)])`
  - `_type_check()` → `assertSchemaEqual` with expected StructType
  - `ensure_conftest()` — auto-creates conftest.py with session-scoped SparkSession fixture

**New ConstraintKinds:** `NOT_NULL`, `TYPE_CHECK`
**New SpecSource:** `PYSPARK`
**New QuellConfig field:** `enable_pyspark: bool = False`

**New CLI commands:**
- `quell pr <N>` — fetch PR diff, check coverage, post comment
- `quell auth login` / `quell auth logout` / `quell auth status`
- `quell install --hook` — writes pre-commit config to user's project
- `quell install --pr` — writes `.github/workflows/quell-pr.yml`

**New CLI flags on `quell check`:**
- `--no-llm` — disables all LLM calls, rule-based only, prints "Your code never left your machine."
- `--format json` — machine-readable output

**Zero-config default:** `_load_config()` returns `llm_provider="none"` when no pyproject.toml

**New test files:** `tests/unit/test_auth.py`, `tests/unit/test_pr_runner.py`, `tests/unit/test_pyspark_reader.py`

**CI additions:**
- `.github/workflows/ci.yml` — added `quell-self-check` job (runs quell on itself) + `release-check` job
- `.github/workflows/quell-pr.yml` — posts Quell report on every PR touching `quell/**/*.py`

**Optional extra:** `pip install quelltest[pyspark]` — only needed to run generated PySpark tests

---

## Current Repository Layout (v0.5.0)

```
quell/
├── __init__.py              ← __version__ = "0.5.0", public exports
├── cli.py                   ← all Typer CLI commands
├── sdk.py                   ← Quell class, CheckResult, programmatic API
├── mcp_server.py            ← MCP server for AI agents
├── auth/
│   ├── __init__.py
│   └── oauth.py             ← OAuth 2.0 PKCE, credentials, token refresh
├── core/
│   ├── models.py            ← ALL Pydantic models (source of truth)
│   ├── verifier.py          ← THE MOAT: PASS original + FAIL violated (finally restores)
│   └── writer.py            ← libcst injection, backup + validate before write
├── coverage/
│   └── checker.py           ← AST-based coverage scan, no test execution
├── spec/
│   ├── base.py              ← SpecReader protocol
│   ├── docstring_reader.py  ← MUST_RAISE, BOUNDARY, ENUM_VALID, MUST_RETURN
│   ├── type_reader.py       ← Pydantic Field validators, Literal annotations
│   ├── bug_reader.py        ← LLM-powered BUG_REPRO from plain-English
│   ├── mutation_reader.py   ← mutmut 3.x / Stryker bridge (optional)
│   └── pyspark_reader.py    ← StructType → NOT_NULL + TYPE_CHECK (AST-only)
├── synthesis/
│   ├── rule_engine.py       ← deterministic test generation per ConstraintKind
│   ├── llm_engine.py        ← LLM fallback for complex cases
│   ├── sig_inspector.py     ← AST signature inspection → real callable tests
│   └── pyspark_rule_engine.py ← NOT_NULL / TYPE_CHECK test generation
├── github/
│   ├── __init__.py          ← lazy __getattr__ imports (avoids circular import)
│   ├── app.py               ← GitHub App webhook server
│   ├── auth.py              ← GitHub App JWT auth
│   ├── formatter.py         ← PR comment markdown formatter
│   ├── pr_commenter.py      ← post/update PR comment
│   └── pr_runner.py         ← GitHubPRRunner: fetch diff, run Quell, post comment
├── ci/
│   ├── diff_parser.py       ← git diff → changed line ranges
│   ├── runner.py            ← runs mutmut programmatically
│   ├── threshold.py         ← score threshold + exit code
│   └── reporter.py          ← console / JSON / GitHub Actions output
├── report/
│   └── generator.py         ← writes .quell/report.json (privacy-safe diagnostic)
├── score/
│   ├── calculator.py        ← FileScore / ProjectScore
│   ├── badge.py             ← SVG badge generation
│   └── tracker.py           ← .quell/history.json snapshots
├── repair/
│   └── engine.py            ← RepairEngine
├── llm/
│   ├── client.py            ← LLMClient abstract + factory
│   ├── prompts.py           ← test generation prompt builder
│   └── providers/
│       ├── anthropic_provider.py
│       ├── openai_provider.py
│       └── ollama_provider.py
├── adapters/
│   ├── base.py              ← MutationAdapter protocol
│   ├── mutmut_adapter.py    ← mutmut 3.x (SQLite) + 2.x (CLI)
│   └── stryker_adapter.py   ← Stryker JSON parser
└── ui/
    ├── console.py
    ├── progress.py
    └── diff.py
```

---

## The Pipeline (v0.5.0)

```
INPUT READERS (spec/*)
  docstring_reader.py   → Raises:/Returns: blocks in docstrings
  type_reader.py        → Pydantic Field validators, Literal annotations
  bug_reader.py         → plain-English bug descriptions (LLM)
  pyspark_reader.py     → StructType schemas (AST-only, no pyspark import)
  mutation_reader.py    → mutmut / Stryker results (optional)
         │
         ▼
  list[Requirement]     ← unified model for everything
         │
         ▼
COVERAGE CHECKER (coverage/checker.py)
  AST-scans test files
  marks each Requirement: covered / uncovered
  uncertain → uncovered (prefer duplicate over missed gap)
         │
         ▼
TEST SYNTHESIZER (synthesis/)
  rule_engine.py        → fast deterministic rules per ConstraintKind
  pyspark_rule_engine.py → NOT_NULL / TYPE_CHECK (PySpark-specific)
  llm_engine.py         → LLM fallback for complex cases only
         │
         ▼
VERIFICATION ENGINE (core/verifier.py)  ← THE MOAT
  1. Run test on original code    → MUST PASS
  2. Inject violation into source → breaks the requirement
  3. Run test on violated code    → MUST FAIL
  4. ALWAYS restore in finally    → no side effects
  Only VERIFIED tests continue
         │
         ▼
WRITER (core/writer.py)
  libcst injection — never string concatenation
  backup before write, validate CST, restore on failure
  append audit log entry
```

---

## Key Models (`quell/core/models.py`)

| Model | Purpose |
|-------|---------|
| `Requirement` | One testable requirement. Has `constraint_kind`, `source`, `target_function`, `target_file`, `description`, `is_covered`, `violation_input` |
| `ConstraintKind` | MUST_RAISE, BOUNDARY, ENUM_VALID, MUST_RETURN, BUG_REPRO, NOT_NULL, TYPE_CHECK |
| `SpecSource` | DOCSTRING, TYPE_ANNOTATION, BUG_REPORT, MUTATION, PYSPARK |
| `GeneratedTest` | Candidate test. Has `test_code`, `test_file_path`, `generated_by` |
| `VerificationResult` | Outcome. Status: verified / fails_on_original / doesnt_catch_violation / syntax_error / timeout |
| `QuellConfig` | Loaded from `[tool.quell]` in pyproject.toml. Defaults: `llm_provider="none"`, `enable_pyspark=False` |
| `FileScore` / `ProjectScore` | Coverage metrics |

---

## CLI Commands (v0.5.0)

```bash
quell check <path>                   # scan specs, show gaps
quell check <path> --fix             # generate + verify + write tests
quell check <path> --no-llm          # rule-based only, no network
quell check <path> --format json     # machine-readable output

quell pr <N>                         # check PR #N diff, show gaps
quell pr <N> --comment               # post result as PR comment
quell pr <N> --repo owner/repo       # explicit repo (auto-detects from git remote)

quell auth login                     # OAuth PKCE browser login
quell auth logout                    # revoke token + delete credentials
quell auth status                    # show current session info

quell install --hook                 # write pre-commit config
quell install --pr                   # write .github/workflows/quell-pr.yml

quell reproduce "bug description"    # LLM-powered BUG_REPRO test
quell prove <file>                   # show requirement coverage for file

quell score                          # per-file score table
quell score --badge                  # write .quell/badge.svg
quell ci src/ --threshold 0.75       # CI threshold gate
quell --version                      # show version
```

---

## Key Invariants — NEVER Violate

1. `verifier.py`: ALWAYS restore source files in a `finally` block
2. `writer.py`: ALWAYS backup before writing, ALWAYS restore on failure
3. `writer.py`: ALWAYS validate CST parses correctly before writing to disk
4. NO code transmitted to any server except the configured LLM provider
5. LLM called ONLY when rule engine is insufficient
6. Every spec reader returns `[]` on any error — never raises
7. pytest always in subprocess — never in-process (module cache)
8. Coverage checker marks uncertain as uncovered (prefer duplicate over missed gap)
9. `quell/github/__init__.py` uses lazy `__getattr__` — NOT eager imports (avoids circular import via `quell.ci.runner`)

---

## Configuration

```toml
[tool.quell]
llm_provider = "none"              # "none" | "anthropic" | "openai" | "ollama"
llm_model = "claude-sonnet-4-6"
max_verification_attempts = 3
verification_timeout_seconds = 30
auto_write = false
enable_docstring = true
enable_types = true
enable_pyspark = false             # requires pip install quelltest[pyspark] to run generated tests
enable_mutations = false
score_threshold = 0.0
```

Zero-config: if no `pyproject.toml`, `_load_config()` returns `QuellConfig(llm_provider="none")`.

Auth: `QUELL_API_KEY` env var bypasses browser login (for CI).

---

## Known Sharp Edges

- `chmod 600` on credentials is a no-op on Windows (test skipped with `@pytest.mark.skipif(sys.platform == "win32", ...)`)
- PySpark tests require a real SparkSession — conftest.py is auto-created but tests only run if `pyspark` is installed
- `quell/github/__init__.py` MUST use lazy imports — eager imports cause `ImportError: cannot import name 'run_mutmut_full'` via the ci module chain
- Violation injector replaces ALL non-None `return` statements in target function scope — required for early-return paths
- `must_return` skips generation for `Optional[X]` / `X | None` return types (avoids false failures)

---

## Development

```bash
uv sync --dev
uv run pytest tests/ -v            # 90 pass, 1 skipped
uv run ruff check . --fix
uv run mypy quell/
uv run quell --help
uv build                           # dist/quelltest-0.5.0-py3-none-any.whl
```

PyPI: `pip install quelltest` → 0.5.0
PyPI (PySpark extra): `pip install quelltest[pyspark]`
GitHub: https://github.com/shashank7109/quelltest_lib
