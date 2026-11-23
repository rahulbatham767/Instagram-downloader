"""Run the server:  python -m app [--host 0.0.0.0] [--port 8000]"""

import argparse

import uvicorn

p = argparse.ArgumentParser()
p.add_argument("--host", default="0.0.0.0")
p.add_argument("--port", type=int, default=8000)
args = p.parse_args()
uvicorn.run("app.main:app", host=args.host, port=args.port)
