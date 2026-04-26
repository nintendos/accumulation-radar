async function waitForTaskIdle(maxSec) {
  const deadline = Date.now() + maxSec * 1000;
  while (Date.now() < deadline) {
    const r = await fetch("/status");
    const s = await r.json();
    if (!s.running) {
      return true;
    }
    await new Promise((res) => setTimeout(res, 2000));
  }
  return false;
}

async function runMode(mode) {
  const resp = await fetch("/run?mode=" + mode, { method: "POST" });
  const data = await resp.json();
  if (!data.ok) {
    alert(data.message || "启动失败");
    await refresh();
    return;
  }
  await refresh();
  const budget = mode === "oi" ? 3600 : 7200;
  await waitForTaskIdle(budget);
  if (mode === "oi" || mode === "full") {
    await refreshStrategies();
  }
  await refreshResults();
}

function setButtons(disabled) {
  document.getElementById("btn-pool").disabled = disabled;
  document.getElementById("btn-oi").disabled = disabled;
  document.getElementById("btn-full").disabled = disabled;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function strategyCardHtml(item, group, bust) {
  const sym = item.sym || `${item.coin}USDT`;
  const coin = item.coin || sym.replace("USDT", "");
  const chartUrl = `/api/chart?symbol=${encodeURIComponent(sym)}&interval=1h&limit=48&t=${bust}`;
  let meta = "";
  if (group === "hot_coins") {
    meta = `热度 ${Number(item.heat || 0).toFixed(0)} | 涨 ${Number(item.px_chg || 0).toFixed(1)}% | OI6h ${Number(
      item.d6h || 0
    ).toFixed(1)}%`;
  } else if (group === "chase") {
    meta = `费率 ${Number(item.fr_pct || 0).toFixed(3)}% ${item.trend || ""} | 涨 ${Number(item.px_chg || 0).toFixed(
      1
    )}%`;
  } else if (group === "combined") {
    meta = `${item.total} 分 | 费${item.f_sc} 市${item.m_sc} 横${item.s_sc} OI${item.o_sc}`;
  } else if (group === "ambush") {
    meta = `${item.total} 分 | 市${item.m_sc} OI${item.o_sc} 横${item.s_sc} 费${item.f_sc} | OI6h ${Number(
      item.d6h || 0
    ).toFixed(1)}%`;
  }
  return `
    <article class="card card-compact">
      <div class="top">
        <div class="coin">${coin}</div>
        <div class="meta">${sym}</div>
      </div>
      <img class="chart" src="${chartUrl}" alt="${sym}" loading="lazy" />
      <div class="kv">${meta}</div>
    </article>
  `;
}

function renderStrategies(data) {
  const wrap = document.getElementById("strategies-wrap");
  const summary = document.getElementById("strategies-summary");
  if (!data.ok) {
    summary.textContent = data.error || "加载失败";
    wrap.innerHTML = "";
    return;
  }
  const m = data.meta || {};
  summary.textContent = `生成时间 ${data.generated_at || "-"} | 热度 ${m.hot_coins_total ?? 0} 条 · 追多 ${m.chase_total ?? 0} · 综合 ${m.combined_total ?? 0} · 埋伏 ${m.ambush_total ?? 0}（各列展示前若干条）`;
  const bust = Date.now();
  const cols = [
    { key: "hot_coins", title: "热度榜", subtitle: "CG 热搜 + 成交量暴增" },
    { key: "chase", title: "追多", subtitle: "按负费率排序" },
    { key: "combined", title: "综合", subtitle: "费率+市值+横盘+OI" },
    { key: "ambush", title: "埋伏", subtitle: "收筹池内" },
  ];
  let html = `<div class="strategy-grid">`;
  for (const col of cols) {
    const items = data[col.key] || [];
    const inner = items.length
      ? items.map((it) => strategyCardHtml(it, col.key, bust)).join("")
      : `<div class="subtle">暂无</div>`;
    html += `<div class="strategy-col"><h3>${col.title}</h3><div class="strategy-sub">${col.subtitle}</div><div class="cards cards-tight">${inner}</div></div>`;
  }
  html += `</div>`;
  if (data.highlights && data.highlights.length) {
    html += `<div class="highlights"><strong>值得关注</strong><ul>`;
    for (const h of data.highlights) {
      html += `<li>${escapeHtml(h)}</li>`;
    }
    html += `</ul></div>`;
  }
  wrap.innerHTML = html;
}

async function refreshStrategies() {
  const btn = document.getElementById("btn-strategies");
  const summary = document.getElementById("strategies-summary");
  const wrap = document.getElementById("strategies-wrap");
  if (btn) btn.disabled = true;
  summary.textContent = "正在计算三策略（请稍候，可能 1–3 分钟）...";
  wrap.innerHTML = "";
  try {
    const resp = await fetch("/api/strategies?top=10");
    const data = await resp.json();
    renderStrategies(data);
  } catch (e) {
    summary.textContent = "请求失败，请重试";
    wrap.innerHTML = "";
  } finally {
    if (btn) btn.disabled = false;
  }
}

function buildCard(item, bust) {
  const safeCoin = item.coin || item.symbol;
  const chartUrl = `/api/chart?symbol=${encodeURIComponent(item.symbol)}&interval=1h&limit=48&t=${bust}`;
  return `
    <article class="card">
      <div class="top">
        <div class="coin">${safeCoin}</div>
        <div class="meta">${item.symbol}</div>
      </div>
      <img class="chart" src="${chartUrl}" alt="${item.symbol} mini kline" loading="lazy" />
      <div class="kv">
        分数 ${item.score} | 状态 ${item.status || "-"} | 横盘 ${item.sideways_days} 天<br/>
        波动 ${item.range_pct}% | 现价 ${item.current_price}
      </div>
    </article>
  `;
}

async function refreshResults() {
  const summary = document.getElementById("result-summary");
  const cards = document.getElementById("cards");
  summary.textContent = "正在加载标的...";
  try {
    const resp = await fetch("/api/results?limit=12");
    const data = await resp.json();
    if (!data.ok) {
      summary.textContent = data.message || "加载失败";
      cards.innerHTML = "";
      return;
    }
    summary.textContent = `共 ${data.count} 个标的（按收筹分数排序）`;
    const bust = Date.now();
    cards.innerHTML = (data.items || []).map((it) => buildCard(it, bust)).join("");
    if (!data.items || data.items.length === 0) {
      cards.innerHTML = `<div class="subtle">暂无数据，请先运行 pool 模式。</div>`;
    }
  } catch (err) {
    summary.textContent = "加载失败，请稍后重试";
    cards.innerHTML = "";
  }
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
refreshResults();
setInterval(refresh, 2000);
setInterval(refreshResults, 15000);
