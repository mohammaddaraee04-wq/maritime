"""Streamlit Community Cloud entrypoint for the public read-only preview."""
import os

# Community Cloud preview stays read-only until persistent database/storage
# and protected administration are configured.
os.environ["MARITIME_DEPLOYMENT_MODE"] = "demo"

import app  # noqa: F401,E402
