"""API-H4. /reports/query and the frontend type that describes it, held together.

The route builds plain dicts with no response model, so nothing generated
from the backend can describe its rows (API-M2). The shared frontend type was
written by hand, and it drifted until it matched the route on two of its
seven fields, while the page that actually renders the report declared a
private, nearly-correct copy of its own. Two definitions of one wire shape,
with the wrong one in the shared module.

These tests read `InventoryReportItem` out of frontend/src/types/index.ts and
compare it with rows the real route returns:

- the keys, both ways, so a renamed field on either side fails;
- optionality: the route always sends every key, so no field may be `?`;
- nullability, both ways: null on the wire only where the type admits it,
  and every field the type declares nullable seen null at least once;
- the scalar type of every non-null value;
- uniqueness: no other flat interface or type alias under frontend/src
  declares a report row, so a copy shaped like the old private one cannot
  come back unnoticed (see that test for what it cannot see).

Reading `InventoryReportItem` itself is a regex over TypeScript source, so it
handles a flat interface only, and it refuses rather than guesses: a block it
cannot match, or a member it cannot read, fails the test instead of shrinking
the comparison.
"""
import re
from pathlib import Path

from backend import models

REPO = Path(__file__).resolve().parents[1]
FRONTEND_SRC = REPO / "frontend" / "src"
TYPES_FILE = FRONTEND_SRC / "types" / "index.ts"
INTERFACE = "InventoryReportItem"

# TypeScript scalar -> the Python type json.loads yields for it.
SCALARS = {"string": str, "number": int}

BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT = re.compile(r"//[^\n]*")


def strip_comments(source):
    return LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", source))


def declared_fields(source, name):
    """{field: (optional, [type alternatives])} for one flat interface."""
    match = re.search(
        rf"export\s+interface\s+{name}\s*\{{(?P<body>[^{{}}]*)\}}", strip_comments(source)
    )
    assert match, f"no flat `export interface {name}` in {TYPES_FILE}"

    fields = {}
    for member in match["body"].split(";"):
        member = member.strip()
        if not member:
            continue
        parsed = re.fullmatch(r"(?P<key>\w+)(?P<optional>\?)?\s*:\s*(?P<type>[^:]+)", member)
        assert parsed, f"cannot read member {member!r} of {name}"
        alternatives = [alt.strip() for alt in parsed["type"].split("|")]
        fields[parsed["key"]] = (bool(parsed["optional"]), alternatives)

    assert fields, f"{name} parsed to zero fields -- the comparison would be vacuous"
    return fields


def report_rows(client, db_session, token_master):
    """The seeded rows plus one item with nothing set that can be left unset.

    The seeded items are verified, held, owned and serialised, so between
    them they never send a null. The bare item has no serial, holder, owner or
    location and was never verified, so it reaches every fallback a valid row
    can -- including last_verified_at's null. (item_type's "Unknown" and
    unit_association's "" need an item with no catalog row or no group; both
    columns are NOT NULL with enforced foreign keys, so no row here reaches
    those two.)
    """
    template = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()
    bare = models.Equipment(catalog_item_id=template.catalog_item_id, group_id=template.group_id)
    db_session.add(bare)
    db_session.commit()
    bare_id = bare.id

    res = client.get("/reports/query", headers=token_master)
    assert res.status_code == 200, res.text
    rows = res.json()
    assert any(row["id"] == bare_id for row in rows), "the bare item is missing from the report"
    return rows


def test_the_shared_type_declares_exactly_the_keys_every_row_carries(
    client, db_session, token_master
):
    fields = declared_fields(TYPES_FILE.read_text(encoding="utf-8"), INTERFACE)

    optional = sorted(key for key, (is_optional, _) in fields.items() if is_optional)
    assert optional == [], (
        f"{INTERFACE} marks {optional} optional, but the route sends every key on every row"
    )

    for row in report_rows(client, db_session, token_master):
        assert set(row) == set(fields), (
            f"route sends {sorted(set(row) - set(fields))} undeclared; "
            f"type declares {sorted(set(fields) - set(row))} never sent"
        )


def test_null_appears_on_the_wire_exactly_where_the_type_admits_it(
    client, db_session, token_master
):
    fields = declared_fields(TYPES_FILE.read_text(encoding="utf-8"), INTERFACE)
    rows = report_rows(client, db_session, token_master)

    for row in rows:
        for key, value in row.items():
            _, alternatives = fields[key]
            if value is None:
                assert "null" in alternatives, (
                    f"route sent null for {key}, which {INTERFACE} types as {' | '.join(alternatives)}"
                )
                continue
            scalars = [alt for alt in alternatives if alt != "null"]
            assert len(scalars) == 1 and scalars[0] in SCALARS, (
                f"{INTERFACE}.{key} is {' | '.join(alternatives)}; this test reads only one scalar"
            )
            expected = SCALARS[scalars[0]]
            # bool is an int subclass; a number field must not accept one.
            assert type(value) is expected, (
                f"{key}={value!r} on the wire, {INTERFACE} says {scalars[0]}"
            )

    for key, (_, alternatives) in fields.items():
        # Each field's scalar type is checked above only on a row where it is
        # not null, so a field null on every row would have its type pass
        # unexamined.
        assert any(row[key] is not None for row in rows), (
            f"{key} is null on every row, so its declared type was never compared"
        )
        # The other direction: a `| null` the route never produces is a type
        # that loosened without anyone noticing, and every reader pays for it
        # in guards.
        if "null" in alternatives:
            assert any(row[key] is None for row in rows), (
                f"{INTERFACE}.{key} admits null, but no row -- not even the bare one -- sends it"
            )


def test_no_second_definition_of_a_report_row_exists_in_the_frontend():
    """The defect was two definitions; this pins it to one.

    A report row is recognised by two keys no other payload in this app
    carries. Interfaces and object type aliases both count.

    Source is scanned WITH its comments. Stripping them by regex cannot tell
    a comment from a string literal -- App.tsx routes `path="/*"`, and a
    later `*/` would erase everything between -- which could hide a second
    definition silently. Unstripped, a commented-out copy is reported, which
    fails loudly and is worth deleting anyway.

    Unlike the parse above, this scan does not refuse what it cannot read: it
    sees only bodies with no brace inside, so a copy with a nested object
    member, a brace in a comment, or an inline type literal such as
    `useState<{ item_type: string; ... }[]>` passes unseen. It catches the
    defect as it actually occurred -- a flat private interface -- not every
    shape a second definition could take.
    """
    declaration = re.compile(
        r"(?:interface\s+(?P<iface>\w+)[^{]*|type\s+(?P<alias>\w+)\s*=\s*)\{(?P<body>[^{}]*)\}"
    )
    found = []
    for path in sorted(FRONTEND_SRC.rglob("*.ts*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        source = path.read_text(encoding="utf-8")
        for match in declaration.finditer(source):
            body = match["body"]
            if re.search(r"\bitem_type\b", body) and re.search(r"\breporting_status\b", body):
                found.append((path.relative_to(FRONTEND_SRC).as_posix(), match["iface"] or match["alias"]))

    assert found == [("types/index.ts", INTERFACE)], found
