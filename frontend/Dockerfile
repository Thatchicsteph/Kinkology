# ---- Build the React app ----
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json yarn.lock ./
RUN yarn install --frozen-lockfile --network-timeout 600000
COPY . .
ARG REACT_APP_BACKEND_URL=""
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL
RUN yarn build

# ---- Serve static files + reverse-proxy /api via Caddy (automatic HTTPS) ----
FROM caddy:2-alpine
COPY --from=build /app/build /srv
COPY Caddyfile /etc/caddy/Caddyfile
EXPOSE 80 443
