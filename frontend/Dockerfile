# INF-H2. Three stages. The last one is the image: nginx serving the built
# files. It holds no Node, no source and no development server. The
# development stack does not run it; docker-compose.yml builds the first stage
# (`target: dev`) and names the development server in its own `command:`.
#
# tests/test_frontend_image.py reads this file, nginx.conf, .dockerignore and
# the compose service, among others. The `frontend-image` job in
# .github/workflows/ci.yml builds the image and requests pages from it.
#
# INF-H8. Both base images are named by digest, so a build takes the same two
# images whenever it runs. Docker takes the image a digest names and does not
# use the tag (../Dockerfile.backend says how that was seen). The tag is kept
# for the reader and for Dependabot, which starts from it. Each digest is the
# one its tag had on 2026-10-12, and names the list of the image's builds for
# each platform. A pin is moved by a change to its line;
# ../.github/dependabot.yml asks Dependabot to propose one.
# tests/test_image_pins.py reads both lines.

# --- dev: the dependencies and the source ------------------------------------
# The major version is the one the `@types/node` entry in package.json targets.
# tests/test_frontend_image.py compares the two, and reads the major from the
# tag: nothing here checks that the digest is an image of that major.
FROM node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS dev

# INF-H4. `node` is the unprivileged account the node image ships (uid 1000).
# Everything from here on runs as it: the install, the development server
# docker-compose.yml starts, and the build in the next stage. USER comes
# before WORKDIR so that /app is created belonging to it, which is what
# BuildKit does. The install and the build each make a directory there
# (node_modules, dist); under a builder that made /app root's, the install
# would fail for want of a place to write.
USER node

WORKDIR /app

# The manifests first, so the install below is rebuilt only when they change.
# The lockfile is named outright: without it this line fails.
COPY package.json package-lock.json ./

# Installs exactly what the lockfile records, and fails if package.json and
# the lockfile disagree.
RUN npm ci

# .dockerignore keeps the host's node_modules and dist out of this copy; they
# would otherwise land on top of the install above. Both COPY lines write
# their files as root, with no --chown: the install, the build and the
# development server read the source and do not write to it.
COPY . .

# No CMD here on purpose, so the stage keeps the node image's (`node`).
# docker-compose.yml gives its own.

# --- build: the static files -------------------------------------------------
FROM dev AS build

# The address of the API, written into the JavaScript by the build below.
# It is chosen here, at `docker build --build-arg VITE_API_URL=...`. Setting
# the variable on a running container does nothing (INF-M4). Not given, or
# given empty, the application calls http://localhost:8000 (src/lib/axios.ts).
ARG VITE_API_URL

# The typecheck, then `vite build` into /app/dist.
RUN npm run build

# --- the image ---------------------------------------------------------------
# The tag names a release. This was `nginx:alpine`, which names none; on
# 2026-10-12 the two tags had this one digest, and the image reported 1.31.6.
FROM nginx:1.31.8-alpine@sha256:1ef7ca94d7879df82421428ce13bd1eaa412404953859cf7a5b6512c25e67a60

COPY nginx.conf /etc/nginx/conf.d/default.conf
# INF-H4. --chown because a COPY from another stage keeps the owner the files
# had there, and the build stage made them as its `node` account (uid 1000),
# a number that belongs to nobody in this image.
COPY --chown=0:0 --from=build /app/dist /usr/share/nginx/html

# INF-H4. nginx does not run as root, master process included. It runs as
# `nginx`, the account the nginx image ships and already runs its workers as.
# USER gives that account's number, as Dockerfile.backend does and for the
# same reason; the `test` fails the build if the base image ever gives the
# account another one.
#
# Started as that account, the stock image stops on two paths only root can
# write: the pid file, moved to /tmp here, and the directory for its temporary
# files, handed over here. The `user` line goes because it tells a root master
# which account to give its workers, and with no root master nginx warns about
# it at every start. The `grep` fails the build if the image's nginx.conf ever
# stops having a `pid` line for the `sed` to rewrite.
#
# The configuration and the built files are root's: nginx reads them and
# cannot change them. Port 3000 needs no privilege.
RUN test "$(id -u nginx):$(id -g nginx)" = 101:101 \
 && sed -i -e '/^user /d' -e 's|^pid .*|pid /tmp/nginx.pid;|' /etc/nginx/nginx.conf \
 && grep -qx 'pid /tmp/nginx.pid;' /etc/nginx/nginx.conf \
 && chown 101:101 /var/cache/nginx
USER 101:101

# The port nginx.conf listens on.
EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
