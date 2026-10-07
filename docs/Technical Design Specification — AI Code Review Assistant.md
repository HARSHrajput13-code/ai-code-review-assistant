# Technical Design Specification
## AI Code Review Assistant

**Document Version:** 0.1  
**Status:** Initial Technical Baseline  
**Derived From:** Behavioural Specification v0.1  
**Implementation Target:** Local-first, modular, production-grade foundation  
**Primary Language:** Python  
**Initial Supported Source Language:** Python

---

# 1. Purpose

This document translates the Behavioural Specification into concrete technical decisions.

It defines:

- application architecture,
- technology choices,
- module boundaries,
- API contracts,
- domain models,
- review orchestration,
- AI integration,
- static-analysis integration,
- scoring strategy,
- validation strategy,
- testing boundaries,
- configuration strategy,
- development/runtime conventions.

This document intentionally stops short of prescribing every source file, class, function, or UI component.

Those implementation details remain the responsibility of the implementation phase.

---

# 2. Architectural Style

The application shall use a **modular monolith** architecture.

It shall not initially be implemented as microservices.

The system shall run as one logical application composed of clearly separated modules:

```text
Frontend
    |
    v
Backend API
    |
    v
Application / Review Layer
    |
    +--------------------+
    |                    |
    v                    v
Static Analysis      AI Provider
                        |
                        v
                      Ollama
```

The reason for choosing a modular monolith is that the project has relatively low domain complexity but still benefits substantially from strong separation of responsibilities.

The architecture shall preserve boundaries that would permit later extraction into services if scale eventually requires it.

---

# 3. Architectural Principles

The implementation shall follow these principles in priority order:

1. Correctness
2. Security
3. Explicit contracts
4. Modularity
5. Testability
6. Maintainability
7. Replaceability
8. Simplicity
9. Scalability

"Scalability" shall not be interpreted as justification for unnecessary infrastructure.

The first version shall be easy to run on a developer laptop.

---

# 4. Final Technology Stack

## 4.1 Frontend

### React

React shall be used for the user interface.

### TypeScript

The frontend shall use TypeScript rather than plain JavaScript.

React's official documentation supports TypeScript as the typed development path for React applications.

### Vite

Vite shall be used as the frontend development/build tool.

### Monaco Editor

Monaco Editor shall provide the source-code editing experience.

### Styling

Tailwind CSS shall be used for the initial UI styling layer.

The design system shall remain component-oriented rather than allowing arbitrary styling to spread across the application.

---

# 5. Frontend State Architecture

The frontend shall separate:

### Local UI state

Examples:

```text
selected language
editor content
expanded issue
UI visibility
```

from:

### Server/request state

Examples:

```text
review request
review result
request loading state
request error state
```

For the initial implementation, React state shall be sufficient for local UI state.

The application shall avoid introducing a global state-management framework unless actual cross-screen state complexity requires it.

This prevents premature complexity.

---

# 6. Frontend API Contract Strategy

The backend shall expose a versioned REST API.

FastAPI automatically provides an OpenAPI schema and interactive API documentation, making OpenAPI the authoritative machine-readable representation of the API contract.

The frontend shall consume this contract rather than independently inventing request/response structures.

The preferred architecture is:

```text
FastAPI/Pydantic
       |
       v
    OpenAPI
       |
       v
Generated TypeScript API types/client
       |
       v
React
```

This minimizes frontend/backend contract drift.

The exact OpenAPI generation utility shall be selected during implementation, but generated client types are preferred over manually duplicated request/response interfaces.

---

# 7. Backend Technology

The backend shall use:

```text
Python
FastAPI
Pydantic
ASGI server
```

FastAPI's dependency-injection system is suitable for supplying abstractions such as repositories, analyzers, and AI providers without hard-coding concrete implementations inside route handlers.

FastAPI's OpenAPI integration shall be treated as part of the API design rather than merely a documentation feature.

---

# 8. Backend Architectural Layers

