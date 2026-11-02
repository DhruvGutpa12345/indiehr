# AI / Agentic Layer for a Multi-tenant Hospital SaaS in India (state as of 2 Nov 2026)

Method notes for the report writer:
- Version/date data comes mainly from PyPI JSON (full timestamps), the Hugging Face API (model metadata, licence tags, parameter counts, creation dates), GitHub release pages (these show day+month only; the year 2026 is inferred from page footers and is flagged) and primary docs (modelcontextprotocol.io, AWS docs/blog, Sarvam docs, Google HAI-DEF terms).
- API endpoints used (fetched live 2 Nov 2026): `https://pypi.org/pypi/<pkg>/json`, `https://huggingface.co/api/models/<id>`, `https://api.github.com/search/repositories`.
- VRAM figures are MY ARITHMETIC (params x bytes/param + ~15-25% for KV cache/activations at modest context), not vendor-published numbers. Parameter counts are cited from the HF API.
- The blueprint's "Llama 3.3 70B / Qwen 2.5 72B / Meditron / BioMistral" list is confirmed outdated by 1-2 generations (see Q3).

---

## 1. Model Context Protocol: spec revision, transports, authorization, SDKs, FHIR MCP servers

### Takeaway
Latest MCP spec revision is **2026-07-28** (previous: 2025-11-25). It makes the protocol stateless (no `initialize`, no `Mcp-Session-Id`), deprecates HTTP+SSE, Roots/Sampling/Logging and OAuth Dynamic Client Registration (DCR), and prefers Client ID Metadata Documents. Official Python SDK `mcp` is v2.3.0 (MIT); FastMCP (PrefectHQ) is v4.1.5 (Apache-2.0). wso2/fhir-mcp-server is actively maintained (Apache-2.0, v0.11.0, 29 Oct 2026); the-momentum/fhir-mcp-server is stale (last push Oct 2025).

