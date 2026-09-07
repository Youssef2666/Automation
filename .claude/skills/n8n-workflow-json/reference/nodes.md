# Node parameter shapes (n8n 2.37) - the ones this repo uses

Every block shows the `parameters` object as it appears in exported JSON. Builder helper names in brackets.

## Triggers

**Webhook v2** [`webhook`]
```json
{"httpMethod": "POST", "path": "t01-orders", "responseMode": "responseNode", "options": {}}
```
Node also carries `"webhookId": "<uuid>"`. `responseMode`: `onReceived` (200 immediately), `lastNode`, `responseNode`.
Header auth: `"authentication": "headerAuth"` + credentials `httpHeaderAuth`. Input item: `{headers, params, query, body}`.
Production URL `http://localhost:5678/webhook/<path>` (workflow must be published); test URL `/webhook-test/<path>`.

**Respond to Webhook v1.1** [`respond`]
```json
{"respondWith": "json", "responseBody": "={{ { \"ok\": true } }}", "options": {"responseCode": 202}}
```
`respondWith`: `json | text | noData | firstIncomingItem | allIncomingItems | binary`.

**Schedule Trigger v1.2** [`schedule`]
```json
{"rule": {"interval": [{"field": "cronExpression", "expression": "0 8 * * *"}]}}
{"rule": {"interval": [{"field": "minutes", "minutesInterval": 5}]}}
{"rule": {"interval": [{"field": "days", "daysInterval": 1, "triggerAtHour": 8, "triggerAtMinute": 0}]}}
```
Timezone comes from workflow `settings.timezone` (we set `Africa/Tripoli`).

**Form Trigger v2.2** [`form_trigger`]
```json
{"formTitle": "Contact", "formDescription": "...", "path": "t05-contact", "responseMode": "onReceived",
 "formFields": {"values": [{"fieldLabel": "Name", "requiredField": true},
                            {"fieldLabel": "Email", "fieldType": "email", "requiredField": true},
                            {"fieldLabel": "Topic", "fieldType": "dropdown", "fieldOptions": {"values": [{"option": "Sales"}, {"option": "Support"}]}},
                            {"fieldLabel": "Message", "fieldType": "textarea"}]},
 "options": {"buttonLabel": "Send"}}
```
Output item keys are the field labels (`$json.Name`, `$json.Email`, plus `submittedAt`, `formMode`). URL `/form/<path>`.

**Email Trigger (IMAP) v2** [`imap_trigger`]
```json
{"mailbox": "INBOX", "postProcessAction": "read", "format": "resolved", "downloadAttachments": true,
 "options": {"customEmailConfig": "[\"UNSEEN\"]", "forceReconnect": 30}}
```
Output: `subject, from, to, textPlain, textHtml, date` + binary `attachment_0`, `attachment_1`...

**Local File Trigger v1** [`file_trigger`] (re-enabled via `NODES_EXCLUDE` in compose)
```json
{"triggerOn": "folder", "path": "/home/node/.n8n-files/data/inbox", "events": ["add"],
 "options": {"awaitWriteFinish": true, "ignoreInitial": true, "usePolling": true}}
```
Output: `{event: "add", path: "/home/node/.n8n-files/data/inbox/file.csv"}`.

**Telegram Trigger v1.1** [`telegram_trigger`]: `{"updates": ["message"], "additionalFields": {}}` + credentials `telegramApi`.

**Chat Trigger v1.1** [`chat_trigger`]
```json
{"public": true, "mode": "hostedChat", "options": {"title": "Lab docs bot", "responseMode": "lastNode"}}
```
Output: `{sessionId, action: "sendMessage", chatInput}`. Hosted chat at `/webhook/<webhookId>/chat`.

**Execute Workflow Trigger v1.1** [`sub_trigger`]: `{"inputSource": "passthrough"}` - receives the caller's items as-is.

**Error Trigger v1** [`error_trigger`]: `{}`. Output:
`{execution: {id, url, error: {message, stack, name}, lastNodeExecuted, mode, retryOf}, workflow: {id, name}}`.

## Data nodes