The backend shall conceptually contain the following layers:

```text
API Layer
    |
    v
Application Layer
    |
    v
Domain Layer
    |
    +-------------------+
    |                   |
    v                   v
Infrastructure      External Adapters
```

More concretely:

```text
API
 |
Review Application Service
 |
Review Domain / Models
 |
+--------------------------+
|                          |
Static Analysis       AI Analysis
|                          |
+--------------------------+
 |
Scoring / Normalization
 |
Persistence Adapter
```

The exact directory names may differ, but these responsibilities shall remain separated.

---

# 9. API Layer Responsibilities

The API layer shall:

- receive HTTP requests,
- validate transport-level input,
- authenticate/authorize in future if required,
- invoke application services,
- translate domain results into API responses,
- translate known application errors into HTTP responses.

The API layer shall **not** contain:

- AI prompt construction,
- scoring algorithms,
- static-analysis logic,
- deduplication algorithms,
- database-specific business logic.

A route handler should remain thin.

Conceptually:

```text
HTTP Request
     |
     v
API Endpoint
     |
     v
ReviewApplicationService
     |
     v
ReviewResult
     |
     v
HTTP Response
```

---

# 10. Primary API

The principal endpoint shall be:

```text
POST /api/v1/reviews
```

Purpose:

> Submit source code for analysis and obtain a structured review.

Request:

```json
{
  "language": "python",
  "source_code": "def divide(a, b):\n    return a / b"
}
```

Response:

```json
{
  "review_id": "...",
  "status": "completed",
  "score": {
    "overall": 78
  },
  "summary": "...",
  "issues": [],
  "improved_code": "...",
  "capabilities": {
    "static_analysis": true,
    "ai_analysis": true,
    "improved_code": true
  }
}
```

The exact schema is to be formalized using Pydantic models.

---

# 11. API Versioning

All application endpoints shall be versioned.

Initial namespace:

```text
/api/v1/
```

Future incompatible API changes shall create a new version rather than silently changing the meaning of existing responses.

---

# 12. API Endpoints

The initial API surface shall remain intentionally small.

### Review

```text
POST /api/v1/reviews
```

### Health

```text
GET /api/v1/health
```

### Readiness

A separate readiness endpoint may be provided if needed to determine whether dependencies such as Ollama are operational.

The initial application shall not create dozens of endpoints for functionality that does not exist.

---

# 13. Domain Model

The central domain object shall be a **Code Review**.

Conceptually:

```text
CodeReview
├── review_id
├── language
├── source
├── status
├── score
├── summary
├── issues
├── improved_code
├── analysis_metadata
└── timestamps
```

The system shall distinguish between:

### Input model

What the user sends.

### Domain model

What the application internally understands.

### Response model

What the API exposes.

These shall not automatically be assumed to be the same object.

---

# 14. Issue Domain Model

The normalized issue shall conceptually contain:

```text
Issue
├── id
├── severity
├── category
├── title
├── location
├── summary
├── impact
├── recommendation
├── source
└── confidence
```

### Severity

```text
CRITICAL
HIGH
MEDIUM
LOW
```

### Category

```text
CORRECTNESS
SECURITY
PERFORMANCE
READABILITY
MAINTAINABILITY
BEST_PRACTICE
```

These shall be enums rather than arbitrary strings wherever practical.

This improves validation and prevents malformed categories from propagating through the system.

---

# 15. Review Status

The review lifecycle shall use an explicit status model.

Initial states:

```text
PENDING
RUNNING
COMPLETED
PARTIAL
FAILED
```

Normal lifecycle:

```text
PENDING
   |
   v
RUNNING
   |
   +------------+
   |            |
   v            v
COMPLETED     PARTIAL
   |
   |
   v
FAILED (only for unrecoverable review failure)
```

The implementation shall avoid exposing unnecessary internal intermediate states through the public API.

---

# 16. Review Orchestrator

