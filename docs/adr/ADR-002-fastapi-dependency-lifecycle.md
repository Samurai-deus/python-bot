# ADR-002: FastAPI Dependency Teardown Timing Is Not Explicitly Guaranteed

## Status
Accepted

## Context

FastAPI documentation describes the execution order of dependency setup and teardown when using yield-based dependencies.

However, the documentation does NOT explicitly specify the relationship between dependency teardown timing and the lifecycle of StreamingResponse.

In particular, it is not documented whether dependency teardown is guaranteed to occur:
- after the streaming generator is fully consumed,
- after the HTTP response body is fully sent,
- or at some other point determined by the ASGI server implementation.

As a result, dependency teardown MUST NOT be treated as a reliable boundary for resource lifetime when using StreamingResponse.

## Decision

Any resource that must remain valid for the entire duration of a streaming response MUST be managed explicitly inside the streaming generator or via middleware.

Dependency teardown semantics MUST NOT be relied upon for resource lifetime management when using StreamingResponse.

## Consequences

- Dependency injection is treated as a convenience mechanism rather than a strict lifecycle boundary.
- StreamingResponse handlers are required to manage resource lifetime explicitly.
- Database sessions, file handles, and external connections MUST NOT be relied upon to remain valid via dependency teardown.
- Additional boilerplate may be required in streaming endpoints to ensure correct cleanup.

## References

- FastAPI Official Documentation — Dependencies with yield
- FastAPI Official Documentation — StreamingResponse
