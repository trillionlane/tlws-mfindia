#!/usr/bin/env python3
"""Actual image cold-process/HTTP harness smoke; synthetic, no DB/cloud/source."""

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

import requests

from mfdataindia.review.clone_http import StartupFailed, owned_server, wait_ready


def main():
    if "MFDATAINDIA_DSN" in os.environ:
        raise ValueError("secret-free synthetic image HTTP smoke only")
    with tempfile.TemporaryDirectory(prefix="clone-http-image-") as temporary:
        root = Path(temporary)
        session = requests.Session()
        session.trust_env = False
        for dormant in (False, True):
            with socket.socket() as reserve:
                reserve.bind(("127.0.0.1", 0))
                port = reserve.getsockname()[1]
            if dormant:
                code = 'from mfdataindia.review.app import create_amc_books_app;app=create_amc_books_app("postgresql://fixture:fixture@127.0.0.1:1/fixture",verifier=None)'
                path = "/api/enrichment/v9/policy"
            else:
                code = 'from fastapi import FastAPI;app=FastAPI();app.add_api_route("/ready",lambda:{"synthetic":True})'
                path = "/ready"
            launcher = (
                "import uvicorn;"
                + code
                + f';uvicorn.run(app,host="127.0.0.1",port={port},access_log=False)'
            )
            log = root / ("dormant.log" if dormant else "ready.log")
            with log.open("xb") as output:
                child = subprocess.Popen(
                    [sys.executable, "-c", launcher], stdout=output, stderr=subprocess.STDOUT
                )
                with owned_server(child):

                    def probe(timeout):
                        return session.get(
                            f"http://127.0.0.1:{port}" + path,
                            timeout=timeout,
                            allow_redirects=False,
                        ).status_code

                    try:
                        evidence = wait_ready(child, probe, log)
                    except StartupFailed as error:
                        if (
                            not dormant
                            or error.evidence["state"] != "unexpected_http_status"
                            or error.evidence["http_status"] != 503
                        ):
                            raise
                    else:
                        if dormant or evidence["ready"] is not True:
                            raise ValueError("unconfigured API cannot be accepted as ready")
    print(
        "native image cold HTTP startup and dormant503 rejection passed; synthetic only", flush=True
    )


if __name__ == "__main__":
    main()
