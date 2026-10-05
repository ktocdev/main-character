# Security

## What this app is

Main Character is a **single-user, local-only journal with no authentication**.
This is deliberate. The server binds to `127.0.0.1`, refuses requests whose
`Host` or `Origin` is anything else, and has no concept of accounts.

That design decides what counts as a vulnerability here.

## Out of scope by design

**Running the app exposed to the internet is not a reportable vulnerability.**
If you bind it to `0.0.0.0`, put it behind a reverse proxy, or forward the port,
you have removed the only boundary the design relies on. Reports along the lines
of "there is no login" or "anyone who can reach the port can read the journal"
will be closed as intended behaviour.

**Any multi-user or shared deployment is unsafe.** The server keeps the open
conversation, the loaded entity index and the Chroma handle in one
process-global dict (`STATE`, `server.py:80`). Two people on one instance do not
get two journals. The second person gets the first person's journal, mid-write.
No configuration changes this.

## In scope

Please report:

- **API key handling or leakage.** The key appearing in logs, in
  `server_run.log`, in an error message, in an API response, or being sent
  anywhere other than Anthropic.
- **Prompt injection via journal content.** Entries, imported conversations and
  entity names all reach model prompts. Content that escapes its context and
  steers the model into acting outside the requested operation is a bug.
- **XSS in rendered entries.** Journal text, entity names and dream content are
  rendered in the browser. Anything that escapes `esc()` and executes is in
  scope.
- **DNS rebinding and local binding.** The `TrustedHostMiddleware` allowlist and
  the `Origin` check in `server.py` are the deliberate defence. Without them, an
  attacker-controlled hostname that resolves to `127.0.0.1` makes every request
  same-origin and defeats the browser's own protection. **They are also the
  CSRF mitigation for the destructive POST routes**, so do not remove them as
  redundant. A bypass of either is in scope.
- **Static-export leakage.** Any path by which `publish.py` emits content that
  was not explicitly opted in.
- **Spend-cap bypass.** Any path that calls the API without passing the session
  and monthly ceilings.

## Reporting

Open a [private security advisory](https://github.com/ktocdev/main-character/security/advisories/new)
on this repository, or contact the maintainer directly. Please do not open a
public issue for a security report.

There is no bug bounty. This is a personal project given away for free. What you
get is a fix, and credit if you want it.

Expect a first response within about a week.

## Licence note

This project is **AGPL-3.0-or-later**. The Affero network-use clause is
deliberate: anyone running a modified version as a network service that other
people use must offer those users its source. If you are evaluating this code
for a hosted or multi-tenant product, read `LICENSE` first. Then read the
section above on why multi-tenancy is unsafe here whatever the licence says.
