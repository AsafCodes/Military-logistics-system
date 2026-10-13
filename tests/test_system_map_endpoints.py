"""DOC-H2 -- the endpoint tables of SYSTEM_MAP.md are the application's routes.

Section 4 of the map listed `GET /equipment` for `GET /equipment/accessible`,
`POST /equipment` for `POST /equipment/`, and `PUT /equipment/assign_owner`
for `POST /equipment/assign_owner/`, and had no row for the PATCH that sets
an item's sensitivity. Nothing compared the tables with the routes. These
tests read the method and the path of every row of that section and compare
them with `app.openapi()`.

What is read:
  - the section: from the line that starts `## 4.` to the next line that
    starts `## `;
  - a row: a line of that section of the form
    | `METHOD` | `/path` | description |
    with the method in capitals and a description that is not empty. A
    path is compared character for character with the specification's, so
    a trailing slash and the name of a path parameter both count. The
    specification writes a parameter without its converter: `{name:path}`
    in a decorator is `{name}` there. No route has one today;
  - every other line of the section that starts with `|` must be a table's
    header or the rule under it, as HEADER and RULE spell them.

What is NOT covered here:
  - the description column. A row that keeps its method and path and says
    something false about the route passes;
  - the file a `###` heading names for its table, and the table a row is in;
  - the order of the rows;
  - a route named in the map's prose outside section 4. Several write `{id}`
    for a parameter the code names otherwise;
  - anything else of the contract: query parameters, bodies, status codes;
  - a row whose line does not start with `|`, indented for one. It is not
    read, so its route is reported as having no row, and an indented row
    for a route that does not exist is not reported at all;
  - a row the page does not show: one inside an HTML comment or a code
    fence is read like any other;
  - a route the specification does not have. Four are served today with no
    row: `/openapi.json`, `/docs`, `/docs/oauth2-redirect` and `/redoc`,
    which FastAPI adds. A route kept out with the keyword
    `include_in_schema=` written in a call under backend/ fails, whatever
    the value. One kept out another way is served and is asked no row: the
    keyword inside a `**` mapping, the attribute set on a route afterwards,
    a router from outside backend/, a WebSocket route, a Starlette `Route`,
    a mounted application;
  - a specification that is not built from the routes: `app.openapi` or
    `app.openapi_schema` assigned to.
"""
import ast
import os
import re

import pytest

from backend.main import app

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MAP = "SYSTEM_MAP.md"
SECTION_PREFIX = "## 4."
HEADER = "| Method | Path | Description |"
RULE = re.compile(r"\|(?:-+\|)+")
ROW = re.compile(r"\| `([A-Z]+)` \| `(/[^`\s]*)` \| \S.* \|")

