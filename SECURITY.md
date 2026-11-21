# Security

voicerig runs your agent in a container with the API keys you pass in `agent.env`, and starts a LiveKit server
with a throwaway development key on a private Docker network. Do not point it at production credentials.

To report a vulnerability, use GitHub's private vulnerability reporting on this repository
(Security -> Report a vulnerability). Please do not open a public issue for it.
