# AI Use Disclosure — Khata

> **Compliance with Meta AI Hackathon 2026 Guidelines**  
> *"Use any AI coding tools you like and list them in AI_USE.md."*

---

## 1. Overview of AI Models & Tools Used

In developing the **Khata** harness, AI models and developer assistants were utilized across three distinct layers:

| Layer | Tools / Models | Role / Responsibilities |
|---|---|---|
| **System Intelligence (Runtime)** | Meta Llama 3 models (via Llama API / local endpoints) | Multimodal bill understanding (vision), scheme extraction from Hinglish voice transcripts, prompt injection classification, autonomous safety validation. |
| **Speech-to-Text (Runtime)** | OpenAI Whisper / Faster-Whisper | Audio transcription of Hinglish and Devanagari `.opus` voice notes into text. |
| **Pair-Programming & Engineering** | Claude Opus 4.6 & Gemini 3.8 Flash | Code architecture design, test-driven eval generation, edge-case analysis, rubric verification, refactoring. |
| **Pair-Programming & Engineering** | OpenAI Codex (GPT-6) | Problem-statement audit, edge-case review, implementation fixes, and report/eval verification. |

---

## 2. Model Usage in System Runtime

### 2.1 Meta Llama Vision & Reasoning
- **Distributor Classification**: Identifying suppliers, salesman identities, and business entities from unstructured conversational intros.
- **Hinglish Scheme Extraction**: Extracting structured scheme records (`buy_qty`, `free_qty`, `product`, `one_time` vs `permanent`) from casual Hindi/English colloquial speech (*"bhaiya 20 carton lo, 2 carton free milega"*).
- **Prompt Injection Defense**: Evaluates incoming forwarded messages against adversarial injection patterns designed to execute unauthorized transfers or manipulate state.
- **Heuristic Fallback Design**: All LLM calls are architected with robust regex and heuristic fallback handlers, ensuring zero crashes and graceful degradation even when API tokens are unconfigured or endpoints experience transient network latency.

### 2.2 Speech-to-Text
- Voice notes received in WhatsApp chats (`.opus` files) are processed via Whisper models to extract Hinglish conversational audio, with support for transcript sidecars.

---

## 3. AI-Assisted Development Methodology

### 3.1 Prompt Engineering for Scheme Extraction
We designed structured JSON-mode prompts for Meta Llama to extract commercial terms reliably:
```text
You are an expert Indian FMCG trade accountant. Analyze this Hinglish message:
"{text}"
Extract:
1. is_scheme: bool
2. product: name of product or 'general'
3. scheme_type: 'one_time' or 'permanent'
4. buy_qty: integer or null
5. free_qty: integer or null
6. discount_pct: float or null
Return only valid JSON.
```

### 3.2 Test-Driven Development (TDD) with AI
AI pair programming was utilized to create 23 targeted edge-case unit and integration tests in `evals/`:
- **Temporal boundary testing**: Validating that bills dated before a rate list update are not flagged as mismatches.
- **One-time scheme single-use**: Validating that once a scheme is applied to a bill, subsequent orders do not trigger duplicate claims.
- **Autonomous safety boundaries**: Ensuring that financial amounts exceeding ₹2,000 or missing documentation are never auto-sent.
- **Capping mistake prevention**: Automated checks against all 5 fatal capping mistakes.

### 3.3 Defensive Coding Against Hallucinations
- Strict typing with Python dataclasses and Enums (`SchemeType`, `FindingStatus`, `SourceType`).
- Explicit mathematical validation for all rupee computations ($(\text{rate}_{\text{billed}} - \text{rate}_{\text{agreed}}) \times \text{qty}$).
- Confidence scoring grounded in empirical source authority hierarchy rather than arbitrary LLM output numbers.

---

## 4. Human-in-the-Loop Oversight
- All code, business logic, GST tax slabs, and temporal ordering rules were designed, verified, and benchmarked against the hackathon problem statement and `GLOSSARY.md`.
- No proprietary API keys or sensitive credentials are saved in repository code or commits.