**Postgres v2.5** [`postgres_query` / `postgres_insert` / `postgres_upsert` / `postgres_select`]
```json
{"operation": "executeQuery", "query": "SELECT * FROM tickets WHERE status = $1 LIMIT $2",
 "options": {"queryReplacement": "={{ $json.status }}, 50"}}
```
```json
{"operation": "insert", "schema": {"__rl": true, "value": "public", "mode": "list"},
 "table": {"__rl": true, "value": "tickets", "mode": "list"},
 "columns": {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": [], "schema": [],
             "attemptToConvertTypes": false, "convertFieldsToString": false},
 "options": {}}
```
`upsert` adds `"matchingColumns": ["external_id"]`. `defineBelow` mapping: `"value": {"email": "={{ $json.body.email }}"}`.
With `autoMapInputData`, the input item must contain **only** column names (use a Set node first).
Multiple statements in one `executeQuery` are allowed; parameters are `$1..$n`, values comma-separated in `queryReplacement`
(so keep them simple: ids, enums, ISO dates; free text goes through `insert`/`upsert` mapping).

**Redis v1** [`redis`]
```json
{"operation": "incr", "key": "=idem:{{ $json.body.external_id }}", "expire": true, "ttl": 86400}
{"operation": "set", "key": "cursor:t03", "value": "={{ $json.last_id }}", "keyType": "string"}
{"operation": "get", "key": "cursor:t03", "propertyName": "cursor", "keyType": "automatic", "options": {}}
```
`incr` returns `{ "<key>": <newValue> }` - value 1 means first time seen.

**S3 v1 (MinIO)** [`s3_upload` / `s3_list` / `s3_delete`]
```json
{"resource": "file", "operation": "upload", "bucketName": "backups", "fileName": "demo-2026-09-06.zip",
 "binaryData": true, "binaryPropertyName": "data", "additionalFields": {}, "tagsUi": {}}
{"resource": "file", "operation": "getAll", "bucketName": "backups", "returnAll": true, "options": {"folderKey": "db-dumps/"}}
{"resource": "file", "operation": "delete", "bucketName": "backups", "fileKey": "={{ $json.Key }}", "options": {}}
```
List output fields: `Key, LastModified, Size, ETag` (`folderKey` = key prefix; zero-byte objects and `.../` folder markers are filtered out by the node). Credential `s3`: endpoint `http://minio:9000`, `forcePathStyle: true`.

**HTTP Request v4.2** [`http`]
```json
{"method": "GET", "url": "http://mock-api:8080/orders", "sendQuery": true,
 "queryParameters": {"parameters": [{"name": "_page", "value": "={{ $json.page }}"}, {"name": "_limit", "value": "20"}]},
 "options": {"response": {"response": {"fullResponse": true, "neverError": true}}, "timeout": 10000,
             "batching": {"batch": {"batchSize": 5, "batchInterval": 1000}}}}
```
JSON body: `"sendBody": true, "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json) }}"`.
Multipart upload: `"contentType": "multipart-form-data", "bodyParameters": {"parameters": [{"parameterType": "formBinaryData", "name": "audio_file", "inputDataFieldName": "data"}]}`.
File download: `"options": {"response": {"response": {"responseFormat": "file", "outputPropertyName": "data"}}}`.
`fullResponse` gives `{body, headers, statusCode}`; `neverError` keeps 4xx/5xx as data (pair with an If on statusCode).

**Edit Fields (Set) v3.4** [`set_fields`]
```json
{"assignments": {"assignments": [{"id": "<uuid>", "name": "email", "value": "={{ $json.body.email.toLowerCase() }}", "type": "string"},
                                 {"id": "<uuid>", "name": "amount", "value": "={{ Number($json.body.amount) }}", "type": "number"}]},
 "includeOtherFields": false, "options": {}}
```
Types: `string | number | boolean | array | object`.

**Code v2** [`code`]: `{"jsCode": "...", "mode": "runOnceForEachItem"}` (omit mode for run-once-for-all-items).

**If v2.2 / Filter v2.2** [`if_` / `filter_`]
```json
{"conditions": {"options": {"caseSensitive": true, "leftValue": "", "typeValidation": "loose", "version": 2},
                "conditions": [{"id": "<uuid>", "leftValue": "={{ $json.body.email }}", "rightValue": "",
                                "operator": {"type": "string", "operation": "exists", "singleValue": true}}],
                "combinator": "and"},
 "options": {}}
```
Operators: string `equals|notEquals|contains|notContains|startsWith|endsWith|regex|exists|notExists|empty|notEmpty`;
number `equals|notEquals|gt|gte|lt|lte|exists`; boolean `true|false`; array `contains|lengthEquals|lengthGt|lengthLt`;
dateTime `after|before|equals`. `singleValue: true` for unary operators.

