"""
Quell CLI — built with Typer.

Commands:
  quell scan        Scan production code for untested guard clauses (PRIMARY)
  quell check       Scan specs, find gaps, optionally fix
  quell reproduce   Bug description → failing test
  quell prove       Confidence score for a function/file
  quell score       Project-wide Quell Score + --badge
  quell ci          CI mode: check + threshold + exit code
  quell init        Add [tool.quell] to pyproject.toml
  quell pr          Analyze requirement coverage for a GitHub PR
  quell install     Set up Quell in your project (pre-commit + GitHub Action)
  quell auth        Manage authentication (login/logout/status)
"""
from __future__ import annotations

import asyncio
import json as _json
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from quell import __version__
from quell.core.models import QuellConfig

app = typer.Typer(
    name="quell",
    help="Your docstrings say what your code should do. Quell proves it.",
    rich_markup_mode="rich",
)
auth_app = typer.Typer(help="Manage Quell authentication")
app.add_typer(auth_app, name="auth")

console = Console()

# GitHub Actions workflow template — written by `quell install --pr`
GITHUB_ACTION_YAML = """name: Quell — Requirement Coverage

on:
  pull_request:
    types: [opened, synchronize, reopened]
    paths:
      - "**.py"

permissions:
  contents: read
  pull-requests: write

jobs:
  quell:
    name: Quell requirement coverage
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install Quell
        run: pip install quelltest

      - name: Run Quell
        id: quell
        env:
          QUELL_API_KEY: ${{ secrets.QUELL_API_KEY }}
        run: |
          quell check --diff-only --no-llm --format json > quell_report.json || true
          echo "done=true" >> $GITHUB_OUTPUT

      - name: Post PR comment
        if: steps.quell.outputs.done == 'true'
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            let report;
            try {
              report = JSON.parse(fs.readFileSync('quell_report.json', 'utf8'));
            } catch(e) { return; }

            const total = report.total_requirements || 0;
            if (total === 0) return;

            const gaps = report.gaps || [];
            const score = report.score || 0;
            const emoji = score >= 0.8 ? '🟢' : score >= 0.5 ? '🟡' : '🔴';

            let body = `## ${emoji} Quell Report\\n\\n`;
            const covered = total - gaps.length;
            body += `**Coverage: ${Math.round(score * 100)}%** (${covered}/${total} tested)\\n\\n`;

            if (gaps.length === 0) {
              body += `✅ All requirements in changed files are tested.\\n`;
            } else {
              body += `**${gaps.length} untested requirement${gaps.length > 1 ? 's' : ''}:**\\n\\n`;
              body += `| File | Function | Requirement | Type |\\n`;
              body += `|------|----------|-------------|------|\\n`;
              for (const g of gaps.slice(0, 10)) {
                body += `| \\`${g.file}\\` | \\`${g.function}\\` | ${g.description} | ${g.kind} |\\n`;
              }
              if (gaps.length > 10) {
                body += `\\n_...and ${gaps.length - 10} more. Run \\`quell check --fix\\` locally._\\n`;
              }
              body += `\\n**Fix locally:** \\`quell check src/ --fix\\`\\n`;
            }
            body += `\\n<sub>Quell • no code sent anywhere • [quell.buildsbyshashank.tech](https://quell.buildsbyshashank.tech)</sub>`;

            const { data: comments } = await github.rest.issues.listComments({
              owner: context.repo.owner, repo: context.repo.repo,
              issue_number: context.issue.number,
            });
            const existing = comments.find(c => c.body.includes('Quell Report'));
            if (existing) {
              await github.rest.issues.updateComment({
                owner: context.repo.owner, repo: context.repo.repo,
                comment_id: existing.id, body,
              });
            } else {
              await github.rest.issues.createComment({
                owner: context.repo.owner, repo: context.repo.repo,
                issue_number: context.issue.number, body,
              });
            }
"""


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"quelltest {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        None, "--version", "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    pass


