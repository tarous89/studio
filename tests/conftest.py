"""All Studio integration tests exercise the same authenticated server mode."""
import os

os.environ["STUDIO_SMOKE_ONLY"] = "0"
os.environ.setdefault("PUBLIC_BASE_URL", "https://studio-web-hz4l.onrender.com")
