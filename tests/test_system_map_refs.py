"""DOC-H1 -- the files and functions SYSTEM_MAP.md names are there.

Module A of the map still read `security.py` → `get_visible_equipment()`
after d06cbcc deleted that function, until 383d468 corrected it. Nothing
compared the map with the code. These tests read the map's `- **Files:**`
lines and compare the names on them with the tree.

This file imports nothing from the backend; the Python files are parsed
with `ast`. tests/conftest.py imports the application, so a backend that
cannot be imported stops these tests with the rest.

What a `Files:` line is read as:
  - a backticked token of letters, digits and `_ . / -` that ends in .py,
    .ts, .tsx or .css is a file. It must be found, spelled as written, under
    exactly one of ROOTS;
  - a `→` straight after a file starts that file's list of names: every
    backticked token from there to the next file, whatever words stand
    between them. A trailing `()` is dropped. A name is defined when the top
    of the file has a `def`, a `class` or an assignment to that one name;
    `Equipment.compliance_level` is the class and then the same in its body;
  - anything inside parentheses is not read.

What is NOT covered here:
  - the map's prose. A function that keeps its name and changes what it does
    passes;
  - the tree in section 2 of the map, and every name outside a `Files:` line;
  - a name on a `Files:` line with no `→` between it and its file. It is
    read as prose, so an arrow written `->` leaves its names unread, and
    so does a file and its arrow inside one pair of backticks;
  - a file moved from one of ROOTS to another, when the map writes it
    without its folder. It is still found in one place;
  - a token that is not such a path: another ending (.md, .yml, .json), a
    space or a `*` in it. It is not taken for a file;
  - names in a file that is not Python. A `→` after one fails, since nothing
    here can check it;
  - a name a Python file gets another way: by import, by a tuple assignment,
    under an `if` or a `try`, inside a function. Such a name fails;
  - a file or a function the map does not name at all.
"""
import ast
import os
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MAP = "SYSTEM_MAP.md"
MODULE_PREFIX = "### Module "
FILES_PREFIX = "- **Files:**"
ARROW = "→"

# Where a path on a `Files:` line may start from. The map writes
# `dependencies.py`, `routers/maintenance.py`, `backend/authz.py` and
# `components/ui/NetworkGlobe.tsx` alike.
ROOTS = ("", "backend/", "backend/routers/", "frontend/src/")

PATH = re.compile(r"[\w./-]+\.(?:py|tsx?|css)")


