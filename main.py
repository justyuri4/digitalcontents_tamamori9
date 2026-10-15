from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

# 新しく作成したファイルから関数をインポート
from backend.send_json import run_analysis_to_json

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def read_index():
    """index/index.html を返します"""
    html_path = Path("index/index.html")
    if not html_path.is_file():
        raise HTTPException(status_code=404, detail="index/index.html が見つかりません")
    return html_path.read_text(encoding="utf-8")

@app.get("/api/analysis")
async def get_analysis():
    """切り出したサービスを実行し、解析結果のJSONを返します"""
    return run_analysis_to_json()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)