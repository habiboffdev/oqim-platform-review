"""Compatibility wrapper for the generic company source seeder."""

from __future__ import annotations

import asyncio

from seed_company import _main


if __name__ == "__main__":
    asyncio.run(_main())