The Review Orchestrator is the central application service.

It shall coordinate:

```text
Input validation
        |
        v
Language resolution
        |
        v
Static analysis
        |
        v
AI analysis
        |
        v
Finding normalization
        |
        v
Finding deduplication
        |
        v
Score calculation
        |
        v
Improved-code generation
        |
        v
Improved-code validation
        |
        v
Final result construction
```

The orchestrator shall coordinate these components.

It shall not implement all of their internal logic itself.

---

# 17. Analysis Interfaces

The core system shall depend on abstract analysis interfaces.

Conceptually:

```text
StaticAnalyzer
     |
     +---- PythonStaticAnalyzer


AIReviewProvider
     |
     +---- OllamaAIReviewProvider
```

This allows replacement implementations during testing.

For example:

```text
Production:
OllamaAIReviewProvider

Testing:
FakeAIReviewProvider
```

The Review Orchestrator should not know which concrete provider is being used.

---

# 18. Static Analysis Architecture

The Python analyzer shall operate without executing submitted source code.

Conceptual pipeline:

```text
Source Code
    |
    v
Python Parser / AST
    |
    +---- Syntax validation
    |
    +---- Structural analysis
    |
    +---- Lint/static rules
    |
    +---- Security analysis
    |
    v
Static Findings
```

External analysis tools shall be invoked through controlled adapters.

Tool-specific formats shall be normalized into the internal `Issue` model.

The core application shall not depend on Pylint/Bandit-specific output structures.

---

# 19. Static Analyzer Result Contract

The analyzer shall return normalized findings rather than raw subprocess output.

For example:

```text
StaticAnalysisResult
├── success
├── findings[]
└── diagnostics[]
```

A tool failure shall be distinguishable from:

```text
Analyzer ran successfully and found zero issues.
```

These are different conditions.

---

# 20. AI Provider Architecture

The AI provider shall be abstracted.

Conceptually:

```text
AIReviewProvider
      |
      +---- OllamaProvider
```

The provider interface shall expose an application-oriented operation rather than a generic "chat" interface.

Conceptually:

```text
review_code(request) -> AIReviewResult
```

This prevents Ollama-specific concepts from leaking through the application.

---

# 21. Ollama Integration

Ollama shall initially be accessed locally.

The application shall communicate with the local Ollama service through its API/client.

The AI response shall use structured output constrained by a JSON schema.

Ollama supports schema-constrained structured outputs and its documentation specifically demonstrates using Pydantic-generated schemas for this purpose.

This is particularly appropriate for the project because our output is inherently structured:

```text
summary
score assessment
issues
improved code
```

---

# 22. AI Model Configuration

The exact LLM shall not be embedded into business logic.

Configuration shall contain, conceptually:

```text
OLLAMA_BASE_URL
OLLAMA_MODEL
OLLAMA_TEMPERATURE
OLLAMA_TIMEOUT
```

Additional generation parameters may be introduced only when justified by actual model behaviour.

The application shall have one central configuration mechanism.

Components shall not read environment variables independently throughout the codebase.

---

# 23. AI Prompt Architecture

The review prompt shall have clear conceptual sections:

```text
SYSTEM / POLICY
        |
        v
REVIEW INSTRUCTIONS
        |
        v
OUTPUT CONTRACT
        |
        v
STATIC FINDINGS
        |
        v
SOURCE CODE
```

The source code shall be explicitly marked as untrusted input.

The AI shall not be permitted to override the review contract because of instructions embedded within source-code comments, strings, docstrings, or identifiers.

---

# 24. AI Output Schema

The canonical AI response shall conceptually be:

```json
{
  "summary": "string",
  "issues": [
    {
      "severity": "HIGH",
      "category": "SECURITY",
      "title": "string",
      "line": 10,
      "end_line": 10,
      "summary": "string",
      "impact": "string",
      "recommendation": "string"
    }
  ],
  "improved_code": "string"
}
```