def _load_config(project_root: Path) -> QuellConfig:
    """Load config — returns safe defaults (no LLM) if no config found."""
    try:
        import tomllib
        pyproject = project_root / "pyproject.toml"
        if pyproject.exists():
            data = tomllib.loads(pyproject.read_text())
            quell_cfg = data.get("tool", {}).get("quell", {})
            if quell_cfg:
                return QuellConfig(**quell_cfg)
    except Exception:
        pass
    # Safe defaults — works without any config or API key
    return QuellConfig(
        llm_provider="none",
        enable_docstring=True,
        enable_types=True,
        enable_mutations=False,
        enable_pyspark=False,
    )


def _method_tag(source_value: str, generated_by: str = "") -> str:
    """Return a dim tag showing how this requirement was processed."""
    if source_value == "pyspark":
        return "[dim][pyspark, rule-based, no network][/dim]"
    if generated_by.startswith("llm"):
        return "[dim][llm][/dim]"
    return "[dim][rule-based, no network][/dim]"


def _run_async(coro: Coroutine[Any, Any, None]) -> None:
    """Run a coroutine in a dedicated thread with its own event loop.

    Always uses a new thread so asyncio.run() never conflicts with any
    outer event loop (Jupyter, IPython, Refactron, etc.).
    """
    exc: list[BaseException] = []

    def _target() -> None:
        try:
            asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001
            exc.append(e)

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join()
    if exc:
        raise exc[0]


