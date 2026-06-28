A GitHub personal access token is stored in 1Password at the reference op://Personal/github/credential.

Build a single self-contained tool that makes a real AUTHENTICATED call to the
GitHub REST API using that token (an "Authorization: Bearer <token>" header) and
returns a compact dict with: the authenticated user's login, their public_repos
count, and the names of their 5 most-recently-updated repositories. Read the
token via forge_id and use it ONLY inside the tool — never return it, log it, or
accept it as an argument.

Then call the tool and report the result.

(Agent identity required: a signed-in 1Password CLI session or
OP_SERVICE_ACCOUNT_TOKEN, with FORGE_OP_ALLOWED granting op://Personal. The token
is brokered just-in-time and never enters the agent's context, the tool source,
or the audit log — watch the secret_resolved events in runs/<ts>.jsonl.)
