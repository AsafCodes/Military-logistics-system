"""
Write the OpenAPI specification of the application (API-H1).

    python -m backend.export_openapi [path]

With a path, the specification is written there; without one, to stdout. Either
way the output is the exact `app.openapi()` of the code on disk, so it cannot
fall behind the routes the way the tracked frontend/openapi.json did -- that
file declared a version the application had long since left, and ten paths,
two of them since removed, where the application serves twenty-nine.

The specification is deliberately not tracked. Generate it when something needs
it, e.g. before `npm run generate-client`, which reads frontend/openapi.json:

    python -m backend.export_openapi frontend/openapi.json

Importing the application needs no live database (DATA-H10) but does need
DATABASE_URL and SECRET_KEY set (DATA-H11), as any other import of it does.

The output is LF-terminated on every platform, including stdout on Windows,
so that line endings never make two exports differ: the same code on the same
dependencies gives the same bytes anywhere. Not across dependency versions --
the schema text is FastAPI's and Pydantic's, and requirements.txt pins neither.
"""
import json
import sys

from .main import app


def render() -> str:
    return json.dumps(app.openapi(), indent=2) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        sys.stderr.write("usage: python -m backend.export_openapi [path]\n")
        return 2

    text = render()

    if not argv:
        sys.stdout.flush()
        # The binary buffer, not sys.stdout itself: text-mode stdout on
        # Windows translates every \n into \r\n.
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
        return 0

    path = argv[0]
    try:
        # newline="" for the same reason: no translation on write.
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    except OSError as exc:
        sys.stderr.write(f"export_openapi: cannot write {path}: {exc.strerror or exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