The AI may provide supporting quality assessments internally, but the application's final score shall remain under control of the scoring engine.

---

# 25. AI Response Validation

The pipeline shall be:

```text
LLM response
    |
    v
JSON decoding
    |
    v
Pydantic validation
    |
    v
Semantic validation
    |
    v
Normalization
```

Semantic validation shall include checks such as:

- severity is valid,
- category is valid,
- line number is plausible,
- required fields exist,
- improved code is a string when present,
- no issue contains impossible ranges.

Malformed AI output shall be rejected.

The application shall never silently coerce arbitrary text into a successful review.

---

# 26. Finding Normalization

Static and AI findings may use different structures.

They shall be converted into the same internal representation:

```text
StaticFinding ─────┐
                    ├──> NormalizedIssue
AIFinding ──────────┘
```

This is necessary before deduplication and scoring.

---

# 27. Finding Deduplication

Deduplication shall happen after normalization.

The initial implementation shall use deterministic heuristics involving factors such as:

```text
category
severity
location
title similarity
issue semantics
```

The system shall not use another LLM call merely to deduplicate findings in Version 1.

The objective is predictable behaviour.

---

# 28. Confidence and Verification

AI findings shall carry internal confidence/verification metadata where useful.

This metadata shall not be presented as scientific probability.

Instead, it represents the application's confidence in whether the finding has enough supporting evidence to become a user-facing result.

Static findings may be considered stronger evidence when a deterministic analyzer directly detects the issue.

AI-only findings shall be handled more conservatively.

---

# 29. Scoring Engine

The score shall be generated by a dedicated scoring component.

Conceptually:

```text
Validated Findings
       |
       v
Scoring Policy
       |
       v
Category Scores
       |
       v
Overall Score
```

The first implementation shall use a deterministic weighted scoring policy.

A proposed initial policy is:

```text
Correctness       25%
Security          25%
Maintainability   15%
Readability       15%
Performance       10%
Best Practice     10%
```

The exact weights shall be configurable in one location.

---

# 30. Score Calculation Principle

The scoring system shall begin at a healthy baseline and apply deductions according to validated findings, severity, and category.

Conceptually:

```text
Category Score = 100 - Validated Deductions
```

with:

```text
0 <= Category Score <= 100
```

The final score shall be:

```text
Overall =
    Correctness × 0.25
  + Security × 0.25
  + Maintainability × 0.15
  + Readability × 0.15
  + Performance × 0.10
  + BestPractice × 0.10
```

The scoring policy shall impose caps/floors where necessary.

For example, severe validated security or correctness issues may prevent the overall score from appearing unrealistically high.

This prevents a code sample with a critical vulnerability from receiving a misleadingly strong overall score because it is well formatted.

---

# 31. Improved-Code Generation

The improved-code step shall be treated as a separate AI operation.

The model should receive:

```text
Original code
+
Normalized issues
+
Review policy
```

rather than being asked to independently rediscover the entire review.

This makes the improvement operation more deterministic and focused.

---

# 32. Improved-Code Validation

Generated code shall pass through:

```text
Generated code
    |
    v
Language parser
    |
    v
Optional lightweight static checks
    |
    v
Accepted / rejected
```

Execution shall not occur.

For Python, `ast.parse()` or an equivalent parser-based validation shall establish basic syntactic validity.

A syntactically valid program is not automatically considered semantically correct.

---

# 33. Error Model

Application-level errors shall have stable internal codes.

Example:

```text
InvalidReviewRequest
UnsupportedLanguage
CodeTooLarge
StaticAnalysisUnavailable
AIProviderUnavailable
AIResponseInvalid
ImprovedCodeInvalid
ReviewTimeout
UnexpectedReviewFailure
```

The HTTP layer shall map these into suitable HTTP statuses.

The UI shall receive safe user-facing messages.

The backend logs shall retain diagnostic context.

---

# 34. Dependency Injection

Dependencies shall be supplied through an application composition layer.

