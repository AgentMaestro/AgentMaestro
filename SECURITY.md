## Security Policy

Security is a top priority for AgentMaestro. If you believe you have discovered a security vulnerability in this repository, please follow the steps below so we can address it responsibly.

1. **Report the issue:** Email the vulnerability details to **dev.agent.maestro@gmail.com**. Include a clear description, reproduction steps, and any supporting files or timestamps that help us understand the impact.
2. **Coordinate privately:** Do not disclose the issue publicly until the AgentMaestro team has had a chance to investigate and respond. We will acknowledge receipt of your report and keep you informed of our remediation progress.
3. **Respect policies:** Avoid any actions that could unintentionally disrupt production systems, compromise customer data, or violate laws. We appreciate reports from researchers who prioritize safe, ethical disclosure.

We aim to respond promptly and appreciate your help keeping AgentMaestro secure.

### Prompt Secret Scrubbing

All agent chat prompts are automatically passed through a server-side secret scrubber before they ever reach the LLM. If the scrubber detects high-confidence secrets (API keys, auth tokens, etc.), the values are masked, the submission is still delivered, and a system message reassures the user that Maestro has their back. This ensures no accidental secrets propagate beyond the chat UI.
