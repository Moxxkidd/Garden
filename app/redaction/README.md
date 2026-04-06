# Redaction

This package now contains the default redaction logic for Garden evidence, exports, and display flows.

Current responsibilities include:

- masking cookies, authorization headers, bearer tokens, session identifiers, passwords, secrets, and key-like values
- partially masking email addresses
- bounding preview length for safer CLI/UI display
- providing a centralized service that evidence capture, findings pages, and exports can all reuse
