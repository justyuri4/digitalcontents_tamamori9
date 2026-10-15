import asyncio
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.send_json import stream_analysis_updates

app = FastAPI()

app.mount("/css", StaticFiles(directory="index/css"), name="css")
app.mount("/js", StaticFiles(directory="index/js"), name="js")

@app.get("/", response_class=HTMLResponse)
async def read_index():
    html_path = Path("index/index.html")
    if not html_path.is_file():
        raise HTTPException(status_code=404, detail="index/index.html が見つかりません")
    return html_path.read_text(encoding="utf-8")

@app.websocket("/ws/analysis")
async def websocket_analysis(websocket: WebSocket):
    """
    <button id="generate"> などから接続された際、
    ChatGPTからの出力が返るたびにリアルタイムでJSONを送信します。
    """
    await websocket.accept()
    try:
        for updated_json in stream_analysis_updates():
            await websocket.send_text(json.dumps(updated_json, ensure_ascii=False))
            await asyncio.sleep(0.1) # 描画負荷軽減のウェイト
    except WebSocketDisconnect:
        print("クライアントとのWebSocket接続が切断されました")
    except Exception as e:
        print(f"WebSocketエラー: {e}")
        await websocket.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)