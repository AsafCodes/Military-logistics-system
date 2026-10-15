"""SEC-M12: grants are written by two scripts and by nothing else.

Two claims in the code rest on that fact, and this file is what holds them:

  - bootstrap_admin.already_bootstrapped's docstring: "no route anywhere in
    the backend issues a Grant ... Grants come from this script and seed_data
    and nowhere else."
  - SEC-M12's closure. MANAGE_CATALOG folds can_add_category and
    can_remove_category into one verb: whoever may propose through the setup
    route, approve someone else's proposal or read the queue may also delete a
    fault type. (Proposing one through a fault report needs no catalog verb,
    and nothing skips review -- SEC-M14.) The ticket's trigger for splitting
    it -- "the moment any profile holds one without the other" -- died with
    profiles.py at H1-12 (aa69b0f). What replaces it is this file. Both
    writers are fixed tables that change only by a code edit, so a request to
    grant the review rights without deletion would most likely arrive as a new
    way of issuing grants, and this file fails on one. Adding one is the
    moment to decide the split again (Capability's docstring in
    backend/enums.py). SEC-M14, which redesigned the same routes, decided it
    once: deletion stays on the one verb, and proposing is separated from
    approving by the proposer check in setup.approve_fault_type, not by a
    verb.

So a failure here is not a style complaint. Before allowlisting a new writer,
decide whether MANAGE_CATALOG should split, and correct both claims above.

Scope is backend/ and alembic/. Migrations count because they are not
offline tools here: main.lifespan calls run_migrations(), so a revision that
inserts grants issues them on deploy. tests/ is out -- fixtures grant nothing
outside a test database.

AST rather than grep, in the shape of tests/test_audit_trail.py's constructor
guard: authz.py's `class Grant` is a ClassDef, not a Call, and the docstrings
naming the class cannot fail their own scan. Any attribute called Grant counts,
whatever its receiver, deliberately: a future schemas.Grant would raise a false
alarm here, and a false alarm is loud and explains itself, where a narrower
match would be a silent miss. Out of reach, and accepted: the class fetched by
getattr or by string, `__table__` held in a variable before `.insert()` is
called on it, and SQL assembled by concatenation or formatting.
"""
import ast
import re
from pathlib import Path

import pytest

import backend
from backend import authz

REPO_ROOT = Path(backend.__file__).parent.parent
SCANNED = ("backend", "alembic")
GRANT = "Grant"
TABLE = "grants"
KNOWN_WRITERS = frozenset({"backend/bootstrap_admin.py", "backend/seed_data.py"})

RAW_INSERT = re.compile(
    r"insert\s+(?:or\s+\w+\s+)?into\s+(?:[\"`\[]?\w+[\"`\]]?\.)?[\"`\[]?grants\b",
    re.IGNORECASE,
)


def trees():
    for top in SCANNED:
        for path in sorted((REPO_ROOT / top).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            yield path.relative_to(REPO_ROOT).as_posix(), ast.parse(source, filename=str(path))


def _imported_as(tree, wanted):
    """Local names bound by `from x import <name> [as alias]` for wanted(name)."""
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if wanted(alias.name)
    }


def _callee(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _names_grant(node, aliases):
    """`Grant`, an import alias of it, `x.Grant`, or any of those `.__table__`."""
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Attribute):
        return node.attr == GRANT or (
            node.attr == "__table__" and _names_grant(node.value, aliases)
        )
    return False


def grant_writes(tree):
    """Line numbers in `tree` that construct or insert a grant row."""
    grants = {GRANT} | _imported_as(tree, lambda name: name == GRANT)
    inserts = _imported_as(tree, lambda name: "insert" in name.lower())
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = _callee(func)
        operands = list(node.args) + [kw.value for kw in node.keywords]
        is_write = (
            # Grant(...), authz.Grant(...), G(...) -- but not Grant.__table__.
            (_names_grant(func, grants) and name != "__table__")
            # insert(Grant), pg_insert(authz.Grant), bulk_insert_mappings(mapper=Grant)
            or (
                name is not None
                and ("insert" in name.lower() or name in inserts)
                and any(_names_grant(arg, grants) for arg in operands)
            )
            # Grant.__table__.insert()
            or (
                isinstance(func, ast.Attribute)
                and "insert" in func.attr.lower()
                and _names_grant(func.value, grants)
            )
            # sa.table("grants", ...): a migration's handle for op.bulk_insert.
            or (
                name in ("table", "Table")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == TABLE
            )
        )
        if is_write:
            lines.append(node.lineno)
    return lines


def writers():
    return {relpath: found for relpath, tree in trees() if (found := grant_writes(tree))}


def test_the_scan_names_the_real_class_and_table():
    """The two names this file searches for are the model's own.

    Renaming the class or its table would otherwise turn every scan below into
    a search for something that no longer exists, and every one would pass.
    """
    assert authz.Grant.__name__ == GRANT
    assert authz.Grant.__tablename__ == TABLE


def test_grants_are_written_by_exactly_the_two_known_writers():
    """Equality, not subset: a writer disappearing is news too.

    If seed_data or bootstrap_admin stopped writing grants, the docstring in
    bootstrap_admin would be wrong in the other direction.
    """
    found = writers()
    assert set(found) == KNOWN_WRITERS, (
        "Grant rows are written outside bootstrap_admin.py and seed_data.py, "
        "or one of them stopped writing them. Before allowlisting a new "
        "writer, decide whether MANAGE_CATALOG should split so deletion takes "
        "a narrower verb (SEC-M12's note in TODO.md, Capability's docstring in "
        "backend/enums.py), and correct bootstrap_admin.already_bootstrapped's "
        f"docstring. Writers found: {found}"
    )


@pytest.mark.parametrize("writer", sorted(KNOWN_WRITERS))
def test_each_known_writer_still_writes_grants(writer):
    """Not vacuous: each allowlisted file really is a writer."""
    tree = ast.parse((REPO_ROOT / writer).read_text(encoding="utf-8"))
    assert grant_writes(tree), (
        f"{writer} no longer constructs authz.Grant, so the scan above may be "
        "looking for a name the code no longer uses"
    )


def test_no_raw_sql_inserts_into_grants():
    """The scan above sees ORM and Core writes only; a SQL string goes round it."""
    offenders = [
        f"{relpath}:{node.lineno}"
        for relpath, tree in trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and RAW_INSERT.search(node.value)
    ]
    assert offenders == [], (
        f"raw SQL inserts into grants: {offenders}. See this file's docstring."
    )
