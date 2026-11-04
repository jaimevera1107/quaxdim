# Release candidate validation

Original v0.8 local validation: 166 passed, 20 skipped (native engines unavailable),
zero failed; one-million and five-million synthetic CSV runs completed.
This was not a full native engine certification.

The public release candidate also has separate packaging and facade tests.
Execute all test suites on GitHub Actions and on a clean Windows environment
before promoting to a stable release.

This document is an audit of *observed* results, not a claim that every
cataloged quality dimension or listed format works today.
