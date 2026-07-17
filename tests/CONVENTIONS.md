# Test conventions

## Rule: tests validate behavior, not source or report text

A test MUST NOT prove behavior by scanning source code or generated reports for
strings. Concretely, a new functional test must not:

- call `inspect.getsource(...)` and assert on the returned text;
- read a file under `src/` (e.g. `Path(mod.__file__).read_text()`) and assert
  that a specific identifier, comment, docstring, or literal is present/absent;
- read a file under `REPORTS/` (or any derived/generated report, including
  `REPORTS/LAST_GREEN.yaml`) and assert on its text to prove a feature works.

Assert on **runtime behavior** instead: call the function/endpoint/CLI and check
the value it returns, the state it writes, the outcome code it yields, or the
JSON it serves. Examples of the preferred style:

- job-kind metadata: assert `manju.core.jobkinds.retryable_kinds()` and the
  `GET /api/meta/job-kinds` payload — not the text of `gui/page.py`;
- operation semantics: assert the returned `OperationOutcome.code` — not that a
  string like `"waiting_user"` appears in a module;
- QC coverage: assert the per-locale result structure a QC run returns — not
  that a report file mentions a locale.

### Why

Source-text pins are brittle (a token in a comment or a refactor trips them)
and they test the *shape of the implementation* rather than what the program
*does*. Behavioral tests survive refactors and actually protect users.

### Exceptions (not "does feature X work?" tests)

A few checks legitimately read files because the file content IS the artifact
under test — these are allowed and are NOT "proving behavior by scanning
source":

- structural/topology checks (e.g. a required workflow FILE exists);
- data-fixture / corpus schema validation (the fixture's own contents);
- packaging/manifest byte-identity or hash pins on committed golden data.

When in doubt: if the assertion would still be true after a behavior-preserving
rename or re-comment, it is probably fine; if a rename/re-comment would flip it,
rewrite it to assert behavior.