# The keys of an OpenAPI path item that are operations.
METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def read(*parts):
    with open(os.path.join(PROJECT_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


# --- Reading the map ---------------------------------------------------------

def section(text):
    """The lines of section 4, without its heading."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(SECTION_PREFIX)]
    assert len(starts) == 1, f"{MAP} has {len(starts)} lines starting `{SECTION_PREFIX}`"
    body = []
    for line in lines[starts[0] + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    return body


def parse(lines):
    """([(method, path), ...] in the lines' order, [table line that is no row, ...])."""
    rows = []
    unread = []
    for line in lines:
        if not line.startswith("|"):
            continue
        line = line.rstrip()
        match = ROW.fullmatch(line)
        if match:
            rows.append((match.group(1), match.group(2)))
        elif line != HEADER and not RULE.fullmatch(line):
            unread.append(line)
    return rows, unread


def map_rows():
    return parse(section(read(MAP)))


# --- Reading the application -------------------------------------------------

def spec_operations():
    return {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        for method in item if method in METHODS
    }


def schema_exclusions(source):
    """The line of each `include_in_schema=` passed in a call in `source`."""
    return sorted(
        keyword.value.lineno
        for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
        for keyword in node.keywords if keyword.arg == "include_in_schema"
    )


def backend_sources():
    for folder, _, names in os.walk(os.path.join(PROJECT_ROOT, "backend")):
        for name in names:
            if name.endswith(".py"):
                path = os.path.join(folder, name)
                yield os.path.relpath(path, PROJECT_ROOT).replace(os.sep, "/"), read(path)


# --- The parser, on lines written here ---------------------------------------

def test_a_row_gives_its_method_and_its_path_as_written():
    lines = [
        "### Equipment (`routers/equipment.py`)",
        HEADER,
        "|--------|------|-------------|",
        "| `POST` | `/equipment/` | Add new equipment (`MANAGE`; by name) |",
        # Spaces after the last bar do not show on the page.
        "| `GET` | `/equipment/{equipment_id}/history` | Audit trail |  ",
        "",
        "Prose with `GET /equipment` in it.",
    ]
    assert parse(lines) == (
        [("POST", "/equipment/"), ("GET", "/equipment/{equipment_id}/history")],
        [],
    )


def test_a_table_line_that_is_not_a_row_is_reported():
    # Each of these would otherwise leave the comparison without a word.
    not_rows = [
        "| `GET | `/equipment/` | a backtick lost |",
        "| GET | `/equipment/` | no backticks on the method |",
        "| `get` | `/equipment/` | the method in small letters |",
        "| `GET` | `equipment/` | no leading slash |",
        "| `GET` | `/equipment/` |",
        "| `GET` | `/equipment/` |  |",
        "| `GET` | `/equipment/` | no closing bar",
        "| `GET /equipment/` | method and path in one cell |",
        "| Method | Path |",
    ]
    assert parse(not_rows) == ([], not_rows)


def test_the_section_ends_at_the_next_line_that_starts_with_two_hashes():
    text = "## 3. A\n| x |\n## 4. B\none\n### Sub\ntwo\n## 5. C\n| y |\n"
    assert section(text) == ["one", "### Sub", "two"]


def test_the_section_is_refused_without_its_heading_or_with_two():
    # With none there is nothing to read; with two, which one is the tables'.
    for text in ("## 3. A\n| x |\n", "## 4. B\n## 4. C\n"):
        with pytest.raises(AssertionError):
            section(text)


# --- The map against the application -----------------------------------------

def test_every_table_line_of_the_section_is_read():
    assert map_rows()[1] == []


def test_no_route_has_two_rows():
    rows = map_rows()[0]
    assert sorted(row for row in set(rows) if rows.count(row) > 1) == []


def test_the_rows_are_the_routes_of_the_specification():
    rows = set(map_rows()[0])
    routes = spec_operations()
    assert {
        "rows with no route": sorted(rows - routes),
        "routes with no row": sorted(routes - rows),
    } == {"rows with no route": [], "routes with no row": []}


def test_an_exclusion_from_the_schema_is_found_wherever_it_is_passed():
    source = (
        "@router.get('/a', include_in_schema=False)\n"
        "def a(): ...\n"
        "app.include_router(\n"
        "    router,\n"
        "    include_in_schema=flag,\n"
        ")\n"
        "# include_in_schema=False\n"
        "include_in_schema = False\n"
    )
    assert schema_exclusions(source) == [1, 5]


def test_no_route_is_kept_out_of_the_specification():
    # A route declared with include_in_schema=False is served and is not in
    # the specification, so the comparison of rows and routes would not ask
    # for its row. The routes are not read off `app.routes`: FastAPI 0.143
    # keeps an included router there as one private object, not as its
    # routes.
    sources = dict(backend_sources())
    assert "backend/main.py" in sources and "backend/routers/equipment.py" in sources
    assert {
        path: lines
        for path, source in sources.items() if (lines := schema_exclusions(source))
    } == {}


def test_a_row_the_ticket_was_about_is_among_what_is_read():
    # The map had this row as `PUT /equipment/assign_owner`: wrong in its
    # verb and in its trailing slash.
    assert ("POST", "/equipment/assign_owner/") in map_rows()[0]