Conceptually:

```text
Application Startup
       |
       +---- Ollama Provider
       +---- Static Analyzer
       +---- Scoring Policy
       +---- Repository
       |
       v
Review Application Service
```

FastAPI's dependency system can provide these components to API handlers without coupling route definitions to infrastructure implementations.

---

# 35. Persistence Decision

Persistence shall be **optional for the first functional release**.

SQLite may be introduced for:

- review metadata,
- diagnostics,
- future history,
- future project expansion.

The core review workflow shall not require database access.

This means:

```text
Core Review
    |
    +---- works without DB

Optional Persistence
    |
    +---- stores review information
```

This is preferable to making SQLite an artificial dependency of every request.

If persistence is enabled, database interaction shall occur behind a repository interface.

---

# 36. Database Abstraction

If SQLite is implemented:

```text
Application
    |
Repository Interface
    |
SQLite Repository
    |
SQLite
```

The application shall not issue SQL statements directly from route handlers or domain services.

A future PostgreSQL implementation should conceptually replace:

```text
SQLiteRepository
```

rather than requiring changes throughout the review engine.

---

# 37. Configuration Management

All runtime configuration shall be centralized.

Configuration categories include:

```text
Application
AI
Static analysis
Scoring
Persistence
Logging
Limits
```

Configuration shall be loaded once and validated.

Secrets shall never be committed into source control.

A committed example configuration file may document available settings without containing secret values.

---

# 38. Repository Structure

The high-level repository remains:

```text
ai-code-review-assistant/
│
├── frontend/
├── backend/
├── ai/
├── analysis/
├── shared/
├── tests/
├── docs/
├── scripts/
└── README.md
```

The technical interpretation is now:

```text
frontend/
    UI and client behaviour

backend/
    HTTP/API and application integration

ai/
    AI provider abstraction + Ollama adapter + prompts/schema assets

analysis/
    language parsing + static-analysis adapters + finding normalization

shared/
    API/domain contracts shared across boundaries where useful

tests/
    unit + integration + end-to-end

docs/
    behavioural and technical specifications

scripts/
    development/setup/utility automation
```

The exact internal decomposition remains an implementation concern.

---

# 39. Dependency Management

The frontend and backend shall have independently managed dependencies.

The frontend shall use the Node ecosystem.

The backend shall use Python's packaging/environment tooling.

The project should use reproducible dependency versions rather than unconstrained dependencies.

Development, test, and runtime dependencies should be distinguishable.

---

# 40. Local Development Runtime

The normal development environment shall contain:

```text
Browser
   |
   +---- React/Vite development server
   |
   +---- FastAPI application
   |
   +---- Ollama local service
```

The developer shall be able to start each required service predictably.

A development setup script/documentation shall eventually make the startup sequence straightforward.

---

# 41. Containerization

Docker shall **not** be a mandatory requirement for Version 1.

The local-first target is primarily intended to run directly on the developer machine.

Containerization may be added later if it provides a concrete benefit, such as reproducible deployment or evaluation environments.

This avoids complicating local Ollama/model access prematurely.

---

# 42. Testing Architecture

The system shall use deterministic tests wherever possible.

### Pure unit tests

Target:

```text
scoring
normalization
deduplication
validation
configuration
domain logic
```

### Adapter tests

Target:

```text
Ollama adapter
static analyzer adapters
SQLite repository
```

These may use mocks/fakes for external dependencies.

### Integration tests

Target:

```text
API
  ↓
Application service
  ↓
Analyzer interfaces
  ↓
Normalized result
```

### End-to-end test

At least one test shall exercise the primary user workflow from frontend request through backend processing and result rendering.

---

# 43. Fake AI Provider

A deterministic fake AI provider shall be available for automated testing.

Conceptually:

```text
AIReviewProvider
      |
      +---- OllamaAIProvider
      |
      +---- FakeAIReviewProvider
```

The fake provider shall return predefined structured review results.

