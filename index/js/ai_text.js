document.addEventListener("DOMContentLoaded", () => {
  const generateBtn = document.getElementById("generate");
  const transcriptArea = document.getElementById("transcript");

  if (generateBtn && transcriptArea) {
    generateBtn.addEventListener("click", () => {
      generateBtn.disabled = true;
      generateBtn.textContent = "分析・生成中...";
      transcriptArea.value = "AI分析を開始しました。結果をリアルタイムで取得中...";

      // WebSocket接続の確立
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsUrl = `${protocol}//${window.location.host}/ws/analysis`;
      const socket = new WebSocket(wsUrl);

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          // ChatGPTから話題単位の出力が返るたびにテキストエリアが即座に更新される
          if (data.results) {
            transcriptArea.value = JSON.stringify(data.results, null, 2);
          }
        } catch (e) {
          console.error("JSONパースエラー:", e);
        }
      };

      socket.onclose = () => {
        generateBtn.disabled = false;
        generateBtn.textContent = "生成";
        console.log("WebSocket接続が終了しました。");
      };

      socket.onerror = (error) => {
        console.error("WebSocketエラー:", error);
        alert("通信中にエラーが発生しました。");
        generateBtn.disabled = false;
        generateBtn.textContent = "生成";
      };
    });
  }
});