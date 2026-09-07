# 0010 - A01 talks to Qdrant over HTTP, not the LangChain vector store node

Date: 2026-09-07
Status: accepted

**Context**: The node allowlist in the `n8n-workflow-json` skill carries `@n8n/n8n-nodes-langchain.vectorStoreQdrant`
(v1), and A01 (RAG over the repo docs) is the item it exists for. In the pinned image it does not work at all: every
call fails with `TypeError: fetch failed`, and the container log gives the real cause -
`InvalidArgumentError: invalid onError method` from `@qdrant/openapi-typescript-fetch`. `@qdrant/js-client-rest@1.16.2`
builds an **undici 6.28** `Agent` and hands it to Node 26.5's built-in `fetch` as `init.dispatcher`, whose handler
expects undici 7's interface. It is a dependency mismatch inside the bundled client, not a configuration problem:
reproduced in the main process and through the CLI, against a Qdrant that `wget` and the HTTP Request node reach
perfectly well from that same container, and no node parameter avoids it. The other AI lanes are unaffected -
`chainLlm` and `lmChatOllama` on `ai_languageModel` work.
**Decision**: A01 assembles its vector store from HTTP Request and Code nodes against Qdrant's REST API (scroll,
points/delete, PUT collection, points/upsert, points/search) and writes **the payload shape the LangChain node
writes** - `{content, metadata: {...}}` - so the node can be dropped back in over the same collection once the
upstream client is fixed. The allowlist entry stays; it is the intended target, not a mistake.
**Consequences**: The `ai_embedding`, `ai_document`, `ai_textSplitter`, `ai_vectorStore` and `ai_retriever` lanes are
absent from A01's canvas, so it reads as a plain pipeline rather than the usual LangChain sub-node fan-out, and
chunking is a Code node reproducing a recursive splitter instead of a configured one. In exchange the retrieval path
is fully visible - the embedding call, the filter, the similarity floor and the exact search body are all on the
canvas, which suits a teaching repo. Anyone adding a second vector-store workflow should expect the same failure and
copy A01's approach rather than fight the node. Re-test on the next n8n bump (the version pinned in ADR 0001): if
`@qdrant/js-client-rest` has caught up with undici 7, A01 can migrate collection-compatibly.