This is important because tests should not depend on:

- model availability,
- model response variability,
- inference speed,
- local GPU availability.

---

# 44. Logging Strategy

Backend logging shall be structured.

Each review request shall receive a correlation identifier.

Log records should identify:

```text
request_id
review_id
stage
duration
status
error_code
model identifier where relevant
```

Source code shall not normally be logged.

Full AI prompts and full model responses shall not be logged by default.

---

# 45. Performance Strategy

Version 1 shall optimize for reliable local execution, not distributed throughput.

The following shall be enforced:

- request size limits,
- analyzer timeouts,
- AI request timeouts,
- bounded processing,
- no duplicate analysis calls without justification.

The frontend shall remain responsive while a review is running.

The AI call shall run in a manner that does not block unrelated server work unnecessarily.

---

# 46. Security Boundary for Static Tools

Static-analysis subprocesses shall be treated carefully.

The implementation shall:

- pass source data through controlled mechanisms,
- avoid shell-string construction,
- avoid shell interpretation where unnecessary,
- configure timeouts,
- capture stdout/stderr safely,
- prevent uncontrolled filesystem access where possible.

The system must never construct commands from untrusted source-code content.

---

# 47. Security Boundary for AI

The AI provider shall receive only the information required for the review.

The application shall explicitly separate trusted instructions from untrusted source code.

The model shall not be given uncontrolled authority to:

- execute commands,
- access arbitrary files,
- invoke system tools,
- make network requests.

Version 1 shall not use an autonomous tool-calling agent.

---

# 48. Observability of Review Stages

Each review should be traceable through logical stages:

```text
RECEIVED
VALIDATED
STATIC_ANALYSIS
AI_ANALYSIS
NORMALIZATION
DEDUPLICATION
SCORING
IMPROVEMENT
VALIDATION
COMPLETED
```

This does not imply that each stage must become a separate microservice.

These are application-level processing stages within the modular monolith.

---

# 49. Technology Replacement Boundaries

The architecture shall make the following replacements relatively localized:

```text
Ollama
    → another AI provider

Model A
    → Model B

Pylint/Bandit
    → another static analyzer

SQLite
    → PostgreSQL

Python-only
    → additional language adapters
```

The application/domain layer should remain largely stable.

This requirement is more important than choosing a particular library at this stage.

---

# 50. Initial Implementation Sequence

Claude Code should implement the system in the following order:

```text
1. Repository bootstrap
        |
2. Backend foundation
        |
3. Domain/contracts
        |
4. Python static-analysis adapter
        |
5. Ollama AI adapter
        |
6. Review orchestrator
        |
7. Scoring engine
        |
8. Improved-code validation
        |
9. API endpoints
        |
10. Frontend foundation
        |
11. Monaco code editor
        |
12. Review result UI
        |
13. Error/partial-failure UI
        |
14. Automated tests
        |
15. End-to-end verification
```

The implementation should proceed vertically enough that a fully functioning review path exists early rather than building the entire frontend before any backend functionality works.

---

# 51. First Vertical Slice

The first meaningful milestone shall be:

```text
Python code entered
       |
       v
POST /api/v1/reviews
       |
       v
Review Orchestrator
       |
       +---- Static Analysis
       |
       +---- Fake AI Provider initially
       |
       v
Normalized ReviewResult
       |
       v
Frontend displays result
```

Only after this stable contract exists should the real Ollama integration replace the fake AI implementation.

This reduces debugging complexity because the application architecture can be tested independently of the local model.

---

# 52. Second Vertical Slice

The second milestone shall replace the fake provider:

```text
FakeAIReviewProvider
        ↓
OllamaAIReviewProvider
        ↓
Structured AI response
        ↓
Pydantic validation
```

At this stage, the real local model becomes part of the working application.

---

# 53. Third Vertical Slice

The third milestone shall complete:

```text
Issue detection
       +
Scoring
       +
Issue explanations
       +
Improved code
       +
Improved-code validation
```

