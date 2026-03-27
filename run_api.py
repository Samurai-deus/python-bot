"""
Launcher for the FastAPI backend.
Run from project root: python run_api.py
"""
import sys
import os

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("API_HOST", "127.0.0.1")
    uvicorn.run(
        "api.main:app",
        host=host,
        port=8000,
        reload=False,
    )