**Switch v3.2** [`switch`]
```json
{"rules": {"values": [{"conditions": {...as If...}, "renameOutput": true, "outputKey": "billing"},
                      {"conditions": {...}, "renameOutput": true, "outputKey": "technical"}]},
 "options": {"fallbackOutput": "extra"}}
```

**Merge v3** [`merge`]: `{"mode": "append"}` (2 inputs), or
`{"mode": "combine", "combineBy": "combineByFields", "advanced": true, "mergeByFields": {"values": [{"field1": "id", "field2": "customer_id"}]}, "joinMode": "enrichInput1"}`.

**Loop Over Items (Split In Batches) v3** [`loop`]: `{"batchSize": 10, "options": {}}`. Output 0 = done, output 1 = loop.

**Aggregate v1** [`aggregate`]: `{"aggregate": "aggregateAllItemData", "destinationFieldName": "data", "options": {}}`.

**Split Out v1** [`split_out`]: `{"fieldToSplitOut": "body.items", "options": {}}`.

**Remove Duplicates v2 (across executions)** [`dedupe_prev_runs`]
```json
{"operation": "removeItemsSeenInPreviousExecutions", "logic": "removeItemsWithAlreadySeenKeyValues",
 "dedupeValue": "={{ $json.guid }}", "options": {"scope": "workflow", "historySize": 10000}}
```

**Extract From File v1** [`extract`]: `{"operation": "csv", "binaryPropertyName": "data", "options": {"headerRow": true}}`;
operations `csv | xlsx | pdf | text | html | fromJson | binaryToPropery`.

**Convert To File v1.1** [`convert`]: `{"operation": "csv", "options": {"fileName": "report.csv"}}`,
`{"operation": "toBinary", "sourceProperty": "html", "options": {"fileName": "page.html", "mimeType": "text/html"}}`.

**Read/Write Files v1** [`read_file` / `write_file`]
```json
{"fileSelector": "/home/node/.n8n-files/seed/customers.csv", "options": {"dataPropertyName": "data"}}
{"operation": "write", "fileName": "=/home/node/.n8n-files/data/out/{{ $json.name }}.pdf", "dataPropertyName": "data", "options": {}}
```

**Compression v1.1** [`compress`]: `{"operation": "compress", "binaryPropertyName": "data", "outputFormat": "zip", "fileName": "bundle.zip"}`.
To zip many binaries, use an Aggregate node (binary aggregation) or a Code node producing `data0, data1...` then `binaryPropertyName: "data0,data1"`.

**Crypto v1** [`crypto_hash`]: `{"action": "hash", "type": "SHA256", "value": "={{ JSON.stringify($json) }}", "dataPropertyName": "hash", "encoding": "hex"}`;
HMAC: `{"action": "hmac", "type": "SHA256", "value": "={{ $json.rawBody }}", "secret": "...", "dataPropertyName": "sig", "encoding": "hex"}`.

**HTML v1.2** [`html_extract`]
```json
{"operation": "extractHtmlContent", "sourceData": "json", "dataPropertyName": "data",
 "extractionValues": {"values": [{"key": "name", "cssSelector": ".product h3", "returnValue": "text", "returnArray": true},
                                 {"key": "price", "cssSelector": ".product .price", "returnValue": "text", "returnArray": true}]},
 "options": {}}
```
`sourceData: "json"` reads the HTML string from `dataPropertyName` on `$json`; `"binary"` reads a binary property.

**RSS Feed Read v1.1** [`rss`]: `{"url": "http://mock-api:8080/feed.xml", "options": {}}` -> items `{title, link, pubDate, content, guid}`.

**Execute Workflow v1.1** [`execute_workflow`]
```json
{"source": "database", "workflowId": {"__rl": true, "value": "ALP03IdempotencyG", "mode": "id"}, "mode": "once",
 "options": {"waitForSubWorkflow": true}}
```

**Wait v1.1** [`wait`]: `{"amount": 10, "unit": "minutes"}` | `{"resume": "specificTime", "dateTime": "={{ $json.remind_at }}"}` | `{"resume": "webhook"}`.

**Email Send v2.1** [`email`]
```json
{"fromEmail": "automation-lab@lab.local", "toEmail": "ops@lab.local", "subject": "=Digest {{ $today.toFormat('yyyy-LL-dd') }}",
 "emailFormat": "html", "html": "={{ $json.html }}", "options": {"attachments": "data"}}
```

