import uvicorn
import os
import sys

# Ensure root directory is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if __name__ == "__main__":
    print("[Drishya 2.0] Starting Uvicorn server on http://localhost:3001")
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=3001, reload=True)
