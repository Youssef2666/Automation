FROM node:26-alpine

WORKDIR /app
ENV NODE_ENV=production

COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund && npm cache clean --force

# db.json stays root-owned on purpose: it is the pristine seed and the runtime user must not be
# able to clobber it. server.js copies it to MOCK_API_DB at startup and json-server writes there,
# so mutating requests work and every restart resets the dataset (see the comment in server.js).
COPY server.js middleware.js db.json ./
ENV MOCK_API_DB=/tmp/mock-api-db.json

EXPOSE 8080
USER node
CMD ["node", "server.js"]