**n8n API node v1** [`n8n_api`]: `{"resource": "execution", "operation": "getAll", "returnAll": false, "limit": 100, "filters": {"status": "error"}}`;
`{"resource": "workflow", "operation": "getAll", "returnAll": true, "filters": {}}`. Credential `n8nApi` (`baseUrl` `http://localhost:5678/api/v1`).

**Stop and Error v1** [`stop_error`]: `{"errorMessage": "=Schema invalid: {{ $json.reason }}"}`.

## LangChain (Ollama + Qdrant, all local)

Sub-nodes hang off a parent via non-main lanes: `ai_languageModel`, `ai_memory`, `ai_tool`, `ai_outputParser`,
`ai_embedding`, `ai_document`, `ai_textSplitter`, `ai_vectorStore`, `ai_retriever`.

**Ollama Chat Model v1** [`ollama_chat`]: `{"model": "llama3.2:3b", "options": {"temperature": 0.2, "format": "json"}}` + credentials `ollamaApi`.
**Embeddings Ollama v1** [`ollama_embed`]: `{"model": "nomic-embed-text:latest"}`.
**Qdrant Vector Store v1** [`qdrant_store`]: `{"mode": "insert", "qdrantCollection": {"__rl": true, "value": "lab-docs", "mode": "id"}, "options": {}}`;
retrieve: `{"mode": "retrieve", ...}` (output lane `ai_vectorStore` -> Vector Store Retriever -> `ai_retriever` -> Retrieval QA chain).
**Default Data Loader v1** [`doc_loader`]: `{"dataType": "binary", "loader": "auto", "binaryMode": "allInputData", "options": {}}` (+ `ai_textSplitter`).
**Recursive Character Text Splitter v1** [`text_splitter`]: `{"chunkSize": 800, "chunkOverlap": 100, "options": {}}`.
**Basic LLM Chain v1.4** [`llm_chain`]: `{"promptType": "define", "text": "=Classify: {{ $json.body }}", "hasOutputParser": true, "messages": {"messageValues": [{"message": "You are..."}]}}`.
**Structured Output Parser v1.2** [`output_parser`]: `{"schemaType": "fromJson", "jsonSchemaExample": "{\"category\": \"billing\", \"priority\": \"high\"}"}` -> `$json.output.category`.
**Retrieval QA Chain v1.4** [`qa_chain`]: `{"promptType": "define", "text": "={{ $json.chatInput }}", "options": {}}` -> `$json.response`.
**Agent v2** [`agent`]: `{"promptType": "define", "text": "={{ $json.chatInput }}", "options": {"systemMessage": "...", "maxIterations": 8}}` -> `$json.output`.
**Window Buffer Memory v1.3** [`memory`]: `{"contextWindowLength": 10}` (session id from Chat Trigger automatically).
**Call n8n Workflow Tool v1.3** [`tool_workflow`]: `{"name": "lookup_customer", "description": "...", "source": "database", "workflowId": {...rl id...}, "specifyInputSchema": true, "schemaType": "fromJson", "jsonSchemaExample": "{\"email\": \"a@lab.local\"}", "fields": {"values": []}}`.
  The sub-workflow must return a single item with a `response` field (string).
**Information Extractor v1** [`info_extractor`]: `{"text": "={{ $json.transcript }}", "schemaType": "fromJson", "jsonSchemaExample": "{...}", "options": {}}` -> `$json.output`.
**Text Classifier v1** [`text_classifier`]: `{"inputText": "={{ $json.body }}", "categories": {"categories": [{"category": "billing", "description": "..."}]}, "options": {"fallback": "other"}}` - one output per category, in order.

## Top-level export shape

```json
{"name": "T01 - Webhook to Database", "nodes": [...], "connections": {...}, "active": false,
 "settings": {"executionOrder": "v1", "timezone": "Africa/Tripoli", "saveManualExecutions": true,
              "saveExecutionProgress": true, "callerPolicy": "workflowsFromSameOwner", "errorWorkflow": "ALP01ErrorHandle"},
 "pinData": {}, "versionId": "<uuid>", "meta": {"templateCredsSetupCompleted": true}, "id": "ALT01WebhookToDa",
 "tags": [{"name": "automation-lab"}, {"name": "Triggers"}]}
```
