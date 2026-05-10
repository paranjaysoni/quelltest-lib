"""
Reads production code and extracts logic requirements from guard clauses.

What it detects (AST patterns):

Pattern 1: if condition: raise
  if amount <= 0:
      raise ValueError("must be positive")
  → BOUNDARY requirement: amount must be > 0

Pattern 2: if x is None: raise / return
  if user is None:
      raise ValueError("user not found")
  → NOT_NULL requirement: user must not be None

Pattern 3: if x not in collection: raise
  if currency not in VALID_CURRENCIES:
      raise ValueError("invalid currency")
  → ENUM_VALID requirement: currency must be in set

Pattern 4: assert statement
  assert amount > 0, "must be positive"
  → BOUNDARY requirement: amount must be > 0

Pattern 5: isinstance check with raise
  if not isinstance(amount, (int, float)):
      raise TypeError("must be numeric")
  → TYPE_CHECK requirement: amount must be numeric

Pattern 6: auth/permission check
  if not request.user.is_authenticated:
      raise PermissionError("login required")
  → AUTH_CHECK requirement: user must be authenticated

Pattern 7: bare except / broad except (security smell)
  except Exception:
      pass
  → BARE_EXCEPT smell: catches everything silently

Pattern 8: empty return on failure
  if not result:
      return None   (without raising)
  → SILENT_FAIL smell: fails silently, should raise

Pattern 9: hardcoded values in conditions
  if status == "admin":   # magic string
  → MAGIC_VALUE smell: hardcoded string in condition

Returns [] on any error — never raises.
No LLM. Pure AST. Works on every Python file.
"""
from __future__ import annotations

import ast
import uuid
from pathlib import Path
from typing import Any

from quell.core.models import ConstraintKind, Requirement, SpecSource


