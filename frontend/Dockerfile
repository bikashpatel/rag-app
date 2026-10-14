# Base images are pinned by digest to builds at least two weeks old; bump them deliberately.
FROM node:22.23.3-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.30.5-alpine@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94
# The entrypoint renders the template into conf.d/default.conf, substituting only NGINX_* variables.
ENV NGINX_ENVSUBST_FILTER=^NGINX_
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --chmod=0755 upload-limit.envsh /docker-entrypoint.d/15-upload-limit.envsh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
