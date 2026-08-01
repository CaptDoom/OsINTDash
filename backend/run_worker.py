import asyncio
import os
import sys

# Ensure root directory is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.main import periodic_ingestion_loop


async def main():
    await periodic_ingestion_loop()


if __name__ == "__main__":
    print("[Drishya 2.0 Worker] Starting ingestion worker")
    asyncio.run(main())
