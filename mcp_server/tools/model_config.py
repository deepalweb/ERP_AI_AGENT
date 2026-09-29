"""One default for chat, investigation recommendations, and incident narration."""
import os

DEFAULT_MODEL = "moonshotai/kimi-k3"


def model_name():
    return os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
