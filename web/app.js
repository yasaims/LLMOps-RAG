// バニラ JS (ビルド不要)。CloudFront が /query, /healthz を同一オリジンの API Gateway に
// プロキシするため、fetch は相対パスで完結する (CORS プリフライトは発生しない)。
(() => {
  const form = document.getElementById("query-form");
  const questionInput = document.getElementById("question");
  const submitBtn = document.getElementById("submit-btn");
  const statusEl = document.getElementById("status");
  const resultEl = document.getElementById("result");
  const answerEl = document.getElementById("answer");
  const sourcesEl = document.getElementById("sources");
  const metaEl = document.getElementById("meta");

  function setStatus(message, isError = false) {
    statusEl.hidden = !message;
    statusEl.textContent = message;
    statusEl.classList.toggle("status--error", isError);
  }

  function renderResult(data) {
    answerEl.textContent = data.answer;

    sourcesEl.innerHTML = "";
    for (const source of data.sources) {
      const li = document.createElement("li");
      const pageText = source.page_start != null ? ` (p.${source.page_start})` : "";
      const sectionText = source.section ? `${source.section}${pageText}` : source.doc;
      const link = document.createElement("a");
      link.href = source.source_url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = `[${source.index}] ${sectionText}`;
      li.appendChild(link);
      sourcesEl.appendChild(li);
    }

    const usage = data.usage || {};
    const inTok = usage.inputTokens ?? "-";
    const outTok = usage.outputTokens ?? "-";
    metaEl.textContent =
      `レイテンシ: ${Math.round(data.latency_ms)} ms / ` +
      `入力トークン: ${inTok} / 出力トークン: ${outTok}`;

    resultEl.hidden = false;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = questionInput.value.trim();
    if (!question) return;

    submitBtn.disabled = true;
    resultEl.hidden = true;
    setStatus("回答を生成中です… (数秒かかることがあります)");

    try {
      const resp = await fetch("/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });

      if (resp.status === 429) {
        setStatus(
          "デモのレート制限に達しました。しばらく待ってから再度お試しください。",
          true
        );
        return;
      }
      if (!resp.ok) {
        setStatus(`エラーが発生しました (HTTP ${resp.status})。`, true);
        return;
      }

      const data = await resp.json();
      setStatus("");
      renderResult(data);
    } catch (err) {
      setStatus("通信エラーが発生しました。ネットワーク状態を確認してください。", true);
    } finally {
      submitBtn.disabled = false;
    }
  });
})();