### Cited Findings
**Spec revision & transports**
- Latest revision is 2026-07-28; changelog lists changes since 2025-11-25. — [MCP changelog 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/changelog); [GitHub release tag 2026-07-28](https://github.com/modelcontextprotocol/specification/releases/tag/2026-07-28)
- Protocol-level sessions and `Mcp-Session-Id` header removed from Streamable HTTP; servers needing state use server-minted handles passed as ordinary tool arguments (SEP-2567). — [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- `initialize`/`notifications/initialized` handshake removed; each request carries protocol version + client capabilities in `_meta`; new mandatory `server/discover` RPC (SEP-2575); version mismatch returns `UnsupportedProtocolVersionError`. — [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- `resources/subscribe` and HTTP GET endpoint replaced by `subscriptions/listen`; `ping`, `logging/setLevel`, `notifications/roots/list_changed` removed; SSE stream resumability (`Last-Event-ID`) removed from Streamable HTTP. — [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- Tasks moved out of core into official extension `io.modelcontextprotocol/tasks`; server-initiated requests (sampling/elicitation/roots) replaced by "Multi Round-Trip Requests" (`InputRequiredResult`, `resultType` field required on all results). — [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- Required HTTP headers `Mcp-Method`, `Mcp-Name` on Streamable HTTP POST; `tools/list` etc. must carry `ttlMs`/`cacheScope`; OpenTelemetry trace-context keys documented in `_meta`; JSON Schema 2020-12 allowed for tool input/output schemas. — [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- **Deprecated** (remain in spec, removal after >=12-month window under new lifecycle policy): HTTP+SSE transport (deprecated since 2025-03-26, now formally "Deprecated" status), Roots, Sampling, Logging, `includeContext` "thisServer"/"allServers", and OAuth DCR (RFC 7591). — [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- Supported transports are therefore stdio and Streamable HTTP; python-sdk README still lists stdio, Streamable HTTP and SSE as supported. — [python-sdk README](https://github.com/modelcontextprotocol/python-sdk)

**Authorization**
- Authorization is OPTIONAL; HTTP-based transports SHOULD follow it; stdio SHOULD NOT (take credentials from environment). — [MCP authorization spec 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- Based on OAuth 2.1 IETF draft (draft-ietf-oauth-v2-1-13), RFC 8414 AS metadata, RFC 9728 Protected Resource Metadata (MCP servers MUST implement), RFC 8707 Resource Indicators (MCP clients MUST send `resource` in auth + token requests), RFC 9207 `iss` validation (new), OAuth Client ID Metadata Documents (SHOULD be supported), DCR (MAY; deprecated). — [authorization spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- PKCE required in flow; token audience validation: MCP servers MUST validate tokens were issued for them and MUST NOT accept or pass through other tokens (no token passthrough); bearer token in `Authorization` header on every request, never in query string; 401 for invalid token, 403 `insufficient_scope` with `WWW-Authenticate` scope for step-up. — [authorization spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- The MCP server is an OAuth *resource server*; the authorization server can be a separate IdP (e.g., Keycloak/Entra/Auth0) — spec leaves AS implementation out of scope. — [authorization spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)

**SDKs**
- Official Python SDK `mcp` on PyPI: **2.3.0**, uploaded 2026-10-02, licence MIT, Python >=3.10. — [PyPI JSON](https://pypi.org/pypi/mcp/json)
- python-sdk GitHub releases: v2.0.0 (28 Jul, stable), v2.1.0 (24 Aug), v2.2.0 (7 Sep), v2.3.0 (2 Oct); 1.x maintained in parallel (v1.29.0 28 Jul, v1.30.0 7 Sep); dates lack year on page. — [python-sdk releases](https://github.com/modelcontextprotocol/python-sdk/releases)
- python-sdk README: MIT licence; Python 3.11+ stated on README (PyPI metadata says >=3.10 — minor inconsistency). — [README](https://github.com/modelcontextprotocol/python-sdk); [PyPI](https://pypi.org/pypi/mcp/json)
- FastMCP (PrefectHQ/fastmcp; originally jlowin): PyPI `fastmcp` **4.1.5**, uploaded 2026-11-01, licence Apache-2.0, Python >=3.10; pre-release 4.2.0a2 on 1 Nov; 3.4.x line still receiving patches (3.4.11 on 20 Oct). — [PyPI JSON](https://pypi.org/pypi/fastmcp/json); [FastMCP releases](https://github.com/PrefectHQ/fastmcp/releases)
- FastMCP major version jumped 2.x -> 3.x -> 4.x within ~a year (I did not read the changelogs; whether FastMCP 4 targets spec 2026-07-28 is not verified). — [FastMCP releases](https://github.com/PrefectHQ/fastmcp/releases)

**Existing open-source FHIR MCP servers**
- wso2/fhir-mcp-server: Apache-2.0, last push 2 Nov 2026, ~137 stars, not archived. — [GitHub search API](https://api.github.com/search/repositories?q=repo:wso2/fhir-mcp-server)
- PyPI `fhir-mcp-server` **0.11.0** uploaded 2026-10-29, Apache-2.0, Python >=3.12. — [PyPI JSON](https://pypi.org/pypi/fhir-mcp-server/json)
- wso2 server exposes any FHIR server/API to AI agents; supports stdio, SSE and Streamable HTTP; env `FHIR_SERVER_BASE_URL`; Docker image on GHCR; created June 2025; includes OAuth/SMART-style config (per directory listing). — [PyPI listing](https://pypi.org/project/fhir-mcp-server/); [search summary of repo](https://awesome.ecosyste.ms/projects/github.com%2Fwso2%2Ffhir-mcp-server)
- the-momentum/fhir-mcp-server: MIT, ~102 stars, **last push 2025-10-23** (i.e., ~12 months stale), not archived; full CRUD on FHIR resources, doc ingestion/semantic search, OAuth2, LOINC support (per Momentum blog / directory). — [GitHub search API](https://api.github.com/search/repositories?q=repo:the-momentum/fhir-mcp-server); [Momentum blog](https://www.themomentum.ai/blog/introducing-fhir-mcp-server-natural-language-interface-for-healthcare-data)
- Also exists: Eka Care's `eka-care/eka-mcp-sdk` ("Eka Care APIs over MCP"), last push 2026-10-09 (Indian health-tech vendor MCP). — [GitHub search API](https://api.github.com/search/repositories?q=eka-care+mcp)

### Inferences
- For a new build target spec **2026-07-28** with Streamable HTTP only (no SSE), OAuth resource-server pattern (RFC 9728 + RFC 8707 audience binding) with tenant-aware tokens; avoid Sampling/Roots/Logging/DCR in new code. Because the 2026-07-28 revision is stateless, MCP servers can scale horizontally behind a plain load balancer, which suits multi-tenant SaaS.
- Use the official `mcp` v2.x SDK (MIT) or FastMCP 4.x (Apache-2.0); both are permissive. Pin versions: the SDKs have moved fast (v2.0.0 to v2.3.0 in ~2 months).
- wso2/fhir-mcp-server is the better reference/base than the-momentum's; but since a hospital SaaS needs per-tenant authorisation, field-level redaction, audit and ABDM profiles, a purpose-built thin MCP server over the product's own FHIR/REST layer is likely safer than exposing a generic FHIR pass-through.

### Gaps
- Did not read the python-sdk v2 migration guide or FastMCP 4 changelog; so which SDK versions implement spec 2026-07-28 (vs 2025-11-25) is unverified.
- GitHub release pages omit the year; year inferred as 2026 (PyPI timestamps confirm for mcp/fastmcp).
- Not checked whether wso2/fhir-mcp-server supports SMART-on-FHIR/ABDM profiles or multi-tenant token mapping.

---

## 2. LLM serving: vLLM, SGLang, Ollama, llama.cpp

### Takeaway
vLLM 0.33.0 (2 Nov 2026) and SGLang 0.5.23 (30 Oct 2026) are both Apache-2.0 and are the production GPU servers; Ollama (MIT, v0.40.13) and llama.cpp (MIT, build b12052) are for dev/edge/CPU. Choose vLLM (default) or SGLang for multi-tenant production.

### Cited Findings
- vLLM PyPI **0.33.0**, uploaded 2026-11-02, Apache-2.0, Python >=3.11,<3.15; GitHub: v0.33.0 (2 Nov), v0.32.0 (18 Oct), v0.31.0 (5 Oct) — roughly fortnightly cadence. — [PyPI](https://pypi.org/pypi/vllm/json); [vLLM releases](https://github.com/vllm-project/vllm/releases)
- SGLang PyPI **0.5.23**, uploaded 2026-10-30, Apache-2.0 (licence text in PyPI metadata), Python >=3.11; GitHub releases page showed v0.5.22 (19 Oct) as latest (cached; PyPI is newer). — [PyPI](https://pypi.org/pypi/sglang/json); [SGLang releases](https://github.com/sgl-project/sglang/releases)
- Ollama GitHub: latest v0.40.13 (28 Oct), near-daily patch releases; repo licence MIT. PyPI client `ollama` 0.6.3 (2026-09-29, MIT). — [Ollama releases](https://github.com/ollama/ollama/releases); [GitHub search API](https://api.github.com/search/repositories?q=repo:ollama/ollama); [PyPI](https://pypi.org/pypi/ollama/json)
- llama.cpp: latest release tag b12052 (2 Nov 2026); licence MIT; ~133k stars. — [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases); [GitHub search API](https://api.github.com/search/repositories?q=repo:ggml-org/llama.cpp)
- Model repos explicitly state compatibility with vLLM and SGLang (e.g., Qwen3.8-27B card: "compatible with Hugging Face Transformers, vLLM, SGLang"). — [Qwen3.8-27B card](https://huggingface.co/Qwen/Qwen3.8-27B)
- Google ships QAT 4-bit variants in vLLM-friendly `w4a16-ct` (compressed-tensors) format and `q4_0` GGUF for llama.cpp/Ollama for Gemma 4. — [HF google org listing](https://huggingface.co/api/models?author=google&sort=createdAt&direction=-1&limit=40)

### Inferences
- Production multi-tenant: **vLLM** (continuous batching, prefix caching, LoRA multi-adapter, OpenAI-compatible API, widest model-day-0 support) as default; **SGLang** as strong alternative (RadixAttention prefix reuse helps when many requests share a large clinical system prompt/tool schema; structured output). These feature descriptions are from my background knowledge, not verified in this session.
- Dev/laptop/on-prem small clinic edge: Ollama or llama.cpp (GGUF). Avoid Ollama as the multi-tenant production gateway (single-node focus, no tenant-level scheduling/quotas).
- Per-tenant isolation should be done at the gateway layer (e.g., LiteLLM/Envoy + tenant IDs, separate KV prefix salting) rather than inside the engine.

### Gaps
- Did not verify multi-LoRA, prefix-cache tenant isolation, or structured-output feature status in vLLM 0.33 / SGLang 0.5.23 release notes.
- Did not benchmark; no throughput comparisons gathered.

---

## 3. Open-weights models (general + medical + Indian) as of Nov 2026

### Takeaway
The blueprint list is outdated. Current candidates: **Google Gemma 4** (Apache-2.0 since March 2026), **Qwen3.8-27B** (Apache-2.0, Aug 2026), **gpt-oss-120b/20b** (Apache-2.0, Aug 2025), **Mistral Small 4 119B** (Apache-2.0), **DeepSeek V4.x** (MIT, very large), **Meta Muse Glimmer 30B** (Apache-2.0; Meta's Llama brand has stalled at Llama 4) and **MedGemma 1.5 4B / MedGemma 27B** (gated, Google HAI-DEF terms, not Apache). Indian: **Sarvam-30B / Sarvam-105B** (Apache-2.0, Mar 2026), BharatGen Param2.

### Cited Findings
**General models**
- **Gemma 4** (Google DeepMind): sizes E2B, E4B, 12B, 26B-A4B (MoE), 31B (dense); multimodal (audio native on E2B/E4B/12B); 128K (small) / 256K (medium) context; 140+ languages; native function calling and system role; licence **Apache 2.0** (per model card). HF creation dates: 31B & 26B-A4B 2026-03-11; 12B 2026-05-23. — [gemma-4-31B-it card](https://huggingface.co/google/gemma-4-31B-it); [HF API 31B](https://huggingface.co/api/models/google/gemma-4-31B-it)
- Gemma 4 param counts: 31B = 31.27B, 26B-A4B = 25.8B total, 12B = 11.96B, E4B = 8.0B (with embeddings). QAT 4-bit variants officially published (q4_0 GGUF, w4a16 compressed-tensors) for 31B, 26B-A4B, 12B, E4B, E2B. — [HF API 26B-A4B](https://huggingface.co/api/models/google/gemma-4-26B-A4B-it); [HF API 12B](https://huggingface.co/api/models/google/gemma-4-12B-it); [google org listing](https://huggingface.co/api/models?author=google&sort=createdAt&direction=-1&limit=40)
- Google's release log lists Gemma 4 on March 31, 2026 (E2B, E4B, 31B, 26B A4B) and Gemma 4 12B on June 3, 2026; Wikipedia says Apache 2.0. — [Gemma releases (via search)](https://ai.google.dev/gemma/docs/releases?hl=en); [Wikipedia Gemma](https://en.wikipedia.org/wiki/Gemma_(language_model)) (HF dates differ slightly from Google's: HF repo 2026-03-11 vs launch 31 Mar — likely repo created before public launch)
- **Qwen**: current generation is **Qwen3.8** (Aug 2026), following Qwen3.5 (Feb-Mar 2026) and Qwen3.6 (Apr 2026). Qwen3.8-27B: dense, 27.8B params, vision-language, Apache-2.0 (created 2026-08-05); thinking mode toggle (`reasoning_effort`). — [Qwen3.8-27B card](https://huggingface.co/Qwen/Qwen3.8-27B); [HF API](https://huggingface.co/api/models/Qwen/Qwen3.8-27B)
- Qwen3.8-Flash-Next (~180B, licence "qwen-community-1.0", i.e., NOT plain Apache) and Qwen3.8-2.4T-A95B (2.45T params, licence "qwen3.8-max") — non-Apache custom licences. Qwen3.6-27B and Qwen3.6-35B-A3B exist (Apr 2026); Qwen3.5 had 0.8B-397B-A17B sizes; Qwen3.5 GPTQ-Int4 builds are published. — [HF API Flash-Next](https://huggingface.co/api/models/Qwen/Qwen3.8-Flash-Next); [HF API 2.4T](https://huggingface.co/api/models/Qwen/Qwen3.8-2.4T-A95B); [Qwen org listing](https://huggingface.co/api/models?author=Qwen&sort=createdAt&direction=-1&limit=25)
- **gpt-oss** (OpenAI): gpt-oss-120b (116.8B total params) and gpt-oss-20b (20.9B), Apache-2.0, created 2025-08-04 (older than most others; ~15 months old). `gpt-oss-safeguard` 20b/120b (Apache-2.0, Sep 2025); `openai/privacy-filter` (Apache-2.0, 2026-04-17) — potentially useful for PHI redaction (not evaluated). — [HF API 120b](https://huggingface.co/api/models/openai/gpt-oss-120b); [HF API 20b](https://huggingface.co/api/models/openai/gpt-oss-20b); [OpenAI HF listing](https://huggingface.co/api/models?author=openai&sort=createdAt&direction=-1&limit=10)
- **Meta**: last "Llama"-branded open weights in HF org `meta-llama` are Llama 4 Scout/Maverick (April 2025, "other" Llama 4 Community licence) and Llama 3.3 70B (Nov 2024, Llama 3.3 licence). Meta's newer open releases are under `meta-models` org: **Muse-Glimmer-30B** (Aug 2026, Apache-2.0) and **Muse-Spark-Open-816B-A42B** (Sep 2026, gated "other" licence). A secondary blog claims a "Llama 4.5" (June 2026) — I could not find any such repo on HF and the source is unsourced; treat as unverified. — [meta-llama listing](https://huggingface.co/api/models?author=meta-llama&sort=createdAt&direction=-1&limit=12); [meta-models listing](https://huggingface.co/api/models?author=meta-models&sort=createdAt&direction=-1&limit=12); [Llama 4.5 claim (secondary)](https://the-agent-report.com/2026/06/meta-llama-4-5-open-weight-refresh/)
- **Mistral**: Mistral-Small-4-119B-2603 (119.4B params, Apache-2.0); Mistral-Medium-3.5-128B (127.7B, licence "other" — non-Apache); Mistral-Large-4-1T-A52B (Sep 2026, "other" licence). — [HF API Small 4](https://huggingface.co/api/models/mistralai/Mistral-Small-4-119B-2603); [HF API Medium 3.5](https://huggingface.co/api/models/mistralai/Mistral-Medium-3.5-128B); [mistralai listing](https://huggingface.co/api/models?author=mistralai&sort=createdAt&direction=-1&limit=15)
- **DeepSeek**: V4.1-Flash (763B params, MIT, 2026-09-10; refreshed 1017 build Oct 17), V4-Pro-0813 (1.65T params, MIT). Too large for typical hospital GPU budgets. — [HF API V4.1-Flash](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash); [HF API V4-Pro](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4-Pro-0813)

**Medical models**
- **MedGemma**: HF repos: `medgemma-4b-it` (2025-05-19), `medgemma-27b-text-it` (2025-05-19), `medgemma-27b-it` (multimodal, 2025-07-09; 27.4B params), **`medgemma-1.5-4b-it`** (2026-01-07; 4.3B params). Google's release log dates MedGemma 1.5 4B to January 13, 2026. All built on Gemma 3 (no Gemma-4-based MedGemma found as of Nov 2026). Gated (auto-approval) with licence "health-ai-developer-foundations". — [HF search medgemma](https://huggingface.co/api/models?search=medgemma&limit=30); [HF API 1.5-4b](https://huggingface.co/api/models/google/medgemma-1.5-4b-it); [Gemma release log via search](https://ai.google.dev/gemma/docs/releases?hl=en)
- **MedGemma licence = Health AI Developer Foundations (HAI-DEF) Terms, NOT Apache**: use/modify/distribute allowed under the agreement; "Clinical Use" is defined (diagnosis/treatment incl. research) and distributors must "seek Health Regulatory Authorization" where applicable; users must not use it in a way that could cause a regulator to deem Google a medical-device "manufacturer"; downstream agreements must flow down restrictions; modified files need prominent notices; "AS IS", no medical advice; California law. Commercial use not separately restricted in text. — [HAI-DEF Terms](https://developers.google.com/health-ai-developer-foundations/terms)
- Secondary guide claims MedGemma 1.5 ~91% on MedQA and calls it the leading open-weight medical model; this is a vendor-style blog, not peer-reviewed. — [Knack guide](https://www.knack.com/blog/healthcare-llm/)
- No Meditron/BioMistral successors were surfaced in my searches; general frontier-ish open models (Qwen3.8, Gemma 4, gpt-oss-120b) are now commonly used for clinical text, with MedGemma as the specialist (esp. multimodal/imaging). — [SiliconFlow guide (secondary)](https://www.siliconflow.com/articles/best-open-source-llm-for-healthcare)

**Indian models**
- **Sarvam-105B** (106.0B params) and **Sarvam-30B** (32.2B), Apache-2.0, released on HF 2026-03-03; FP8 and GGUF builds (2026-03-20..26). Earlier: Sarvam-M (May 2025), Sarvam-Translate (GPL-3.0!, Jun 2025). — [HF API sarvam-30b](https://huggingface.co/api/models/sarvamai/sarvam-30b); [HF API sarvam-105b](https://huggingface.co/api/models/sarvamai/sarvam-105b); [sarvamai listing](https://huggingface.co/api/models?author=sarvamai&sort=createdAt&direction=-1&limit=15)
- **BharatGen** (Govt-backed): Param2-17B-A2.4B-Thinking (17.2B params, Feb 2026; no licence tag on HF), Param-1 (5B/7B, Dec 2025), AyurParam (Apache-2.0, Aug 2025). — [BharatGen listing](https://huggingface.co/api/models?author=bharatgenai&sort=createdAt&direction=-1&limit=10)
- **Krutrim** (Ola): Krutrim-1/2-instruct, Chitrapathak etc. all licence "other" (custom) — latest Mar 2026; not Apache. — [Krutrim listing](https://huggingface.co/api/models?author=krutrim-ai-labs&sort=createdAt&direction=-1&limit=8)
- **AI4Bharat** recent: IndicBERT-v3 (MIT; 270M/1B/4B), IndicTrans3-beta (CC-BY-4.0), BrailleQwen-2B. — [AI4Bharat listing](https://huggingface.co/api/models?author=ai4bharat&sort=createdAt&direction=-1&limit=20)

**VRAM estimates (my arithmetic: weights = params x 0.5 B @4-bit, x 1 B @8-bit; add ~15-25% for KV cache/activations; MoE needs ALL experts resident)**
| Model | Params (HF API) | ~4-bit weights | ~8-bit weights | Practical GPU |
|---|---|---|---|---|
| MedGemma 1.5 4B | 4.3B | ~2.2 GB | ~4.3 GB | 1x 16 GB |
| Gemma 4 12B | 12.0B | ~6 GB | ~12 GB | 1x 24 GB |
| gpt-oss-20b | 20.9B | ~11-13 GB (native MXFP4) | ~21 GB | 1x 24 GB |
| Qwen3.8-27B | 27.8B | ~14 GB | ~28 GB | 1x 24-40 GB (4b) / 1x 48-80 GB (8b) |
| MedGemma 27B | 27.4B | ~14 GB | ~27 GB | same as above |
| Gemma 4 31B | 31.3B | ~16 GB | ~31 GB | 1x 24-48 GB (4b) / 1x 80 GB (8b) |
| Sarvam-30B | 32.2B | ~16 GB | ~32 GB | same |
| gpt-oss-120b | 116.8B | ~60-65 GB (native MXFP4) | ~117 GB | 1x 80 GB (tight) / 2x 80 GB |
| Mistral Small 4 119B | 119.4B | ~60 GB | ~119 GB | 1-2x 80 GB |
| Sarvam-105B | 106.0B | ~53 GB | ~106 GB | 1-2x 80 GB |
| DeepSeek V4.1-Flash | 763B | ~380 GB | ~763 GB | multi-node 8x H200 class |

### Inferences
- Replacement mapping for the blueprint: Llama 3.3 70B -> **Gemma 4 31B / Qwen3.8-27B** (dense, single-GPU, Apache-2.0) or **gpt-oss-120b / Mistral Small 4** (bigger MoE); Qwen 2.5 72B -> **Qwen3.8-27B**; Meditron/BioMistral -> **MedGemma 1.5 4B / MedGemma 27B** (but with HAI-DEF terms) or just a general model plus RAG on guidelines.
- For Indic-language clinical summarisation/translation, **Sarvam-30B/105B** (Apache-2.0) and Gemma 4 (140+ languages) are the natural candidates; need an in-house eval on Hindi/Hinglish/regional clinical notes — no verified benchmark found.
- Licence ranking for a commercial SaaS: Apache-2.0 / MIT (Gemma 4, Qwen3.8-27B, gpt-oss, Mistral Small 4, Sarvam, DeepSeek) > HAI-DEF gated (MedGemma; flow-down + regulatory language) > custom licences (Qwen Flash-Next/Max, Mistral Medium/Large, Meta Muse Spark Open, Krutrim). Read each custom licence before use.
- MoE models (gpt-oss-120b, Mistral Small 4, Gemma 4 26B-A4B) give much lower per-token compute than their VRAM suggests but still need full weights resident.

### Gaps
- Qwen3.5/3.6/3.8 intermediate sizes (e.g., 35B-A3B, 122B-A10B) and their licences beyond those listed were not individually verified.
- Muse Glimmer / Muse Spark Open licence text not read; "Llama 4.5" unverified; no primary Meta announcement fetched.
- No independent clinical benchmark (MedQA/HealthBench/Indian clinical) comparing these open models was found from a primary source.
- MedGemma card (gated) text couldn't be read; MedGemma 27B licence terms taken from HF metadata + HAI-DEF terms.
- Mistral/Qwen custom licence texts not read.

---

## 4. Indic ASR for ambient scribing

### Takeaway
Best open options: **AI4Bharat IndicConformer 600M multilingual** (MIT, 22 languages, hybrid CTC/RNNT) and Whisper-family (large-v3 MIT/Apache; poor out-of-box on Indic). Best commercial/API: **Sarvam Saaras v4** (default; 22 Indic langs + English, code-mix output mode, diarization via Batch only). faster-whisper 1.2.1 (MIT) hasn't had a release since Oct 2025. pyannote community-1 is CC-BY-4.0 (gated by HF token).

### Cited Findings
- **Whisper**: `openai/whisper-large-v3` (2023-11-07, Apache-2.0 tag) and `whisper-large-v3-turbo` (2024-10-01, MIT). openai-whisper PyPI 20250625 (MIT). — [OpenAI HF listing](https://huggingface.co/api/models?author=openai&sort=createdAt&direction=-1&limit=10); [PyPI](https://pypi.org/pypi/openai-whisper/json)
- **faster-whisper** 1.2.1 (2025-10-31, MIT) — last release 12 months ago though repo pushed 2 Nov 2026; CTranslate2 4.8.4 (2026-10-25, MIT); WhisperX 3.8.6 (2026-05-25, BSD-2-Clause). — [PyPI faster-whisper](https://pypi.org/pypi/faster-whisper/json); [PyPI ctranslate2](https://pypi.org/pypi/ctranslate2/json); [PyPI whisperx](https://pypi.org/pypi/whisperx/json); [GitHub search API](https://api.github.com/search/repositories?q=repo:SYSTRAN/faster-whisper)
- Whisper on Indic: base Whisper "performs poorly on Indic languages out of the box" with zero-shot WER >100% in some Hindi cases (Vividh-ASR study); a community Whisper-large-v3 Hindi fine-tune gets ~18.2 WER on FLEURS (read speech). On the Vaani Hindi benchmark all systems degrade on code-switched tokens. — [Vividh-ASR blog](https://huggingface.co/blog/adalat-ai/vividh-benchmark); [KhushiDS large-v3 Hindi](https://huggingface.co/KhushiDS/whisper-large-v3-Hindi); [Vaani benchmark paper](https://arxiv.org/pdf/2606.21408)
- **AI4Bharat IndicConformer** `indic-conformer-600m-multilingual`: MIT; 22 official Indian languages; hybrid CTC+RNNT Conformer ~600M params; Hindi WER 13.2 on ARTPARK-IISc Vaani benchmark; `trust_remote_code`; card does not mention code-mixing; card recommends a newer "Indic-Transcribe" (could not be traced to AI4Bharat — a search found only third-party Canary-based fine-tunes). ~196k downloads. — [HF card](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual); [Search on Indic-Transcribe](https://huggingface.co/bodhan-ai/indic-transcribe-core)
- AI4Bharat 2026 ASR releases target Bhili (MIT: conformer-600m, canary-1.2b, nemotron-600m); IndicConformerASR GitHub repo (MIT) last push June 2025. — [AI4Bharat listing](https://huggingface.co/api/models?author=ai4bharat&sort=createdAt&direction=-1&limit=20); [GitHub search API](https://api.github.com/search/repositories?q=repo:AI4Bharat/IndicConformerASR)
- **Sarvam Saaras v4** (default, recommended) and v3 via `/speech-to-text`: 23 languages (22 Indian + English; v4 adds global English), modes `transcribe|translate|verbatim|translit|codemix`; code-mixed speech supported; diarization only in Batch API (up to 2 h/file); REST max 30 s; WebSocket streaming exists; chunk-level timestamps only; keyterm prompting (<=50 terms, v4 only); 8 kHz telephony optimised. Saarika v2.5 is legacy. Hosted API (not open weights). — [Sarvam Saaras docs](https://docs.sarvam.ai/api-reference-docs/getting-started/models/saaras.md); [Sarvam STT search](https://docs.sarvam.ai/changelog.md)
- Sarvam pricing page not retrieved; Sarvam also has Bulbul V4 TTS (Oct 2026). — [Sarvam changelog](https://docs.sarvam.ai/api-reference-docs/changelog.md)
- **pyannote**: PyPI `pyannote-audio` 4.0.7 (2026-06-30); GitHub repo MIT; new pipeline `pyannote/speaker-diarization-community-1` is **CC-BY-4.0**, requires accepting HF user conditions + token, can run fully offline after git-lfs clone; DER e.g., AMI-IHM 17.0, AMI-SDM 19.9, DIHARD3 20.2 (improved over 3.1); paid `precision-2` on pyannoteAI is separate. — [PyPI](https://pypi.org/pypi/pyannote-audio/json); [GitHub search API](https://api.github.com/search/repositories?q=repo:pyannote/pyannote-audio); [community-1 card](https://huggingface.co/pyannote/speaker-diarization-community-1)
- **Bhashini** (govt platform) hosts 300+ models incl. ASR/NMT/TTS across 22 scheduled languages (Microsoft page); a developer blog claims free for non-commercial / discounted commercial use — official commercial terms NOT confirmed. — [Microsoft Bhashini page](https://www.microsoft.com/en-in/aifirstmovers/bhashini); [Caller.Digital blog (secondary)](https://caller.digital/blog/open-source-voice-ai-india-sarvam-ai4bharat-bhasini-2026)
- Other open ASR in 2026: Qwen3-ASR-0.6B/1.7B (Apache-2.0, 2026-06-26) — Indic coverage not verified; Gemma 4 E2B/E4B/12B accept audio natively. — [Qwen listing](https://huggingface.co/api/models?author=Qwen&sort=createdAt&direction=-1&limit=25); [Gemma 4 card](https://huggingface.co/google/gemma-4-31B-it)
- Eka Care publishes an "Eka Medical ASR Evaluation Dataset" on AIKosh (Indian medical ASR eval) — details not fetched. — [AIKosh dataset](https://aikosh.indiaai.gov.in/home/datasets/details/eka_medical_asr_evaluation_dataset.html)

### Inferences
- For a hospital scribe, a pragmatic stack: VAD + (IndicConformer or Saaras) for ASR, pyannote community-1 for diarization (offline, CC-BY attribution), then an LLM (Gemma 4 / Qwen3.8 / Sarvam) for structuring. Doctor-patient Hinglish code-mixing is the hard case; Whisper large-v3 without fine-tuning is risky.
- Self-hosting Saaras isn't possible (API only), so if data must stay in a VPC/in India, IndicConformer/fine-tuned Whisper are the open choices; Sarvam is an Indian-hosted API but DPDP processor terms/data residency must be contractually confirmed (not verified here).
- Medical terminology (drug brand names, Latin abbreviations) will need keyterm biasing (Saaras v4) or LM rescoring/fine-tuning.

### Gaps
- No head-to-head WER on IndicVoices / Hinglish clinical audio for Whisper-v3 vs IndicConformer vs Saaras from a primary source.
- Sarvam pricing, data-retention policy and hosting region not retrieved.
- Bhashini official terms of service and rate limits not confirmed.
- AI4Bharat's "Indic-Transcribe" model could not be verified as an AI4Bharat release.

---

## 5. Existing open-source clinical scribe: ohcnetwork (CARE)

### Takeaway
The repo `ohcnetwork/care_scribe` is no longer there (404); the CARE EMR now has **`ohcnetwork/care_eka_scribe_fe`** (MIT), a frontend plugin that delegates audio transcription + structuring to Eka Care's hosted EkaScribe API.

### Cited Findings
- `care_scribe` URL returns 404; GitHub search of org `ohcnetwork` for "scribe" returns only `care_eka_scribe_fe` (MIT, last push 2026-10-18, 0 stars). — [GitHub search API](https://api.github.com/search/repositories?q=scribe+org:ohcnetwork)
- How it works: floating mic button records doctor-patient conversation; audio sent via `@eka-care/ekascribe-ts-sdk` to Eka Care's voice API, which transcribes and returns structured JSON according to a dynamic template built from the current CARE questionnaire form's fields; values are fuzzy-matched to form fields, filled in, highlighted and scrolled into view for clinician review. Runs as Vite Module Federation remote loaded by `care_fe` via `Scribe` extension point; needs an EkaScribe access token from Eka's developer console; TypeScript/React 19. — [care_eka_scribe_fe README](https://github.com/ohcnetwork/care_eka_scribe_fe)
- Open Healthcare Network describes "CARE Scribe" as its voice-based documentation tool alongside CARE EMR. — [aggregator profile](https://aeroleads.com/c/ohcnetwork)

### Inferences
- The pattern worth copying is "voice-to-form": generate a JSON template from the target form schema, have the LLM fill it, fuzzy-match to field IDs, and require clinician review/accept (human-in-the-loop). The ASR/LLM themselves are NOT open in this repo — it's an API wrapper — so it is a UI pattern reference, not an open ASR pipeline.

### Gaps
- The original `care_scribe` backend (possibly Whisper/OpenAI-based, earlier plug-in) could not be inspected; its prior architecture is unverified.
- Eka's backend ASR/LLM, pricing and data residency not examined.

---

## 6. Clinical coding: ICD-10/11, SNOMED CT, Snowstorm, ICD-11 API

### Takeaway
Snowstorm 11.0.0 (2 Sep 2026; Java 25, Elasticsearch-based) is Apache-2.0 software, but SNOMED CT content needs an affiliate licence (free for India via NRCeS/MLDS). WHO ICD-11 API (v2.6) can be self-hosted via Docker and has a more open licence as of the 2026 release; embedded coding tool v1.8.

### Cited Findings
- Snowstorm latest release **11.0.0** (2 Sep 2026): major release, Java 25 upgrade and FHIR improvements; Docker uses Elasticsearch 8.19.8. — [Snowstorm releases](https://github.com/IHTSDO/snowstorm/releases)
- Snowstorm code licence: Apache License 2.0 (LICENSE.md text). GitHub's API reports NOASSERTION because of the filename. Last push 2 Nov 2026; ~399 stars. — [LICENSE.md](https://raw.githubusercontent.com/IHTSDO/snowstorm/master/LICENSE.md); [GitHub search API](https://api.github.com/search/repositories?q=repo:IHTSDO/snowstorm)
- SNOMED CT in India: India is a SNOMED International member since March 2014; NRCeS (C-DAC Pune, MoHFW) provides free tools/SDKs, licences and training; Government of India procured a licence offered to professionals/vendors at no cost (undated page); affiliate licences via MLDS (https://mlds.ihtsdotools.org/#/landing/IN); NRCeS publishes a 2023 affiliate licence agreement. International Edition releases continue (June and August 2026 listed). — [SNOMED India member page](https://www.snomed.org/members/india); [SNOMED India Confluence](https://conf.spaces.snomed.org/wiki/spaces/MCC/pages/131343368/India); [NRCeS affiliate licence 2023](https://www.nrces.in/download/files/pdf/SNOMED%20CT%20Affiliate%20License%20Agreement%202023.pdf); [SNOMED forum June 2026](https://forums.snomed.org/t/june-2026-snomed-ct-international-edition-release-available/1373)
- WHO ICD API: REST API; ICD-11 versions updated with 2026 release and more languages; API version 2.6; "ICD-11 License is now more open"; can run locally as Docker container (ARM supported), Windows service or systemd; Embedded Classification Tool v1.8; requires registration for API keys. — [WHO ICD API](https://icd.who.int/icdapi)

### Inferences
- Recommended auto-coding architecture (from general knowledge, not from a cited source): LLM extracts clinical entities -> candidate retrieval from Snowstorm (SNOMED, ECL queries/description search) and ICD-11 API/ICD-10 table with embedding re-ranking -> LLM/rules choose code -> coder confirms. Never let an LLM emit a bare code unverified: validate each code against the terminology server. For Indian billing/claims (insurance, PMJAY), ICD-10 remains practical; ICD-11 is for ABDM/forward readiness (ABDM/NRCeS position not verified here).
- Self-host Snowstorm per environment (needs Elasticsearch + Java 25 + loaded SNOMED RF2 package under affiliate licence); mention ES licensing separately (not researched).

### Gaps
- ICD-11 licence exact terms (CC BY-ND 3.0 IGO historically, "more open" now) not read; ICD-10 data licence for commercial use not checked.
- India's ABDM/NHA code-system mandates (ICD-10 vs ICD-11 vs SNOMED, NRCeS India extension) not verified for 2026.
- Current NRCeS affiliate terms not confirmed for 2026 (search says undated).

---

## 7. Drug interaction data and Indian drug master

### Takeaway
No fully open, commercially usable, severity-graded DDI dataset was confirmed. DrugBank is non-commercial/academic only without a commercial agreement (academic downloads currently paused); NLM RxNav's interaction API was retired in 2024; RxNorm is US-only. Indian brand data is available only via unofficial Kaggle-style datasets (licences unverified) or paid vendors; the Indian government has said it has no plan for a central brand database.

### Cited Findings
- DrugBank: non-commercial use permitted; commercial use requires separate agreement; academic datasets are CC BY-NC 4.0 and academic downloads were "temporarily paused" per the latest release page. — [DrugBank FAQ](https://www.drugbank.ca/faqs); [DrugBank release page](https://go.drugbank.com/releases/5-1-19)
- NLM RxNav Drug-Drug Interaction API was discontinued on/about 2 Jan 2024 (it used a limited subset of DrugBank data, with no severity). — [DrugBank blog on NIH discontinuation](https://blog.drugbank.com/nih-discontinues-their-drug-interaction-api/); [RxNav](https://www.lhncbc.nlm.nih.gov/RxNav/index.html)
- DDInter 2.0: open-access DDI database; 2,310 drugs, 302,516 DDI records (paper); commercial-use licence terms not stated in results. — [PMC article](https://pmc.ncbi.nlm.nih.gov/articles/PMC11701621/)
- Indian brand datasets: Kaggle "Indian Medicine Data" (~195,605 rows, product name + salt composition) and "A-Z Medicine Dataset of India" (250k+ allopathy medicines with pricing; yearly updates) — licence not shown. Eka Care's "Indian Drug MCQA" (1,512 Q&A on Indian brand names, MIT) on AIKosh. HF `dmedhi/indian-medicines` also exists. — [Baselight mohneesh7](https://baselight.app/u/kaggle/dataset/mohneesh7_indian_medicine_data); [Baselight shudhanshusingh](https://baselight.app/u/kaggle/dataset/shudhanshusingh_az_medicine_dataset_of_india); [AIKosh Indian Drug MCQA](https://aikosh.indiaai.gov.in/home/datasets/details/indian_drug_mcqa.html); [HF dmedhi dataset](https://huggingface.co/datasets/dmedhi/indian-medicines/tree/refs%2Fconvert%2Fparquet/default)
- Lok Sabha answer: no plan for a centralised drug brand database because the Drugs & Cosmetics Act does not provide for recognising brand names. — [RAPS discussion citing it](https://connect.raps.org:443/discussion/india-plans-comprehensive-drug-database?hlmlt=VT) (secondary; also [eparlib.nic.in doc](https://eparlib.nic.in/bitstream/123456789/599311/1/102273.pdf))
- Eka Care MCP SDK/server exposes Eka's APIs (which include Indian drug/medication data) over MCP — commercial/API relationship required. — [eka-care/eka-mcp-sdk](https://api.github.com/search/repositories?q=eka-care+mcp); [Eka MCP server listing](https://mcpservers.org/fr/servers/eka-care/eka_mcp_server)

### Inferences
- A defensible approach: build a **drug master** keyed on generic/INN + ATC (WHO ATC is licensed — not researched) with brand -> composition mapping curated from (a) licensed commercial feed (e.g., CIMS/MIMS India, Eka, 1mg — not evaluated), (b) CDSCO approvals/NPPA price lists (public but unstructured; not verified) and (c) the Kaggle datasets only as bootstrap after licence/scraping-legality review. Map generics to RxNorm/SNOMED (CT has a pharmaceutical/biologic product hierarchy in the India extension — unverified) for interaction lookups.
- For interaction severity use a licensed source (DrugBank commercial, First Databank, Lexicomp, Medi-Span, etc.) or DDInter after confirming licence; use LLM only to explain, never as the source of truth.

### Gaps
- DDInter licence, openFDA label-based interaction text usability, CDSCO/NPPA/CDSCO-SUGAM machine-readable data, WHO ATC licence terms, and the licence of the Kaggle datasets were not verified.
- No India-specific open DDI dataset found.

---

## 8. Early warning scores: NEWS2, qSOFA, SOFA, MEWS, PEWS

### Takeaway
NEWS2 (RCP 2017), qSOFA and SOFA have standard published formulas (below). MEWS and PEWS have many local variants and I could not source a canonical table; there are small MIT-licensed NEWS2 libraries but nothing mature to adopt wholesale — implementing in-house with unit tests against the RCP chart is recommended.

### Cited Findings
- NEWS2: six parameters (RR, SpO2, systolic BP, pulse, consciousness/new confusion, temperature) plus +2 for supplemental oxygen; scores summed; new confusion scores 3; "adjunct to clinical judgment". Chart PDFs (Chart 1 scoring, Chart 2 thresholds/triggers) are at RCP. — [RCP NEWS2](https://www.rcp.ac.uk/improving-care/resources/national-early-warning-score-news-2/)
- NEWS2 values (MDCalc): RR <=8 =3, 9-11 =1, 12-20 =0, 21-24 =2, >=25 =3; Room air 0 / supplemental O2 +2; Temp <=35.0 =3, 35.1-36.0 =1, 36.1-38.0 =0, 38.1-39.0 =1, >=39.1 =2; SBP <=90 =3, 91-100 =2, 101-110 =1, 111-219 =0, >=220 =3; Pulse <=40 =3, 41-50 =1, 51-90 =0, 91-110 =1, 111-130 =2, >=131 =3; Consciousness alert =0, new confusion/V/P/U =3. — [MDCalc NEWS2](https://www.mdcalc.com/calc/10083/national-early-warning-score-news-2)
- SOFA (0-24): CNS GCS 15=0, 13-14=1, 10-12=2, 6-9=3, <6=4; Cardiovascular MAP>=70=0, <70=1, dopamine<=5 or any dobutamine=2, dopamine>5 or epi/norepi<=0.1=3, dopamine>15 or epi/norepi>0.1=4; Respiratory PaO2/FiO2 >=400=0, <400=1, <300=2, <200 with ventilation=3, <100 with ventilation=4; Platelets >=150=0, <150=1, <100=2, <50=3, <20=4; Bilirubin <1.2=0, 1.2-1.9=1, 2.0-5.9=2, 6.0-11.9=3, >12=4; Renal creatinine <1.2=0, 1.2-1.9=1, 2.0-3.4=2, 3.5-4.9 (or UO<500 ml/d)=3, >5.0 (or UO<200 ml/d)=4. — [Wikipedia SOFA](https://en.wikipedia.org/wiki/SOFA_score)
- qSOFA (0-3): SBP <=100, RR >=22, altered mentation (GCS <=14), 1 point each; >=2 flags higher risk; poorly sensitive but reasonably specific; SIRS may screen better. — [Wikipedia SOFA](https://en.wikipedia.org/wiki/SOFA_score)
- Generic EWS description: most score up to 3 per parameter and sum; single-parameter extreme can trigger escalation; MEWS and PEWS (patients <16, different ranges) exist; NEWS developed by RCP in 2012, NEWS2 in 2017. — [Wikipedia Early warning score](https://en.wikipedia.org/wiki/Early_warning_score)
- Open-source NEWS2 implementations (all tiny): `kscarlett/news2` (Go, MIT, pushed 2026-07-23), `Eddiegah/harbinger` (MIT, 2026-09-02), `bairdj/news2` (MIT, 2022), `abusuraihsakhri/news2-qsofa-early-warning` (MIT, 2026-10-13), `naidihr/NEWS2` (licence NOASSERTION). All low-star, low-trust. — [GitHub search API NEWS2](https://api.github.com/search/repositories?q=NEWS2+early+warning+score)

### Inferences (from my background knowledge, NOT verified by a source in this session — validate against the RCP chart before shipping)
- NEWS2 SpO2 Scale 1: <=91 =3, 92-93 =2, 94-95 =1, >=96 =0. Scale 2 (target 88-92%, hypercapnic failure, clinician-prescribed): <=83 =3, 84-85 =2, 86-87 =1, 88-92 =0 (or >=93 on air =0), 93-94 on O2 =1, 95-96 on O2 =2, >=97 on O2 =3. ACVPU: only 'A' = 0; C/V/P/U = 3.
- NEWS2 triage: aggregate 0 = low; 1-4 low (ward-based response); any single parameter scoring 3 = low-medium (urgent ward-based response); 5-6 = medium (urgent response, key threshold); >=7 = high (emergency response). Standard approach recommended in RCP chart 2.
- Sepsis-3: sepsis = suspected infection + acute SOFA increase >=2 from baseline.
- MEWS (Subbe 2001) and PEWS (e.g., Brighton PEWS, Monaghan) have multiple variants; Indian paediatric units use diverse local versions — hospital should pick one explicit variant per ward type and version it. Not verified.
- Design: implement as pure functions with versioned parameter tables + unit tests (golden cases), expose as FHIR `RiskAssessment`/`Observation` and as an MCP tool; treat as deterministic CDS (not LLM-computed). Have the LLM only explain results. Missing vitals must produce "incomplete" not zero.

### Gaps
- No primary-source table (RCP PDF, original MEWS/PEWS papers) was fetched; SpO2 Scale 2 details and thresholds above are unverified.
- No credible, widely-used FHIR-native open-source EWS library found (search for FHIR/CDS-Hooks NEWS2 returned nothing).

---

## 9. Regulatory caution: CDS as medical device, DPDP, hosted LLM APIs, Claude on Bedrock India

### Takeaway
CDSCO finalised its Medical Device Software guidance (CDSCO/MD/GD/MDSW/01/2026) on 21 July 2026: diagnostic/monitoring/CDS software can be a regulated medical device under MDR-2017. DPDP's cross-border and most duty provisions start 12/13 May 2027. **Claude Opus 5, Sonnet 5 and Haiku 4.5 are available on Amazon Bedrock via an India-only inference profile (`in.` prefix) routing between Mumbai (ap-south-1) and Hyderabad (ap-south-2) — added 29 Sep 2026.**

### Cited Findings
**Bedrock India / Claude**
- AWS post (29 Sep 2026): India geographic inference profile now includes Claude Opus 5 (`in.anthropic.claude-opus-5`), Claude Sonnet 5 (`in.anthropic.claude-sonnet-5`) and Claude Haiku 4.5 (`in.anthropic.claude-haiku-4-5-20251001-v1:0`); requests route only between ap-south-1 and ap-south-2; "keeps inference within India"; customer data not stored in destination Region and stays in source Region; CloudWatch/CloudTrail logs in source Region only; Bedrock uses zero-data-retention by default (no storing of inputs/outputs); certain models may require human review by AWS if classifiers flag content (see Data retention section of Bedrock guide). — [AWS ML blog: Bedrock expands Claude model availability to India](https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-expands-claude-model-availability-to-india-cross-region-inference/)
- Earlier India access to Claude was via Global cross-Region inference (processing anywhere worldwide) — not residency-safe; AWS tells data-residency customers to call `GetInferenceProfile` from each source region and verify destination Regions rather than trusting the ID prefix. — [AWS: Access Claude in India with Global CRIS](https://aws.amazon.com/blogs/machine-learning/access-anthropic-claude-models-in-india-on-amazon-bedrock-with-global-cross-region-inference); [Bedrock inference profiles doc](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html)
- Whether Claude models are available as *in-region on-demand* (non-profile) in ap-south-1 was not confirmed (Bedrock now documents regional availability on per-model pages that I could not load). — [Bedrock model regions page (redirects)](https://docs.aws.amazon.com/bedrock/latest/userguide/models-regions.html)
- Secondary report claims Anthropic has started processing Claude requests in India (first-party) — unverified. — [lapaasvoice](https://lapaasvoice.com/anthropic-starts-processing-claude-requests-in-india/)

**DPDP**
- DPDP Rules notified Nov 2025; phases: immediate (13 Nov 2025; DPB set-up etc.), **12 Nov 2026** (consent-manager registration, Rule 4), **12 May 2027** (most duties incl. notice/consent, security safeguards, breach notification, retention/erasure, children's data, cross-border transfer rules). Other sources give 13 May 2027 / 13-14 Nov 2025 — minor date conflicts. — [AZB Partners](https://www.azbpartners.com/bank/indias-digital-personal-data-protection-act-phased-rollout-and-key-compliance-milestones/); [S&R summary](https://ssrana.in/articles/meity-notifies-final-digital-personal-data-protection-rules-2025/); [Mondaq](https://www.mondaq.com/india/privacy-protection/1708830/dpdp-act-compliance-mandate)
- DPDP cross-border regime is permissive-by-default (transfers allowed except to government-restricted countries; Significant Data Fiduciaries may face extra localisation); sectoral stricter laws prevail; rule numbers vary by source (13/14/15). A Sept 2026 analysis says transfer provisions not yet operational. No health-specific transfer rule found. — [King Stubb & Kasiva](https://ksandk.com/data-protection-and-data-privacy/cross-border-data-transfers-dpdp-act-india/); [BDO India](https://www.bdo.in/getmedia/87552494-85d2-4edb-95da-0f118fdb3052/The-DPDP-Rules_BDO-India_2025.pdf)

**Medical device**
- CDSCO "Guidance Document on Medical Device Software" Doc No. CDSCO/MD/GD/MDSW/01/2026, issued 21 July 2026 (draft was 21 Oct 2025): standalone diagnostic, monitoring and CDS software regulated as Medical Device Software under Medical Devices Rules 2017; classification by medical purpose, significance of output for decisions and condition severity; software that diagnoses/monitors/predicts/screens or directly informs treatment gets more scrutiny; AI-enabled MDSW must show performance in representative populations/settings; diagnostic CDS in facilities should support ABDM-compliant record generation. Commentary differs on how much is new; manufacturer attribution (developer/host/marketer) unresolved. — [Cyril Amarchand blog](https://corporate.cyrilamarchandblogs.com/2026/01/medical-device-as-software-has-cdsco-guidance-changed-the-rules/); [Nishith Desai](https://nishithdesai.com/research-and-articles/hotline/pharma-healthcare-update/from-code-to-compliance-decoding-indias-medical-device-software-guidance-15623); [Emergo](https://www.emergobyul.com/news/india-cdsco-finalizes-guidance-medical-device-software); [Maven RS](https://www.mavenrs.com/blog/india-medical-device-software-regulation-cdsco-samd-2026) (the "21 July 2026 / doc number" detail comes from search-result summary text; confirm on cdsco.gov.in)
- MedGemma's HAI-DEF terms add a contractual layer: users must seek regulatory authorisation for clinical use where required. — [HAI-DEF Terms](https://developers.google.com/health-ai-developer-foundations/terms)

### Inferences
- Hosted Claude on Bedrock India profile is a credible option for DPDP-aligned processing (data stays in India, ZDR default) — still requires: BAA-equivalent/DPA with AWS as processor, consent/notice for AI processing, de-identification where possible, tenant-level audit logs, and a decision on the human-review-on-flag clause. Prefer pinning to the `in.` profile through IAM/SCP and verifying destination Regions.
- Design the product as clinician-in-the-loop decision *support* with transparent logic (e.g., deterministic NEWS2) and avoid autonomous diagnosis/treatment output to reduce device-classification exposure; seek legal advice on whether AI CDS modules need CDSCO registration (guidance applies to "manufacturers", and it is not clear if a SaaS vendor counts).
- Azure OpenAI India: not researched; do not assume (see Gap).

### Gaps
- Azure OpenAI India region availability (Central India / South India), model list and data-zone terms: not researched.
- Whether Anthropic's first-party API offers India data residency: not confirmed.
- Primary CDSCO circular text not read; classification rules for CDS and Class A-D mapping not extracted; no legal opinion.
- Exact DPDP rule numbering for cross-border transfer and any Sector (ABDM/NHA, ICMR AI ethics guidelines, Telemedicine Guidelines) overlays not researched.
- Bedrock in-region (non-CRIS) availability in ap-south-1 for older Claude models not verified.
