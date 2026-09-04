import json
import sys
from pathlib import Path

# Ensure project root is on sys.path so local packages are importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.tools.sales import get_sales_summary


if __name__ == '__main__':
    print(json.dumps(get_sales_summary(), indent=2))