class CodeGuardReader:
    """
    Primary reader for v0.6.0.
    Reads production code guards directly from AST.
    Zero config. Zero docstrings. Zero types needed.
    """

    def read(self, file_path: Path) -> list[Requirement]:
        """Read a Python file and extract Requirements from guard clauses."""
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except Exception:
            return []

        requirements: list[Requirement] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                requirements.extend(
                    self._scan_function(node, file_path, source)
                )
        return requirements

    def _scan_function(
        self,
        func: ast.FunctionDef | ast.AsyncFunctionDef,
        path: Path,
        source: str,
    ) -> list[Requirement]:
        reqs: list[Requirement] = []
        lines = source.splitlines()

        for node in ast.walk(func):
            # Pattern 1, 2, 3, 5, 6: if <condition>: raise
            if isinstance(node, ast.If):
                raise_nodes = [
                    n for n in node.body
                    if isinstance(n, ast.Raise)
                ]
                if raise_nodes:
                    req = self._classify_if_raise(
                        node, raise_nodes[0], func, path, lines
                    )
                    if req:
                        reqs.append(req)
                # Pattern 8: silent failure (if x: return None)
                else:
                    req = self._classify_silent_failure(node, func, path, lines)
                    if req:
                        reqs.append(req)

            # Pattern 4: assert
            elif isinstance(node, ast.Assert):
                req = self._classify_assert(node, func, path, lines)
                if req:
                    reqs.append(req)

            # Pattern 7: bare except
            elif isinstance(node, ast.ExceptHandler):
                if node.type is None:  # bare except:
                    reqs.append(self._bare_except_smell(node, func, path, lines))

        return reqs

    def _classify_if_raise(
        self,
        if_node: ast.If,
        raise_node: ast.Raise,
        func: ast.FunctionDef | ast.AsyncFunctionDef,
        path: Path,
        lines: list[str],
    ) -> Requirement | None:
        test = if_node.test
        raw = lines[if_node.lineno - 1].strip() if if_node.lineno <= len(lines) else ""

        # Pattern 2: if x is None or if not x
        if self._is_null_check(test):
            return Requirement(
                id=str(uuid.uuid4())[:8],
                description=f"must not be None — {raw}",
                constraint_kind=ConstraintKind.NOT_NULL,
                source=SpecSource.CODE_GUARD,
                target_function=func.name,
                target_file=path,
                raw_spec_text=raw,
                violation_input=self._extract_null_input(test),
            )

        # Pattern 1: if x <= 0 or if x < 0 etc. (boundary, including compound)
        if self._is_boundary_check(test) or self._is_compound_boundary(test):
            return Requirement(
                id=str(uuid.uuid4())[:8],
                description=f"boundary condition — {raw}",
                constraint_kind=ConstraintKind.BOUNDARY,
                source=SpecSource.CODE_GUARD,
                target_function=func.name,
                target_file=path,
                raw_spec_text=raw,
                violation_input=self._extract_boundary_input(test),
            )

        # Pattern 3: if x not in [...] (enum)
        if self._is_enum_check(test):
            return Requirement(
                id=str(uuid.uuid4())[:8],
                description=f"must be valid value — {raw}",
                constraint_kind=ConstraintKind.ENUM_VALID,
                source=SpecSource.CODE_GUARD,
                target_function=func.name,
                target_file=path,
                raw_spec_text=raw,
                violation_input=self._extract_enum_input(test),
            )

        # Pattern 5: if not isinstance(x, Type) (type check)
        if self._is_isinstance_check(test):
            return Requirement(
                id=str(uuid.uuid4())[:8],
                description=f"type check — {raw}",
                constraint_kind=ConstraintKind.TYPE_CHECK,
                source=SpecSource.CODE_GUARD,
                target_function=func.name,
                target_file=path,
                raw_spec_text=raw,
            )

        # Pattern 6: auth/permission check
        if self._is_auth_check(raw):
            return Requirement(
                id=str(uuid.uuid4())[:8],
                description=f"auth/permission check — {raw}",
                constraint_kind=ConstraintKind.AUTH_CHECK,
                source=SpecSource.CODE_GUARD,
                target_function=func.name,
                target_file=path,
                raw_spec_text=raw,
            )

        # Pattern 9: magic value check
        if self._is_magic_value_check(test):
            return Requirement(
                id=str(uuid.uuid4())[:8],
                description=f"magic value in condition — {raw}",
                constraint_kind=ConstraintKind.MAGIC_VALUE,
                source=SpecSource.CODE_GUARD,
                target_function=func.name,
                target_file=path,
                raw_spec_text=raw,
            )

        # Generic if/raise we couldn't classify specifically
        return Requirement(
            id=str(uuid.uuid4())[:8],
            description=f"guard clause — {raw}",
            constraint_kind=ConstraintKind.CUSTOM,
            source=SpecSource.CODE_GUARD,
            target_function=func.name,
            target_file=path,
            raw_spec_text=raw,
        )

    def _classify_assert(
        self,
        node: ast.Assert,
        func: ast.FunctionDef | ast.AsyncFunctionDef,
        path: Path,
        lines: list[str],
    ) -> Requirement | None:
        raw = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
        if self._is_boundary_check(node.test):
            return Requirement(
                id=str(uuid.uuid4())[:8],
                description=f"assert boundary — {raw}",
                constraint_kind=ConstraintKind.BOUNDARY,
                source=SpecSource.CODE_GUARD,
                target_function=func.name,
                target_file=path,
                raw_spec_text=raw,
            )
        return Requirement(
            id=str(uuid.uuid4())[:8],
            description=f"assert — {raw}",
            constraint_kind=ConstraintKind.CUSTOM,
            source=SpecSource.CODE_GUARD,
            target_function=func.name,
            target_file=path,
            raw_spec_text=raw,
        )

    def _classify_silent_failure(
        self,
        if_node: ast.If,
        func: ast.FunctionDef | ast.AsyncFunctionDef,
        path: Path,
        lines: list[str],
    ) -> Requirement | None:
        """Detect `if not x: return None` — silent failure smell."""
        # Check body is a single `return None`
        if len(if_node.body) == 1 and isinstance(if_node.body[0], ast.Return):
            ret = if_node.body[0]
            if ret.value is None or (isinstance(ret.value, ast.Constant) and ret.value.value is None):
                raw = lines[if_node.lineno - 1].strip() if if_node.lineno <= len(lines) else ""
                return Requirement(
                    id=str(uuid.uuid4())[:8],
                    description=f"silent failure — returns None instead of raising — {raw}",
                    constraint_kind=ConstraintKind.SILENT_FAIL,
                    source=SpecSource.CODE_GUARD,
                    target_function=func.name,
                    target_file=path,
                    raw_spec_text=raw,
                )
        return None

    def _bare_except_smell(
        self,
        node: ast.ExceptHandler,
        func: ast.FunctionDef | ast.AsyncFunctionDef,
        path: Path,
        lines: list[str],
    ) -> Requirement:
        raw = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
        return Requirement(
            id=str(uuid.uuid4())[:8],
            description=f"bare except catches all errors silently — {raw}",
            constraint_kind=ConstraintKind.BARE_EXCEPT,
            source=SpecSource.CODE_GUARD,
            target_function=func.name,
            target_file=path,
            raw_spec_text=raw,
        )

    # ── helpers ─────────────────────────────────────────────────────────────

    def _is_null_check(self, test: ast.expr) -> bool:
        # x is None / x is not None
        if isinstance(test, ast.Compare):
            for op in test.ops:
                if isinstance(op, (ast.Is, ast.IsNot)):
                    if any(
                        isinstance(c, ast.Constant) and c.value is None
                        for c in test.comparators
                    ):
                        return True
        # not x  (loose null check)
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            if isinstance(test.operand, ast.Name):
                return True
        return False

    def _is_boundary_check(self, test: ast.expr) -> bool:
        if isinstance(test, ast.Compare):
            for i, op in enumerate(test.ops):
                if isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                    return True
                # Numeric equality: if x == 0 / if x != 0
                if isinstance(op, (ast.Eq, ast.NotEq)):
                    comparator = test.comparators[i] if i < len(test.comparators) else None
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, (int, float)):
                        return True
        return False

    def _is_enum_check(self, test: ast.expr) -> bool:
        # x not in [...]
        if isinstance(test, ast.Compare):
            for op in test.ops:
                if isinstance(op, ast.NotIn):
                    return True
        # not (x in [...])
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            if isinstance(test.operand, ast.Compare):
                for op in test.operand.ops:
                    if isinstance(op, ast.In):
                        return True
        return False

    def _is_isinstance_check(self, test: ast.expr) -> bool:
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            if isinstance(test.operand, ast.Call):
                func = test.operand.func
                if isinstance(func, ast.Name) and func.id == "isinstance":
                    return True
        return False

    def _is_auth_check(self, raw: str) -> bool:
        auth_keywords = [
            "authenticated", "authorized", "permission",
            "is_admin", "is_staff", "has_role",
            "token", "api_key", "auth",
        ]
        raw_lower = raw.lower()
        return any(kw in raw_lower for kw in auth_keywords)

    def _is_compound_boundary(self, test: ast.expr) -> bool:
        """Detect `if x < 0 or x > 100:` — BoolOp wrapping boundary comparisons."""
        if isinstance(test, ast.BoolOp):
            return any(self._is_boundary_check(v) for v in test.values)
        # `not (0 <= x <= 100)` — UnaryOp wrapping a chained Compare
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            return self._is_boundary_check(test.operand)
        return False

    def _is_magic_value_check(self, test: ast.expr) -> bool:
        """Detect hardcoded string/int literal in a comparison condition."""
        if isinstance(test, ast.Compare):
            for op in test.ops:
                if isinstance(op, (ast.Eq, ast.NotEq)):
                    for comp in test.comparators:
                        if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                            return True
        return False

    def _extract_null_input(self, test: ast.expr) -> dict[str, Any] | None:
        if isinstance(test, ast.Compare) and test.comparators:
            if isinstance(test.left, ast.Name):
                return {test.left.id: None}
        if isinstance(test, ast.UnaryOp) and isinstance(test.operand, ast.Name):
            return {test.operand.id: None}
        return None

    def _extract_boundary_input(self, test: ast.expr) -> dict[str, Any] | None:
        if isinstance(test, ast.Compare):
            if isinstance(test.left, ast.Name) and test.comparators:
                comparator = test.comparators[0]
                if isinstance(comparator, ast.Constant):
                    return {
                        "variable": test.left.id,
                        "boundary_value": comparator.value,
                    }
        return None

    def _extract_enum_input(self, test: ast.expr) -> dict[str, Any] | None:
        if isinstance(test, ast.Compare):
            if isinstance(test.left, ast.Name):
                for comp in test.comparators:
                    if isinstance(comp, (ast.List, ast.Tuple, ast.Set)):
                        values = [
                            elt.value for elt in comp.elts
                            if isinstance(elt, ast.Constant)
                        ]
                        return {"variable": test.left.id, "valid_values": values}
        return None

    @property
    def source_name(self) -> str:
        """Reader name."""
        return "code_guard"
