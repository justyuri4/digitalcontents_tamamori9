"""互換用の起動口。アプリ本体は backend/main.py にあります。"""

import importlib.util
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

spec = importlib.util.spec_from_file_location(
    "tamamori_backend_main", BACKEND_DIR / "main.py"
)
if spec is None or spec.loader is None:
    raise ImportError("backend/main.py を読み込めません")

backend_main = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backend_main
spec.loader.exec_module(backend_main)
app = backend_main.app

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080)
