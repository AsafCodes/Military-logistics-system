# INF-H2. Three stages. The last one is the image: nginx serving the built
# files. It holds no Node, no source and no development server. The
# development stack does not run it; docker-compose.yml builds the first stage
# (`target: dev`) and names the development server in its own `command:`.
#
# tests/test_frontend_image.py reads this file, nginx.conf, .dockerignore and
# the compose service, among others. The `frontend-image` job in
# .github/workflows/ci.yml builds the image and requests pages from it.

# --- dev: the dependencies and the source ------------------------------------
# The major version is the one the `@types/node` entry in package.json targets.
FROM node:24-alpine AS dev

WORKDIR /app

# The manifests first, so the install below is rebuilt only when they change.
# The lockfile is named outright: without it this line fails.
COPY package.json package-lock.json ./

# Installs exactly what the lockfile records, and fails if package.json and
# the lockfile disagree.
RUN npm ci

# .dockerignore keeps the host's node_modules and dist out of this copy; they
# would otherwise land on top of the install above.
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
FROM nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

# The port nginx.conf listens on.
EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
