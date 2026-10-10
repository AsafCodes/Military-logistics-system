"""INF-H2 -- the frontend image serves a production build, not the dev server.

frontend/Dockerfile used to have one stage whose command was `npm run dev`,
and docker-compose.yml ran it unchanged, so Vite's development server was
what any use of the image started. The file now has three stages. The last
one, which is the image, is nginx serving the files `npm run build` produced.
docker-compose.yml, the development stack, builds the first stage and names
the development server itself.

Nothing here needs Docker. The tests read frontend/Dockerfile,
frontend/nginx.conf, frontend/.dockerignore, the compose service and the CI
job, and compare them with each other and with frontend/package.json, the
port in frontend/vite.config.ts and the origin list in backend/main.py.

What is NOT covered here:
  - that the image builds, and what nginx does with nginx.conf. The readers
    below see text; a directive nginx rejects or reads differently passes
    them. The `frontend-image` job in .github/workflows/ci.yml builds the
    image, starts it and requests pages from it, and is the only check of
    either. These tests do not run that job. They check that it exists,
    has no `if` or `continue-on-error` on the job or on a step, builds and
    starts the image, and still contains each of its probe lines;
  - the rest of nginx's configuration. nginx.conf is one file included by
    the nginx image's own /etc/nginx/nginx.conf, which with its mime.types
    decides, for one, what Content-Type a .js file gets and so whether
    `gzip_types` matches it;
  - what the .dockerignore patterns match. Its lines are compared as text;
  - that the development stack starts under Docker;
  - an ENTRYPOINT inherited from a base image. The nginx image has one, and
    it runs the CMD it is given.

PyYAML is imported here and is not named in requirements.txt; it arrives
through `uvicorn[standard]`.
"""
import json
import os
import re
import shlex

import yaml

from tests.test_container_entrypoint import dockerfile_instructions, exec_form

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(PROJECT_ROOT, "frontend")

PORT = "3000"
SERVED_FROM = "/usr/share/nginx/html"
NGINX_CONFIG_IN_IMAGE = "/etc/nginx/conf.d/default.conf"
CI_JOB = "frontend-image"


