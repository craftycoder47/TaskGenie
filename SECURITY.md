# Security policy

TaskGenie is a public portfolio/research repository. Production secrets and customer data must never be committed.

Do not commit:

- Telegram bot tokens
- AI/API keys
- passwords, cookies or access tokens
- `.env` files
- private SSH keys
- production databases or customer enquiry exports
- private hostnames, internal infrastructure details or personal data

Runtime credentials must be supplied through environment variables or the deployment platform's secret store.

If a secret is accidentally committed, rotate/revoke it first and then remove it from the repository history. Do not open a public issue containing the secret itself.
