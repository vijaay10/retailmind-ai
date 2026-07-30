# RetailMind frontend — Next.js standalone build served slim.
# TODO(S3): activate when the Next.js app lands (PRD Sprint S3).

FROM node:20-alpine AS builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ .
RUN npm run build

FROM node:20-alpine AS runtime
RUN addgroup -g 10001 app && adduser -u 10001 -G app -D app
WORKDIR /srv
COPY --from=builder --chown=app:app /build/.next/standalone ./
COPY --from=builder --chown=app:app /build/.next/static ./.next/static
COPY --from=builder --chown=app:app /build/public ./public
USER app
EXPOSE 3000
CMD ["node", "server.js"]