def read(*parts):
    with open(os.path.join(PROJECT_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


# --- Reading the Dockerfile --------------------------------------------------

def build_stages(text):
    """Split a Dockerfile into its stages, in order.

    Each stage is a dict: `base` (what FROM names), `name` (after AS, or None)
    and `steps`, the (KEYWORD, arguments) pairs that follow its FROM. A flag
    on the FROM line (`--platform=...`) is dropped. An ARG before the first
    FROM is not handled; frontend/Dockerfile has none.
    """
    stages = []
    for keyword, arguments in dockerfile_instructions(text):
        if keyword == "FROM":
            words = [word for word in arguments.split() if not word.startswith("--")]
            name = words[2] if len(words) == 3 and words[1].upper() == "AS" else None
            stages.append({"base": words[0], "name": name, "steps": []})
        else:
            assert stages, f"{keyword} appears before the first FROM"
            stages[-1]["steps"].append((keyword, arguments))
    return stages


def image_stages():
    return build_stages(read("frontend", "Dockerfile"))


def stage_named(name):
    matches = [stage for stage in image_stages() if stage["name"] == name]
    assert len(matches) == 1, f"expected one stage named {name!r}, found {len(matches)}"
    return matches[0]


def the_image():
    """The last stage: what `docker build` with no --target produces."""
    return image_stages()[-1]


# --- Reading nginx.conf ------------------------------------------------------

LOCATION = re.compile(r"location\s+([^{]+?)\s*\{([^{}]*)\}")


def directives(text):
    return [" ".join(part.split()) for part in text.split(";") if part.strip()]


def nginx_server(text):
    """Return (server directives, [(location, directives), ...]).

    Understands what this check needs and no more: `#` comments, one `server`
    block at the top level, and `location` blocks one level deep inside it. A
    `#` or a `;` inside a quoted value, and a block nested in a location, are
    not handled; frontend/nginx.conf has none of them.
    """
    body = " ".join(line.split("#", 1)[0] for line in text.splitlines())
    locations = [(match.group(1), directives(match.group(2))) for match in LOCATION.finditer(body)]
    server = re.fullmatch(r"\s*server\s*\{(.*)\}\s*", LOCATION.sub(" ", body))
    assert server is not None, "expected nginx.conf to be exactly one server block"
    assert "{" not in server.group(1), "a block this reader does not understand"
    return directives(server.group(1)), locations


def served():
    return nginx_server(read("frontend", "nginx.conf"))


def location(path):
    matches = [found for name, found in served()[1] if name == path]
    assert len(matches) == 1, f"expected one `location {path}`, found {len(matches)}"
    return matches[0]


def names(found):
    return {directive.split()[0] for directive in found}


# --- Reading the other files -------------------------------------------------

def package_json():
    return json.loads(read("frontend", "package.json"))


def compose_frontend():
    return yaml.safe_load(read("docker-compose.yml"))["services"]["frontend"]


def ignored_by_the_build_context():
    lines = [line.strip() for line in read("frontend", ".dockerignore").splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def ci_job():
    jobs = yaml.safe_load(read(".github", "workflows", "ci.yml"))["jobs"]
    assert CI_JOB in jobs, f"ci.yml has no `{CI_JOB}` job"
    return jobs[CI_JOB]


def step_running(command):
    steps = [step for step in ci_job()["steps"] if command in step.get("run", "")]
    assert len(steps) == 1, f"expected one step running `{command}`, found {len(steps)}"
    return steps[0]


def major(version):
    return int(re.search(r"\d+", version).group())


# --- The readers themselves --------------------------------------------------

def test_the_stage_reader_understands_the_shapes_it_is_asked_about():
    # Hand-written, so it gets its own check: a stage it failed to tell apart
    # would let a test about "the last stage" read the wrong one.
    text = "\n".join([
        "# FROM in a comment is not a stage",
        "FROM --platform=linux/amd64 node:24-alpine AS dev",
        "RUN npm ci",
        "from dev as build",
        "RUN npm run build",
        "FROM nginx:alpine",
        'CMD ["nginx"]',
    ])
    assert build_stages(text) == [
        {"base": "node:24-alpine", "name": "dev", "steps": [("RUN", "npm ci")]},
        {"base": "dev", "name": "build", "steps": [("RUN", "npm run build")]},
        {"base": "nginx:alpine", "name": None, "steps": [("CMD", '["nginx"]')]},
    ]


def test_the_nginx_reader_understands_the_shapes_it_is_asked_about():
    text = "\n".join([
        "# location /hidden { return 200; }",
        "server {",
        "    listen 80;  # trailing comment",
        "    location = /exact { return 204; }",
        "    location /a/ {",
        "        try_files   $uri",
        "            =404;",
        "    }",
        "    root /srv;",
        "}",
    ])
    assert nginx_server(text) == (
        ["listen 80", "root /srv"],
        [("= /exact", ["return 204"]), ("/a/", ["try_files $uri =404"])],
    )


# --- The image ---------------------------------------------------------------

def test_the_image_is_nginx_and_holds_nothing_but_the_config_and_the_built_files():
    image = the_image()

    assert image["base"].split(":")[0] == "nginx"
    # The whole stage, so that anything added to it is added on purpose: a RUN
    # that installs Node, an ENTRYPOINT that replaces the server, a COPY of
    # the source, an ENV.
    assert image["steps"] == [
        ("COPY", f"nginx.conf {NGINX_CONFIG_IN_IMAGE}"),
        ("COPY", f"--from=build /app/dist {SERVED_FROM}"),
        ("EXPOSE", PORT),
        ("CMD", '["nginx", "-g", "daemon off;"]'),
    ]


def test_the_image_command_is_the_web_server_in_exec_form():
    commands = [arguments for keyword, arguments in the_image()["steps"] if keyword == "CMD"]
    assert len(commands) == 1
    # "daemon off;" keeps nginx in the foreground; without it the process the
    # container started exits at once and the container stops with it.
    assert exec_form(commands[0]) == ["nginx", "-g", "daemon off;"]


def test_the_built_files_come_from_a_stage_that_runs_the_production_build():
    build = stage_named("build")

    # It continues the stage that installed the dependencies.
    assert build["base"] == "dev"
    # The argument is declared before the build runs, and has no default: a
    # default written here would be an API address every image carries
    # unless told otherwise.
    assert build["steps"] == [
        ("ARG", "VITE_API_URL"),
        ("RUN", "npm run build"),
    ]
    # ... and `npm run build` ends in Vite's production build.
    assert package_json()["scripts"]["build"].split("&&")[-1].strip() == "vite build"
    # WORKDIR /app in the first stage is why the files are under /app/dist.
    assert ("WORKDIR", "/app") in stage_named("dev")["steps"]


def test_the_dependencies_are_installed_from_the_lockfile():
    dev = stage_named("dev")

    # The whole stage. In order: the manifests alone, so the install is cached
    # until they change; `npm ci`, which installs what the lockfile records
    # and fails when package.json disagrees with it; then the source. The
    # lockfile is named without a `*`, so a context that lacks it fails the
    # build. And no CMD: the stage is not the image.
    assert dev["steps"] == [
        ("WORKDIR", "/app"),
        ("COPY", "package.json package-lock.json ./"),
        ("RUN", "npm ci"),
        ("COPY", ". ."),
    ]


def test_nothing_in_the_file_installs_outside_the_lockfile_or_starts_the_dev_server():
    # The stages above are each pinned whole. This reads every stage there is,
    # so a fourth one cannot bring `npm install` or `npm run dev` back.
    stages = image_stages()
    assert [stage["name"] for stage in stages] == ["dev", "build", None]

    for stage in stages:
        for keyword, arguments in stage["steps"]:
            if keyword in ("RUN", "CMD", "ENTRYPOINT"):
                assert "npm install" not in arguments
                assert "npm i " not in arguments + " "
                assert "dev" not in arguments.replace(",", " ").replace('"', " ").split()


def test_the_node_version_is_the_one_the_type_definitions_target():
    # The audit found Node 18 under type definitions for a runtime six majors
    # newer. Whichever moves, the other has to follow.
    base = stage_named("dev")["base"]
    # `node:<major>` with an optional variant. A full version
    # (node:24.1.0-alpine) or a digest is not understood here, so pinning
    # the tag further (INF-H8) means widening this pattern.
    found = re.fullmatch(r"node:(\d+)(-[a-z0-9.]+)?", base)
    assert found, f"the dev stage is not built on a numbered node image: {base}"

    types = package_json()["devDependencies"]["@types/node"]
    assert int(found.group(1)) == major(types)


# --- What nginx is told to serve ---------------------------------------------

def test_one_port_from_the_image_to_the_browser():
    server, _ = served()
    listens = {directive for directive in server if directive.startswith("listen ")}
    assert listens == {f"listen {PORT}", f"listen [::]:{PORT}"}

    exposed = [arguments for keyword, arguments in the_image()["steps"] if keyword == "EXPOSE"]
    assert exposed == [PORT]

    frontend = compose_frontend()
    assert frontend["ports"] == [f"{PORT}:{PORT}"]
    # The development server the compose service runs listens there too ...
    assert re.search(rf"\bport:\s*{PORT}\b", read("frontend", "vite.config.ts"))
    # ... and it is the origin the API accepts a browser from.
    assert f'"http://localhost:{PORT}"' in read("backend", "main.py")


def test_nginx_serves_the_directory_the_build_was_copied_to():
    server, _ = served()
    assert f"root {SERVED_FROM}" in server
    assert "index index.html" in server


def test_the_server_block_holds_these_directives_and_no_others():
    # An `error_page 404 /index.html`, a `return`, a `rewrite` or a
    # `proxy_pass` would change what a request gets without touching the two
    # locations the tests below read.
    server, locations = served()
    assert names(server) == {"listen", "root", "index", "gzip", "gzip_types"}
    # Exactly these two, as plain prefixes. A regex location (`~ \.js$`) wins
    # over a prefix one, so it would take requests away from either.
    assert sorted(name for name, _ in locations) == ["/", "/assets/"]
    for _, found in locations:
        assert names(found) == {"try_files", "add_header"}


def test_a_route_the_browser_handles_gets_the_page():
    found = location("/")
    # /dashboard is not a file. With `=404` as the last argument nginx
    # answers 404, and a reload or a typed address fails.
    assert [d for d in found if d.startswith("try_files")] == ["try_files $uri $uri/ /index.html"]
    # index.html names the current asset files, so it is revalidated.
    assert [d for d in found if d.startswith("add_header")] == ['add_header Cache-Control "no-cache"']


def test_a_missing_asset_is_a_404_and_a_present_one_may_be_kept():
    found = location("/assets/")
    # Not the page: a browser that asked for a script would get HTML.
    assert [d for d in found if d.startswith("try_files")] == ["try_files $uri =404"]
    assert [d for d in found if d.startswith("add_header")] == [
        'add_header Cache-Control "public, max-age=31536000, immutable"'
    ]


def test_compression_is_on_for_what_the_build_produces():
    server, _ = served()
    assert "gzip on" in server
    types = [d for d in server if d.startswith("gzip_types ")]
    assert len(types) == 1
    assert {"text/css", "application/javascript"} <= set(types[0].split()[1:])


# --- The build context -------------------------------------------------------

def test_the_build_context_leaves_out_what_the_host_installed_and_built():
    ignored = {line.rstrip("/") for line in ignored_by_the_build_context()}

    # `COPY . .` runs after `npm ci`. The host's node_modules would land on
    # top of the image's own, built for another platform; the host's dist
    # would sit where the build is about to write.
    assert {"node_modules", "dist"} <= ignored
    # Vite reads these when it builds.
    assert {".env", ".env.*"} <= ignored
    # A `!` line puts something back in.
    assert not [line for line in ignored if line.startswith("!")]


# --- The development stack ---------------------------------------------------

def test_the_development_stack_builds_the_node_stage_and_names_the_dev_server_itself():
    frontend = compose_frontend()
    build = frontend["build"]

    assert build["context"] == "./frontend"
    assert build["dockerfile"] == "Dockerfile"
    # A stage that exists, and not the last one: nginx has no npm to run.
    assert build["target"] == "dev"
    assert stage_named(build["target"]) is not None
    assert the_image()["name"] != build["target"]

    assert frontend.get("command") == ["npm", "run", "dev"]
    # `vite` with flags only is the development server; `vite build` and
    # `vite preview` are other programs under the same name.
    program, *rest = package_json()["scripts"]["dev"].split()
    assert program == "vite"
    assert all(word.startswith("-") for word in rest), rest
    # As for the image: an entrypoint here would be what runs instead.
    assert "entrypoint" not in frontend


# --- The CI job that builds and runs the image -------------------------------

def test_the_ci_job_cannot_be_skipped_or_forgiven():
    job = ci_job()

    # The whole key set, for the job and for each step: no `if`, no
    # `continue-on-error`, and nothing else that changes when or whether a
    # step counts.
    assert set(job) == {"runs-on", "steps"}
    for step in job["steps"]:
        assert set(step) <= {"name", "uses", "run"}, step


def test_the_ci_job_builds_the_image_itself_with_an_api_address():
    argv = shlex.split(step_running("docker build")["run"])

    assert argv[:2] == ["docker", "build"]
    # The frontend context and its own Dockerfile, to the last stage.
    assert argv[-1] == "./frontend"
    for flag in ("--target", "-f", "--file"):
        assert not [word for word in argv if word == flag or word.startswith(flag + "=")]

    given = [argv[i + 1] for i, word in enumerate(argv) if word == "--build-arg"]
    assert len(given) == 1
    name, _, address = given[0].partition("=")
    assert name == "VITE_API_URL" and address


def test_the_ci_job_runs_the_image_it_built_with_the_script_set_to_stop_on_a_failure():
    build = shlex.split(step_running("docker build")["run"])
    tag = build[build.index("-t") + 1]

    script = step_running("docker run")["run"]
    lines = [line.strip() for line in script.splitlines() if line.strip()]

    # Without -e a failed probe is just a line of output.
    assert lines[0] == "set -euo pipefail"
    run = [line for line in lines if line.startswith("docker run ")]
    assert len(run) == 1
    argv = shlex.split(run[0])
    assert argv[-1] == tag
    assert argv[argv.index("-p") + 1] == f"{PORT}:{PORT}"
    # -e stays on: no later `set` line.
    commands = [line for line in lines if not line.startswith("#")]
    assert [line for line in commands if line.startswith("set ")] == ["set -euo pipefail"]
    # No `||` but the log dump's. A probe wrapped in an `if`, or written
    # with a leading `!`, would also fail without stopping the script;
    # the test below holds the probe lines as they are written.
    forgiving = [line for line in commands if "||" in line]
    assert forgiving == ["trap 'docker logs frontend-image || true' ERR"]


def test_the_ci_job_still_makes_each_of_its_probes():
    # The probes are shell, and nothing here runs them. This holds each one
    # in the script as a line of its own, outside a comment, so that a
    # probe cannot be dropped or commented out with everything still
    # green. It does not show that a line does what it says.
    build = shlex.split(step_running("docker build")["run"])
    address = build[build.index("--build-arg") + 1].partition("=")[2]

    script = step_running("docker run")["run"]
    commands = [line.strip() for line in script.splitlines()]

    for probe in (
        # the page is HTML, revalidated, and the built one
        "grep -qi '^content-type: text/html' root.headers",
        "grep -qi '^cache-control: no-cache' root.headers",
        "grep -q '<div id=\"root\">' root.html",
        "if grep -q '/src/main.tsx' root.html; then",
        # a route the browser handles gets the same page
        'curl -fsS -o deep.html "$base/dashboard"',
        "cmp root.html deep.html",
        # the script the page names
        "grep -qi '^content-type: .*javascript' script.headers",
        "grep -qi '^cache-control: .*immutable' script.headers",
        "grep -qi '^content-encoding: gzip' script.headers",
        # a missing asset
        'test "$status" = 404',
        # the build argument, no source maps, no Node
        f"docker exec frontend-image grep -rlF '{address}' \"$html/assets\"",
        'test -z "$maps"',
        "if docker exec frontend-image sh -c 'command -v node'; then",
    ):
        assert probe in commands, f"the probe script lost: {probe}"
