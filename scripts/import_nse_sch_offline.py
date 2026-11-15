#!/usr/bin/env python3
"""Offline NSE SCH only. No downloads, credentials service or live activation."""
from mfdataindia.nse.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
