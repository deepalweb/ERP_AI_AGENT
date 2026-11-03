"""One default for chat, investigation recommendations, and incident narration."""
import os

DEFAULT_MODEL = "meta/muse-glimmer-30b"


def model_name():
    return os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
