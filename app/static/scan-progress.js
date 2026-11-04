/* One SSE connection; one low-frequency status-API fallback. No page reloads. */
(() => {
  const root = document.getElementById('scan-progress-root');
  const initial = document.getElementById('scan-progress-initial');
  if (!root || !initial || root.dataset.progressBound) return;
  root.dataset.progressBound = 'true';
  let source, pollTimer, timer, state = JSON.parse(initial.textContent);
  const active = s => ['queued', 'running'].includes(s.status);
  const put = (key, value) => root.querySelectorAll(`[data-progress="${key}"]`).forEach(el => {
    el.textContent = value == null ? '未知' : String(value);
  });
  function stop() {
    if (source) source.close();
    clearTimeout(pollTimer); clearInterval(timer);
  }
  function tick() {
    if (!root.isConnected) { stop(); return; }
    if (!state.started_at) return;
    const stamp = state.started_at;
    const start = Date.parse(/(?:Z|[+-]\d{2}:\d{2})$/.test(stamp) ? stamp : stamp + 'Z');
    root.querySelectorAll('[data-scan-started]').forEach(el => {
      el.textContent = `已用时 ${Math.max(0, Math.floor((Date.now() - start) / 1000))} 秒`;
    });
  }
  function apply(snapshot) {
    if (!root.isConnected) { stop(); return; }
    state = snapshot;
    ['execution', 'progress_text', 'current_stage', 'status', 'coverage', 'findings', 'asset_count', 'evidence_count'].forEach(key => put(key, state[key]));
    put('activity', state.activity || (active(state) ? '等待下一次观察' : '采集已结束'));
    put('waiting_reason', state.waiting_reason || '');
    const bar = root.querySelector('[role="progressbar"]');
    bar.setAttribute('aria-valuenow', state.progress);
    bar.firstElementChild.style.width = `${state.progress}%`;
    (state.stages || []).forEach(stage => {
      const card = [...root.querySelectorAll('[data-stage]')].find(el => el.dataset.stage === stage.name);
      if (card) {
        card.querySelector('[data-stage-status]').textContent = stage.status;
        card.querySelector('[data-stage-summary]').textContent = {
          pending: '尚未开始', running: '阶段执行中', completed: '阶段已结束，详情见最终报告',
          failed: '阶段失败，详情见最终诊断', skipped: '阶段已跳过', interrupted: '阶段已中断'
        }[stage.status] || stage.status;
      }
    });
    tick();
    if (!active(state)) {
      root.querySelector('#final-scan-link').hidden = false;
      stop();
    }
  }
  async function poll() {
    if (!root.isConnected || !active(state)) return;
    try {
      const response = await fetch(`/api/scans/${root.dataset.runId}`, {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const view = await response.json();
      apply(view.execution_feedback);
    } catch (_) {
      put('waiting_reason', '状态连接中断，等待下一次状态查询；当前结果可能已过期');
    }
    if (active(state) && root.isConnected) pollTimer = setTimeout(poll, 4000);
  }
  function fallback() {
    if (source) source.close();
    clearTimeout(pollTimer);
    poll();
  }
  timer = setInterval(tick, 1000);
  apply(state);
  if (active(state)) {
    if (window.EventSource) {
      source = new EventSource(`/api/scans/${root.dataset.runId}/events`);
      source.addEventListener('snapshot', event => apply(JSON.parse(event.data)));
      source.onerror = fallback;
    } else fallback();
  }
  window.addEventListener('pagehide', stop, {once: true});
})();
