# Stage 1: Build React App
FROM node:20.18.3-alpine AS builder

WORKDIR /app

COPY package*.json ./

# `npm ci`, not `npm install`. The project standard is explicit: Node installs from
# `package-lock.json`, and a build that re-resolves instead of reading a lockfile is a failed
# build. `npm install` re-resolves, silently updating the lockfile when a range admits a newer
# version.
# The two Python images already use `uv sync --frozen`; this is the same guarantee for Node.
RUN npm ci

# The four build-time settings the bundle bakes in. Vite substitutes `import.meta.env.VITE_*` at
# build time, so these have to arrive as build args — an environment variable on the *running*
# container reaches nothing, because by then the values are already string literals in the bundle.
#
# `VITE_CURRICULUM_URL` was documented as build-time configuration, and the code did read it — but
# it could not possibly work, because neither this Dockerfile nor the Compose service passed it in.
# Every deployment silently shipped the defaults.
ARG VITE_CURRICULUM_URL
ARG VITE_REPOSITORY_URL
ARG VITE_API_BASE_URL
ARG VITE_LOCALSTACK_URL
ENV VITE_CURRICULUM_URL=${VITE_CURRICULUM_URL} \
    VITE_REPOSITORY_URL=${VITE_REPOSITORY_URL} \
    VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_LOCALSTACK_URL=${VITE_LOCALSTACK_URL}

COPY . .
RUN npm run build

# Stage 2: Serve with Nginx
#
# The unprivileged variant, because runtime images must run as a non-root user and the two Python
# images do. Stock `nginx:alpine` runs its master process as root in order to
# bind :80; this image runs as uid 101 and listens on 8080 instead, which is why `nginx.conf` and
# the Compose port mapping both name 8080. The published host port is unchanged at 3000.
FROM nginxinc/nginx-unprivileged:1.27.4-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
