"""
Fortis Edge customer success agent package.

Modules:
- prompts: system instructions for Grok
- api: FastAPI router (chat, Slack-style webhooks not included; Twilio + REST)
- tools: agent tools (create_estimate)
- estimate_models / estimate_pdf: packaging estimate domain + PDF output
- knowledge: Supabase-backed reference retrieval
"""

__version__ = "1.0.0"
