"""Project paths, ports, and configuration."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
GRAMJS_SIDECAR_DIR = PROJECT_ROOT / "gramjs-sidecar"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
SNAPSHOTS_DIR = PROJECT_ROOT / ".snapshots"
MOCK_FIXTURES_DIR = PROJECT_ROOT / "cli" / "mock" / "fixtures"
LOG_DIR = PROJECT_ROOT / ".dev-logs"

PORTS = {
    "frontend": 4200,
    "backend": 8001,
    "gramjs": 3100,
    "postgres": 5434,
    "redis": 6381,
}

DOCKER_CONTAINERS = {
    "postgres": "oqim-biz-postgres",
    "redis": "oqim-biz-redis",
}

PROD_HOST = os.getenv("PROD_HOST", "YOUR_VM_HOST")
PROD_USER = os.getenv("PROD_USER", "oqim")
PROD_DOMAIN = os.getenv("PROD_DOMAIN", "your-domain.example")
PROD_DIR = "/opt/oqim"

TMUX_SESSION = "oqim"