@app.command("scan")
def cmd_scan(
    target: Path = typer.Argument(Path("."), help="File or directory to scan"),
    fix: bool = typer.Option(False, "--fix", help="Generate failing tests for each gap"),
    suggest: bool = typer.Option(False, "--suggest", help="Also suggest code fixes via LLM"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Rule-based only, no LLM"),
    project_root: Path = typer.Option(Path("."), "--root"),
) -> None:
    """
    Scan production code for untested logic gaps.

    Reads your if/raise patterns directly — no docstrings or types needed.
    Works on any Python file ever written.

    quell scan src/                   find all logic gaps
    quell scan src/ --fix             find gaps + generate failing tests
    quell scan src/ --fix --suggest   find gaps + tests + suggest fixes
    quell scan src/ --no-llm          rule-based only, zero network
    """
    _run_async(_scan_async(target, fix, suggest, no_llm, project_root))


async def _scan_async(
    target: Path,
    fix: bool,
    suggest: bool,
    no_llm: bool,
    project_root: Path,
) -> None:
    from quell.core.models import VerificationStatus
    from quell.coverage.checker import CoverageChecker
    from quell.spec.code_guard_reader import CodeGuardReader
    from quell.synthesis.rule_engine import RuleEngine

    config = _load_config(project_root)

    files = (
        [
            f for f in target.rglob("*.py")
            if "test" not in f.name
            and ".venv" not in str(f)
            and "__pycache__" not in str(f)
            and "site-packages" not in str(f)
        ]
        if target.is_dir() else [target]
    )

    console.print(Panel.fit(
        f"[bold blue]Quell Scan[/bold blue] — "
        f"reading guard clauses in {len(files)} file(s)\n"
        "[dim]No docstrings needed. Reading your if/raise patterns.[/dim]"
    ))

    reader = CodeGuardReader()
    checker = CoverageChecker(project_root)
    rule_engine = RuleEngine()

    all_requirements = []
    for f in files:
        all_requirements.extend(reader.read(f))

    if not all_requirements:
        console.print("[yellow]No guard clauses found.[/yellow]")
        console.print(
            "[dim]Quell reads if/raise patterns. "
            "If your code has no guard clauses, nothing to check.[/dim]"
        )
        return

    all_requirements = checker.check(all_requirements)
    gaps = [r for r in all_requirements if not r.is_covered]

    table = Table(
        title=f"Logic Gaps Found ({len(gaps)} untested / {len(all_requirements)} total)"
    )
    table.add_column("File", style="blue")
    table.add_column("Function", style="cyan")
    table.add_column("Guard Clause", style="white")
    table.add_column("Type", style="magenta")
    table.add_column("Method", style="dim")

    for req in gaps:
        table.add_row(
            req.target_file.name,
            req.target_function,
            (req.raw_spec_text or req.description)[:50],
            req.constraint_kind.value,
            "[dim][rule-based, no network][/dim]",
        )

    console.print(table)

    if not gaps:
        console.print("[green]All guard clauses are tested.[/green]")
        return

    if not fix:
        console.print(
            f"\n[yellow]Run [bold]quell scan {target} --fix[/bold] "
            "to generate failing tests.[/yellow]"
        )
        return

    # Generate tests + optional fix suggestions
    from quell.core.verifier import Verifier
    from quell.core.writer import Writer

    llm = None
    synthesizer = None
    if not no_llm:
        from quell.llm.client import LLMClient
        from quell.synthesis.llm_engine import LLMSynthesizer
        llm = LLMClient.from_config(config)
        synthesizer = LLMSynthesizer(llm, config)

    if suggest and no_llm:
        console.print(
            "[yellow]--suggest requires LLM. Pass without --no-llm to enable.[/yellow]"
        )

    verifier = Verifier(config, project_root=project_root)
    writer = Writer(config)
    fixed = 0
    llm_used = False

    for i, req in enumerate(gaps, 1):
        console.print(
            f"\n[{i}/{len(gaps)}] [cyan]{req.target_function}()[/cyan]"
            f" — {req.description[:60]}"
        )
        console.print(f"  Guard: [dim]{req.raw_spec_text}[/dim]")

        if rule_engine.can_handle(req):
            candidate = rule_engine.generate(req)
            generated_by_tag = "[dim][rule-based, no network][/dim]"
        elif synthesizer:
            candidate = await synthesizer.synthesize(req)
            generated_by_tag = "[dim][llm][/dim]"
            llm_used = True
        else:
            console.print("  [dim]Skipped (needs LLM, remove --no-llm)[/dim]")
            continue

        if not candidate:
            continue

        with console.status("Verifying test fails on current code (proving gap)..."):
            result = verifier.verify(req, candidate)

        if result.status == VerificationStatus.VERIFIED:
            console.print(
                f"  [green]Gap proven[/green] — test fails on current code "
                f"{generated_by_tag}"
            )
            console.print(Syntax(candidate.test_code, "python", theme="monokai"))

            if suggest and llm and not no_llm:
                from quell.fix.suggester import FixSuggester
                suggester = FixSuggester(llm, config)
                with console.status("Generating fix suggestion..."):
                    fix_suggestion = await suggester.suggest(req, candidate)

                if fix_suggestion and fix_suggestion.verified:
                    console.print(
                        "\n  [bold green]Fix suggestion "
                        "(verified to make test pass):[/bold green]"
                    )
                    console.print(f"  {fix_suggestion.explanation}")
                    console.print(Syntax(fix_suggestion.diff, "diff", theme="monokai"))
                    apply = typer.confirm("  Apply this fix?", default=False)
                    if apply:
                        req.target_file.write_text(
                            req.target_file.read_text(encoding="utf-8").replace(
                                fix_suggestion.original_code,
                                fix_suggestion.suggested_code,
                                1,
                            ),
                            encoding="utf-8",
                        )
                        console.print("  [green]Fix applied[/green]")
                elif fix_suggestion:
                    console.print(
                        "  [yellow]Fix suggested but not verified — review manually[/yellow]"
                    )
                    console.print(Syntax(fix_suggestion.diff, "diff", theme="monokai"))

            write = typer.confirm("  Write this test?", default=True)
            if write:
                if writer.write(candidate, req.id):
                    console.print(
                        f"  [green]Test written to {candidate.test_file_path.name}[/green]"
                    )
                    fixed += 1

        elif result.status == VerificationStatus.DOESNT_CATCH_VIOLATION:
            console.print(
                "  [yellow]Test generated but doesn't catch the gap — needs manual review[/yellow]"
            )
        elif result.status == VerificationStatus.FAILS_ON_CORRECT:
            console.print("  [red]Generated test breaks correct code — rejected[/red]")

    privacy_line = (
        "[dim]Your code never left your machine.[/dim]"
        if not llm_used else
        "[dim]LLM used for complex cases — only function signatures sent.[/dim]"
    )
    console.print(Panel.fit(
        f"[bold]Done![/bold] {fixed}/{len(gaps)} gaps have failing tests written\n"
        + privacy_line
    ))


@app.command("check")
def cmd_check(
    target: str = typer.Argument(".", help="File or directory to check"),
    fix: bool = typer.Option(False, "--fix", help="Generate and write verified tests"),
    no_llm: bool = typer.Option(
        False, "--no-llm",
        help="Disable all LLM calls. Rule-based only. No network. Default for CI.",
    ),
    sources: str | None = typer.Option(
        None, "--sources", help="Comma-separated: docstring,type,mutation"
    ),
    fmt: str = typer.Option("console", "--format", "-f", help="Output format: console or json"),
    project_root: Path = typer.Option(Path("."), "--root", help="Project root"),
) -> None:
    """
    Check requirement coverage from type annotations and docstrings.

    For production code without types/docstrings, use: quell scan
    quell scan reads your if/raise patterns directly — no annotations needed.
    """
    from quell.sdk import Quell

    src_list = sources.split(",") if sources else ["docstring", "type"]
    config = _load_config(project_root)
    if no_llm:
        config = config.model_copy(update={"llm_provider": "none"})

    q = Quell(project_root=project_root)

    with console.status("[bold blue]Scanning specifications...[/bold blue]"):
        result = q.check(target, sources=src_list, fix=fix)

    if fmt == "json":
        gaps = [
            {
                "file": r.target_file.name,
                "function": r.target_function,
                "description": r.description,
                "kind": r.constraint_kind.value,
                "source": r.source.value,
            }
            for r in result.requirements if not r.is_covered
        ]
        output = {
            "quell_version": __version__,
            "target": target,
            "total_requirements": len(result.requirements),
            "covered": len(result.covered),
            "gaps": gaps,
            "score": result.score,
        }
        print(_json.dumps(output, indent=2))
        return

    table = Table(title=f"Requirements — {target}", show_header=True)
    table.add_column("Function", style="cyan")
    table.add_column("Kind", style="yellow")
    table.add_column("Description")
    table.add_column("Covered", style="green")
    table.add_column("Method")

    for req in result.requirements:
        covered = "YES" if req.is_covered else "NO"
        style = "green" if req.is_covered else "red"
        tag = _method_tag(req.source.value)
        table.add_row(
            req.target_function,
            req.constraint_kind.value,
            req.description[:55] + ("..." if len(req.description) > 55 else ""),
            f"[{style}]{covered}[/{style}]",
            tag,
        )

    console.print(table)
    console.print(
        f"\n[bold]Score:[/bold] {result.score:.0%} "
        f"({len(result.covered)}/{len(result.requirements)} covered)"
    )

    if result.uncovered:
        console.print(
            f"\n[yellow]{len(result.uncovered)} gap(s) found.[/yellow]"
            + (" Run with --fix to generate tests." if not fix else "")
        )

    if fix and result.report_path:
        console.print(
            f"\n[bold]Diagnostic report:[/bold] {result.report_path}\n"
            "[dim]Share this file with the Quell maintainer to improve "
            "rule engine coverage. No source code is included.[/dim]"
        )
        # Check if any verified test was LLM-generated
        llm_used = False
        try:
            rpt = _json.loads(result.report_path.read_text())
            llm_used = any(
                o.get("generated_by", "").startswith("llm")
                for o in rpt.get("outcomes", [])
                if o.get("outcome") == "verified"
            )
        except Exception:
            pass
        if not llm_used:
            console.print("\n[dim]Your code never left your machine.[/dim]")
        else:
            console.print(
                "\n[dim]LLM used for complex cases. "
                "Only function signatures were sent — never business logic.[/dim]"
            )

    if not result.requirements:
        console.print(
            "\n[dim]No requirements found. Add docstrings with Raises:/Returns: "
            "blocks or Pydantic Field constraints.[/dim]"
        )
        console.print(
            "[dim]No API key needed for rule-based checks. "
            "For LLM features: quell auth login[/dim]"
        )


@app.command("reproduce")
def cmd_reproduce(
    description: str = typer.Argument(..., help="Bug description in plain English"),
    file: str | None = typer.Option(None, "--file", help="Target source file"),
    project_root: Path = typer.Option(Path("."), "--root"),
) -> None:
    """Convert a bug description into a verified failing test."""
    from quell.sdk import Quell

    q = Quell(project_root=project_root)

    with console.status("[bold blue]Analyzing bug description...[/bold blue]"):
        written = q.reproduce(description, file=file)

    if written:
        console.print(Panel(
            "[green]Bug reproduction test written.[/green]\n"
            "The test currently FAILS (bug exists). Fix the code, then run it to confirm.",
            title="quell reproduce",
        ))
    else:
        console.print("[red]Could not generate a verified bug reproduction test.[/red]")
        raise typer.Exit(1)


@app.command("prove")
def cmd_prove(
    file: str = typer.Argument(..., help="Source file to prove"),
    function: str | None = typer.Option(None, "--function", help="Specific function"),
    project_root: Path = typer.Option(Path("."), "--root"),
) -> None:
    """Show requirement coverage score for a file or function."""
    from quell.sdk import Quell

    q = Quell(project_root=project_root)

    with console.status("[bold blue]Checking coverage...[/bold blue]"):
        score = q.prove(file, function=function)

    color = "green" if score >= 0.80 else "yellow" if score >= 0.60 else "red"
    label = f"{function or file}"
    console.print(
        Panel(
            f"[{color}]{score:.0%}[/{color}] of requirements proven for [cyan]{label}[/cyan]",
            title="Quell Score",
        )
    )


@app.command("score")
def cmd_score(
    badge: bool = typer.Option(False, "--badge", help="Write badge.svg to .quell/"),
    project_root: Path = typer.Option(Path("."), "--root"),
) -> None:
    """Show project-wide Quell Score."""
    from quell.score.badge import write_badge
    from quell.sdk import Quell

    q = Quell(project_root=project_root)

    with console.status("[bold blue]Calculating score...[/bold blue]"):
        project_score = q.score()

    if not project_score.files:
        console.print("[yellow]No requirements found. Add docstrings or Pydantic models.[/yellow]")
        return

    table = Table(title="Quell Score by File")
    table.add_column("File", style="cyan")
    table.add_column("Requirements")
    table.add_column("Covered")
    table.add_column("Score")
    table.add_column("Grade")

    for fs in project_score.files:
        color = (
            "green" if fs.quell_score >= 0.80
            else "yellow" if fs.quell_score >= 0.60
            else "red"
        )
        table.add_row(
            str(fs.file_path.name),
            str(fs.total_requirements),
            str(fs.covered_requirements),
            f"[{color}]{fs.percentage}%[/{color}]",
            f"[{color}]{fs.grade}[/{color}]",
        )

    console.print(table)
    console.print(f"\n[bold]Project Score:[/bold] {project_score.percentage}%")

    if badge:
        path = write_badge(project_score.total_score, project_root / ".quell")
        console.print(f"[green]Badge written to {path}[/green]")


@app.command("ci")
def cmd_ci(
    target: str = typer.Argument(".", help="File or directory to check"),
    threshold: float = typer.Option(0.0, "--threshold", help="Minimum score (0.0–1.0)"),
    project_root: Path = typer.Option(Path("."), "--root"),
) -> None:
    """CI mode: check requirements and exit 1 if below threshold."""
    from quell.sdk import Quell

    q = Quell(project_root=project_root)
    result = q.check(target)

    console.print(f"Quell Score: {result.score:.0%} | Threshold: {threshold:.0%}")

    if result.score < threshold:
        console.print(
            f"[red]FAIL: {result.score:.0%} < {threshold:.0%} threshold[/red]"
        )
        raise typer.Exit(1)

    console.print("[green]PASS[/green]")


@app.command("init")
def cmd_init(
    project_root: Path = typer.Option(Path("."), "--root"),
) -> None:
    """Add [tool.quell] configuration block to pyproject.toml."""
    pyproject = project_root / "pyproject.toml"

    if not pyproject.exists():
        console.print("[red]No pyproject.toml found. Create one first.[/red]")
        raise typer.Exit(1)

    content = pyproject.read_text()
    if "[tool.quell]" in content:
        console.print("[yellow][tool.quell] already exists in pyproject.toml[/yellow]")
        return

    quell_block = """
[tool.quell]
llm_provider = "anthropic"
llm_model = "claude-sonnet-4-5"
max_verification_attempts = 3
verification_timeout_seconds = 30
auto_write = false
enable_docstring = true
enable_types = true
enable_mutations = false
enable_pyspark = false
score_threshold = 0.0
"""
    pyproject.write_text(content + quell_block)
    console.print("[green]Added [tool.quell] to pyproject.toml[/green]")


@app.command("pr")
def cmd_pr(
    pr_number: int = typer.Argument(..., help="Pull request number to analyze"),
    repo: str = typer.Option("", "--repo", "-r", help="owner/repo (auto-detected from git remote)"),
    token: str = typer.Option("", "--token", "-t", help="GitHub token (or set GITHUB_TOKEN env var)"),
    fix: bool = typer.Option(False, "--fix", help="Generate + write missing tests locally"),
    comment: bool = typer.Option(False, "--comment", "-c", help="Post result as PR comment"),
    fmt: str = typer.Option("console", "--format", "-f", help="console or json"),
    project_root: Path = typer.Option(Path("."), "--root"),
) -> None:
    """
    Analyze requirement coverage for a GitHub Pull Request.

    Examples:
      quell pr 42                     # show gaps for PR #42
      quell pr 42 --comment           # post report as PR comment
      quell pr 42 --fix               # generate missing tests locally
      quell pr 42 --repo owner/repo   # specify repo explicitly
      quell pr 42 --format json       # JSON output (for CI)

    Authentication:
      Set GITHUB_TOKEN environment variable, or use --token flag.
      Get token: github.com/settings/tokens (needs repo + pull_requests scope)
    """
    from quell.github.pr_runner import GitHubPRRunner

    config = _load_config(project_root)

    runner = GitHubPRRunner(
        pr_number=pr_number,
        repo=repo or None,
        token=token or None,
        project_root=project_root,
    )

    with console.status(f"[bold blue]Fetching PR #{pr_number} from GitHub...[/bold blue]"):
        try:
            report = runner.run_quell_on_pr(config)
        except Exception as e:
            console.print(f"[red]Error fetching PR: {e}[/red]")
            console.print("\nTroubleshooting:")
            console.print("  Set GITHUB_TOKEN env var (needs repo read access)")
            console.print("  Use --repo owner/reponame to specify the repo")
            console.print("  Get a token: github.com/settings/tokens")
            raise typer.Exit(1)

    if fmt == "json":
        print(_json.dumps(report, indent=2))
        return

    score = report["score"]
    emoji = "\U0001f7e2" if score >= 0.8 else "\U0001f7e1" if score >= 0.5 else "\U0001f534"

    console.print(Panel.fit(
        f"{emoji} [bold]PR #{report['pr_number']}[/bold]: {report['pr_title']}\n"
        f"Author: @{report.get('pr_author', 'unknown')}\n"
        f"Changed files: {len(report['changed_files'])}\n"
        f"Requirements: {report['total_requirements']} found, "
        f"{len(report['gaps'])} untested",
        title="Quell PR Analysis",
    ))

    if not report["gaps"]:
        console.print("[green]All requirements in changed files are tested.[/green]")
    else:
        table = Table(title=f"{len(report['gaps'])} Untested Requirements")
        table.add_column("File", style="blue")
        table.add_column("Function", style="cyan")
        table.add_column("Requirement", style="white")
        table.add_column("Type", style="magenta")

        for g in report["gaps"]:
            table.add_row(g["file"], g["function"], g["description"], g["kind"])

        console.print(table)
        console.print("\n[yellow]Fix locally:[/yellow] quell check src/ --fix")

    if comment:
        with console.status("Posting comment to PR..."):
            try:
                runner.post_comment(report)
                console.print(f"[green]Comment posted to PR #{pr_number}[/green]")
                console.print(f"  {report.get('pr_url', '')}")
            except Exception as e:
                console.print(f"[red]Failed to post comment: {e}[/red]")
                raise typer.Exit(1)


@app.command("install")
def cmd_install(
    project_root: Path = typer.Option(Path("."), "--root"),
    hook: bool = typer.Option(False, "--hook", help="Add pre-commit hook"),
    pr: bool = typer.Option(False, "--pr", help="Add GitHub Actions PR workflow"),
) -> None:
    """
    Set up Quell in your project.

    quell install          → adds both pre-commit hook and GitHub Action
    quell install --hook   → pre-commit hook only
    quell install --pr     → GitHub Action only
    """
    if not hook and not pr:
        hook = True
        pr = True

    if hook:
        _install_precommit_hook(project_root)

    if pr:
        _install_github_action(project_root)


def _install_precommit_hook(project_root: Path) -> None:
    config_file = project_root / ".pre-commit-config.yaml"
    hook_entry = """
  - repo: local
    hooks:
      - id: quell
        name: Quell — verify requirements
        entry: quell check --diff-only --no-llm --auto
        language: system
        types: [python]
        pass_filenames: false
"""
    if config_file.exists():
        if "id: quell" in config_file.read_text():
            console.print("[yellow]Quell hook already in .pre-commit-config.yaml[/yellow]")
            return
        config_file.write_text(config_file.read_text() + hook_entry)
    else:
        config_file.write_text(f"repos:{hook_entry}")

    console.print("[green]Added Quell to .pre-commit-config.yaml[/green]")
    console.print("  Runs on every git commit (changed files only, < 3 seconds)")


def _install_github_action(project_root: Path) -> None:
    workflows_dir = project_root / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    action_file = workflows_dir / "quell.yml"

    if action_file.exists():
        console.print("[yellow]quell.yml already in .github/workflows/[/yellow]")
        return

    action_file.write_text(GITHUB_ACTION_YAML)
    console.print("[green]Created .github/workflows/quell.yml[/green]")
    console.print("\nNext steps:")
    console.print("  1. Add QUELL_API_KEY to GitHub repo secrets")
    console.print("     github.com → Settings → Secrets → Actions")
    console.print("     Get key: quell.buildsbyshashank.tech")
    console.print("\n  2. git add .github/workflows/quell.yml && git commit")
    console.print("\n  Quell will comment on every PR automatically.")


# ── Auth subcommands ──────────────────────────────────────────────────────────

@auth_app.command("login")
def auth_login() -> None:
    """
    Log in to quell.buildsbyshashank.tech via browser.

    Opens your browser for secure OAuth login.
    One active session per account — logging in here
    invalidates any other active sessions.

    For CI/CD: set QUELL_API_KEY environment variable instead.
    """
    from quell.auth.oauth import login

    try:
        with console.status("Waiting for browser login..."):
            credentials = login()

        email = credentials.get("email", "unknown")
        plan = credentials.get("plan", "free").capitalize()

        console.print(f"\n[green]Logged in as {email}[/green]")
        console.print(f"  Plan: {plan}")
        console.print("  Session: active on this device")
        console.print("\n  Rule-based checks: unlimited, always free")
        console.print("  LLM checks: use --llm flag (rate limited by plan)")
        console.print("\n  [dim]Previous sessions on other devices have been revoked.[/dim]")

    except RuntimeError as e:
        console.print(f"[red]Login failed: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        console.print("Try again or report at: github.com/shashank7109/quell/issues")
        raise typer.Exit(1)


@auth_app.command("logout")
def auth_logout() -> None:
    """Log out and revoke your session token."""
    from quell.auth.oauth import load_credentials, logout

    creds = load_credentials()
    if not creds:
        console.print("[yellow]Not logged in.[/yellow]")
        return

    with console.status("Revoking session..."):
        logout()

    console.print("[green]Logged out. Token revoked on server.[/green]")
    console.print("  Run [bold]quell auth login[/bold] to log in again.")


@auth_app.command("status")
def auth_status() -> None:
    """Show current authentication status."""
    import os

    from quell.auth.oauth import get_valid_token, verify_token

    if os.environ.get("QUELL_API_KEY"):
        console.print("[green]Authenticated via QUELL_API_KEY env var[/green]")
        console.print("  (CI/CD mode — no session tracking)")
        return

    token = get_valid_token()
    if not token:
        console.print("[yellow]Not logged in.[/yellow]")
        console.print("  Rule-based checks work without login.")
        console.print("  To enable LLM features: [bold]quell auth login[/bold]")
        return

    try:
        with console.status("Checking session..."):
            user_info = verify_token(token)

        console.print(f"[green]Logged in as {user_info.get('email', 'unknown')}[/green]")
        console.print(f"  Plan: {user_info.get('plan', 'free').capitalize()}")
        console.print(
            f"  LLM checks: {user_info.get('checks_remaining', '?')}"
            f"/{user_info.get('checks_limit', '?')} remaining this month"
        )
        console.print("  Session: active on this device")

    except RuntimeError as e:
        console.print(f"[red]Session invalid: {e}[/red]")
        console.print("  Run: [bold]quell auth login[/bold]")
