async function runMode(mode) {
  const resp = await fetch("/run?mode=" + mode, { method: "POST" });
  const data = await resp.json();
  if (!data.ok) {
    alert(data.message || "启动失败");
  }
  await refresh();
}

function setButtons(disabled) {
  document.getElementById("btn-pool").disabled = disabled;
  document.getElementById("btn-oi").disabled = disabled;
  document.getElementById("btn-full").disabled = disabled;
}

async function refresh() {
  const resp = await fetch("/status");
  const data = await resp.json();
  setButtons(data.running);
  const status = data.running
    ? `运行中 | 模式: ${data.mode} | 开始: ${data.started_at}`
    : `空闲 | 最近模式: ${data.mode || "-"} | 开始: ${data.started_at || "-"} | 结束: ${data.ended_at || "-"} | 退出码: ${data.exit_code ?? "-"}`;

  document.getElementById("status").textContent = status;
  const logs = document.getElementById("logs");
  logs.textContent = data.logs || "";
  logs.scrollTop = logs.scrollHeight;
}

refresh();
setInterval(refresh, 2000);
