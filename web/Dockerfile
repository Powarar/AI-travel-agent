FROM node:22-alpine

WORKDIR /app

ENV npm_config_fund=false \
    npm_config_audit=false

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./

EXPOSE 5173

# /app is bind-mounted from the host while /app/node_modules is a named volume. A
# volume may contain a partial install after an interrupted startup; checking only
# for the Vite executable misses broken package chunks. `npm ci` makes every local
# stack startup deterministic from package-lock.json.
CMD ["sh", "-c", "npm ci && exec npm run dev -- --host 0.0.0.0 --port 5173"]