The application can then be considered feature-complete from the perspective of the six core requirements.

---

# 54. Technical Acceptance Criteria

The technical implementation shall satisfy the following:

### Architecture

The application has clear module boundaries.

### API

The API is versioned and represented through OpenAPI.

### Validation

Inputs and AI outputs are validated.

### AI

The LLM is accessed through an abstraction rather than directly from API routes.

### Static Analysis

Static analysis is accessed through an abstraction rather than directly from API routes.

### Scoring

Score calculation is deterministic and independently testable.

### Security

Submitted code is never executed.

### Failure Handling

AI and static-analysis failures are represented explicitly.

### Testing

Core business logic does not require a live LLM for unit tests.

### Maintainability

Infrastructure details do not leak into core application logic.

### Replaceability

The AI model/provider, analyzer implementation, and persistence mechanism are replaceable without redesigning the review domain.

---

# 55. Decisions Now Considered Frozen

Unless a later requirement exposes a genuine problem, the following should now be treated as architectural decisions:

```text
Architecture:
Modular monolith

Frontend:
React + TypeScript + Vite
Monaco Editor
Tailwind CSS

Backend:
Python + FastAPI + Pydantic

API:
Versioned REST + OpenAPI

AI:
Ollama behind an AI provider abstraction

AI output:
Structured schema + Pydantic validation

Static analysis:
Python AST + analyzer adapters

Review:
Central Review Orchestrator

Scoring:
Dedicated deterministic scoring engine

Persistence:
Optional SQLite behind repository abstraction

Testing:
Unit + integration + E2E + fake AI provider

Runtime:
Local-first

Execution:
No arbitrary source-code execution

Containerization:
Not mandatory for Version 1
```

---

# 56. Decisions Deliberately Not Frozen Yet

The following should remain open until implementation requires them:

```text
Exact coding LLM
Exact quantization
Exact prompt wording
Exact Pydantic class names
Exact file names
Exact React component tree
Exact CSS design
Exact analyzer rule set
Exact score deduction table
Exact database table structure
Exact package-manager commands
```

These are implementation decisions, not product architecture.

---

# 57. Technical Design Principle

The most important implementation constraint is:

> **Infrastructure must serve the review domain, not define it.**

Therefore:

```text
Ollama ≠ AI domain

Pylint ≠ Review domain

SQLite ≠ Review domain

FastAPI ≠ Review domain

React ≠ Review domain
```

They are implementation mechanisms surrounding the core review system.

The core system is:

```text
Code
  ↓
Analysis
  ↓
Findings
  ↓
Normalization
  ↓
Scoring
  ↓
Improvement
  ↓
Validated Review Result
```

That conceptual separation shall guide the implementation.

---

# 58. Current Implementation Target

At the end of the implementation phase, the repository should be capable of doing this locally:

```text
Developer
   |
   v
Open Web Application
   |
   v
Select Python
   |
   v
Enter Code
   |
   v
Review Code
   |
   +------------------------+
   |                        |
   v                        v
Static Analysis        Local Ollama LLM
   |                        |
   +-----------+------------+
               |
               v
       Review Orchestrator
               |
       +-------+--------+
       |       |        |
       v       v        v
      Score  Issues  Improved Code
               |
               v
          Review Result
```

This constitutes the technical baseline that Claude Code should implement.

---

# 59. Implementation Rule for Claude Code

Claude Code should not introduce a technology merely because it is popular or because it can solve a problem in a more sophisticated way.

Before adding a dependency or architectural mechanism, the implementation should ask:

```text
Does this solve a defined requirement?
Does it preserve the established boundaries?
Does it reduce complexity overall?
Can it be tested?
Can it be replaced?
```

If the answer is no, the simpler existing mechanism should be preferred.

The target is not the largest or most complicated system.

The target is a small but genuinely engineered system whose architecture could be extended without having to be rewritten.