def read(*parts):
    with open(os.path.join(PROJECT_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


# --- Reading the map ---------------------------------------------------------

def files_lines(text):
    return [line for line in text.splitlines() if line.startswith(FILES_PREFIX)]


def outside_backticks(line):
    return "".join(line.split("`")[0::2])


def parse(line):
    """[(path, [name, ...]), ...] for one `Files:` line, in the line's order."""
    found = []
    depth = 0           # of parentheses, counted outside backticks only
    after_file = False  # the last token read was a file
    listing = False     # from a `→` straight after a file to the next file
    pieces = line[len(FILES_PREFIX):].split("`")
    for index, piece in enumerate(pieces):
        if index % 2 == 0:
            for char in piece:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                elif char == ARROW and depth == 0:
                    listing = after_file
        elif depth:
            continue
        elif PATH.fullmatch(piece):
            found.append((piece, []))
            after_file = True
            listing = False
        else:
            after_file = False
            if listing:
                found[-1][1].append(piece.removesuffix("()"))
    return found


def references():
    return [pair for line in files_lines(read(MAP)) for pair in parse(line)]


def is_file(parts):
    """True when the repository has this file under exactly this spelling.

    os.path.isfile alone takes `Models.py` for `models.py` on Windows and
    refuses it on the Linux runner, so each part is looked up in a listing.
    """
    folder = PROJECT_ROOT
    for part in parts:
        if not os.path.isdir(folder) or part not in os.listdir(folder):
            return False
        folder = os.path.join(folder, part)
    return os.path.isfile(folder)


def locations(path):
    """Every place under ROOTS where `path` is a file, from the repository top."""
    return [root + path for root in ROOTS if is_file((root + path).split("/"))]


def not_one_file(paths):
    """path -> its locations, for each path that is not exactly one file."""
    return {path: locations(path) for path in paths if len(locations(path)) != 1}


# --- Reading a Python file ---------------------------------------------------

def defined(body):
    """name -> node, for what a module's or a class's body defines directly."""
    names = {}
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names[target.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names[node.target.id] = node
    return names


def defines(source, dotted):
    body = ast.parse(source).body
    for part in dotted.split("."):
        node = defined(body).get(part)
        if node is None:
            return False
        body = node.body if isinstance(node, ast.ClassDef) else []
    return True


# --- The parser, on lines written here ---------------------------------------

def test_names_after_an_arrow_are_read_and_their_brackets_dropped():
    line = "- **Files:** `a.py` → `one()` (the filter), `two`, `b.py` → `Three.four`"
    assert parse(line) == [("a.py", ["one", "two"]), ("b.py", ["Three.four"])]


def test_words_between_names_do_not_end_the_list():
    # A name after "and" is as much a claim as one after a comma.
    line = "- **Files:** `a.py` → `one` and `two`; see `three`"
    assert parse(line) == [("a.py", ["one", "two", "three"])]


def test_what_is_in_parentheses_is_not_read():
    # Module C's line in miniature: column names are not definitions the top
    # of the file carries. Neither a file nor an arrow is read in there.
    line = "- **Files:** `models.py` → `Equipment` (fields: `owner_user_id`, `x.py`)"
    assert parse(line) == [("models.py", ["Equipment"])]
    assert parse("- **Files:** `a.py` (see → `one`), `two`") == [("a.py", [])]


def test_an_arrow_lists_names_only_straight_after_a_file():
    line = "- **Files:** none. `Profile` and `User.role` are deleted from `models.py`"
    assert parse(line) == [("models.py", [])]
    assert parse("- **Files:** → `one`, `a.py`") == [("a.py", [])]
    # `README.md` is not taken for a file, and its names are not `a.py`'s.
    assert parse("- **Files:** `a.py`, `README.md` → `Name`") == [("a.py", [])]
    assert parse("- **Files:** `a.py` → `one` → `two`") == [("a.py", ["one"])]


def test_the_next_file_ends_a_list_of_names():
    line = "- **Files:** `a.py`, `b.py` → `Capability`, `c.py`, `not_a_name`"
    assert parse(line) == [("a.py", []), ("b.py", ["Capability"]), ("c.py", [])]


def test_a_dotted_name_is_a_class_and_then_its_member():
    source = (
        "class Equipment:\n"
        "    @property\n"
        "    def level(self): ...\n"
        "    kind = 1\n"
        "    size: int\n"
        "async def run():\n"
        "    def inner(): ...\n"
    )
    for name in ("Equipment", "Equipment.level", "Equipment.kind",
                 "Equipment.size", "run"):
        assert defines(source, name), name
    for name in ("Equipment.missing", "level", "Equipment.kind.real", "missing",
                 "run.inner", "inner"):
        assert not defines(source, name), name


def test_a_file_is_found_only_under_its_own_spelling():
    assert is_file(["tests", "test_system_map_refs.py"])
    assert not is_file(["tests", "Test_system_map_refs.py"])
    assert not is_file(["Tests", "test_system_map_refs.py"])
    assert not is_file(["tests"])
    assert not is_file(["tests", "..", "tests", "test_system_map_refs.py"])


def test_a_path_found_nowhere_or_in_two_places_is_not_one_file():
    # `__init__.py` is under backend/ and under backend/routers/.
    assert not_one_file(["__init__.py", "gone.py", "models.py"]) == {
        "__init__.py": ["backend/__init__.py", "backend/routers/__init__.py"],
        "gone.py": [],
    }


# --- The map's shape ---------------------------------------------------------
#
# The two tests of the last section pass when there is nothing to check. The
# five here fail on the ways of having nothing to check that they name. A
# way they do not name, such as one arrow written `->`, passes.

def test_every_module_has_one_files_line():
    counts = {}
    module = None
    for line in read(MAP).splitlines():
        if line.startswith("#"):
            module = line if line.startswith(MODULE_PREFIX) else None
            if module:
                counts[module] = 0
        elif module and line.startswith(FILES_PREFIX):
            counts[module] += 1
    assert counts, f"{MAP} has no `{MODULE_PREFIX}` heading"
    assert {module: n for module, n in counts.items() if n != 1} == {}


def test_backticks_and_parentheses_on_a_files_line_are_paired():
    # An odd backtick turns names into prose and prose into names from there
    # on. A parenthesis left open hides the rest of the line.
    wrong = []
    for line in files_lines(read(MAP)):
        depth = 0
        lowest = 0
        for char in outside_backticks(line):
            depth += (char == "(") - (char == ")")
            lowest = min(lowest, depth)
        if line.count("`") % 2 or depth or lowest:
            wrong.append(line)
    assert wrong == []


def test_every_files_line_names_a_file():
    # A retired module's line passes by naming the files it was removed from.
    assert [line for line in files_lines(read(MAP)) if not parse(line)] == []


def test_every_arrow_has_a_file_before_it_and_a_name_after_it():
    # An arrow inside parentheses is counted too, and no name is read there,
    # so it fails here.
    unread = []
    for line in files_lines(read(MAP)):
        arrows = outside_backticks(line).count(ARROW)
        if arrows != sum(1 for _, names in parse(line) if names):
            unread.append(line)
    assert unread == []


def test_the_filter_is_among_what_is_read():
    # The reference DOC-H1 was about, as the map writes it today.
    assert "backend/dependencies.py" in {
        place
        for path, names in references() if "scope_equipment_query" in names
        for place in locations(path)
    }


# --- The map against the tree ------------------------------------------------

def test_every_file_named_is_one_file():
    # None: the file is gone. Two: the map must write the longer path.
    assert not_one_file(path for path, _ in references()) == {}


def test_every_name_is_defined_in_its_file():
    missing = []
    for path, names in references():
        found = locations(path)
        if not names or len(found) != 1:
            continue  # the test above reports a path that is not one file
        if not path.endswith(".py"):
            missing.append((path, "names after a file that is not Python"))
            continue
        source = read(found[0])
        missing += [(path, name) for name in names if not defines(source, name)]
    assert missing == []
