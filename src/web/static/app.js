const form = document.querySelector('#search');
const goal = document.querySelector('#goal');
const threshold = document.querySelector('#threshold');
const submit = document.querySelector('#submit');
const results = document.querySelector('#results');
const progress = document.querySelector('#progress');
const progressBar = document.querySelector('#progress-bar');
const code = document.querySelector('#code');
const hits = document.querySelector('#hits');
const summary = document.querySelector('#summary');

let passed = 0, assessed = 0;
function setBusy(busy) {
  goal.disabled = busy; threshold.disabled = busy; submit.disabled = busy;
  form.setAttribute('aria-busy', String(busy));
  document.querySelectorAll('.chip').forEach(button => button.disabled = busy);
  document.querySelector('#build-catalog').disabled = busy;
  document.querySelector('#settings-form button').disabled = busy;
}
goal.addEventListener('keydown', event => {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  if (!goal.disabled && !submit.disabled && !event.repeat) form.requestSubmit(submit);
});
function counts(total) {
  document.querySelector('#assessed').textContent = `${assessed} / ${total} assessed`;
  document.querySelector('#passed-count').textContent = passed;
  document.querySelector('#declined-count').textContent = assessed - passed;
  document.querySelector('#remaining-count').textContent = Math.max(0, total - assessed);
  summary.replaceChildren(); const strong = document.createElement('strong'); strong.textContent = `${passed} passed`; summary.append(strong, ` / ${assessed} assessed`);
}

async function showSource(runId, method) {
  const response = await fetch(`/api/search/${runId}/source?method=${encodeURIComponent(method)}`);
  const payload = await response.json();
  document.querySelector('#source-path').textContent = response.ok ? payload.path : 'Source unavailable';
  code.textContent = response.ok
    ? `${payload.path}\n\n${payload.lines.map((line, index) => `${String(payload.start_line + index).padStart(4)}  ${line}`).join('\n')}`
    : payload.detail;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (submit.disabled || !goal.value.trim()) return;
  passed = 0; assessed = 0; counts(0);
  results.replaceChildren(); hits.replaceChildren();
  code.textContent = 'Select a method to view its source.';
  progress.textContent = 'Starting local search…'; progressBar.value = 0; setBusy(true);
  try {
  const response = await fetch('/api/search', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({goal: goal.value, threshold: Number(threshold.value)})});
  const payload = await response.json();
  if (!response.ok) { progress.textContent = payload.detail; setBusy(false); return; }
  const stream = new EventSource(`/api/search/${payload.run_id}/events`);
  stream.onmessage = async ({data}) => {
    const eventPayload = JSON.parse(data);
    if (eventPayload.type === 'evaluation') {
      assessed = eventPayload.current; if (eventPayload.passed) passed++; counts(eventPayload.total);
      document.querySelector('#percentage').textContent = `${Math.round(assessed / eventPayload.total * 100)}% complete`;
      progress.textContent = `Assessing catalog ${eventPayload.current} / ${eventPayload.total}`;
      progressBar.max = eventPayload.total; progressBar.value = eventPayload.current;
      const row = document.createElement('tr'); row.tabIndex = 0;
      const values = [`${eventPayload.current}/${eventPayload.total}`, eventPayload.method.split('.').slice(-2).join('.'), eventPayload.score.toFixed(2), eventPayload.passed ? '✓ Passed' : '− Decline', `${eventPayload.file}:${eventPayload.line}`];
      values.forEach((value, index) => { const cell = document.createElement('td'); if (index === 3) { const badge = document.createElement('span'); badge.className = `pill ${eventPayload.passed ? 'passed' : 'decline'}`; badge.textContent = value; cell.append(badge); } else cell.textContent = value; row.append(cell); });
      row.title = eventPayload.method;
      const select = () => { results.querySelectorAll('tr').forEach(item => item.classList.remove('selected')); row.classList.add('selected'); showSource(payload.run_id, eventPayload.method); };
      row.addEventListener('click', select); row.addEventListener('keydown', e => { if(e.key === 'Enter') select(); }); results.append(row);
      const resultsViewport = results.closest('.table-wrap');
      resultsViewport.scrollTop = resultsViewport.scrollHeight;
    } else if (eventPayload.type === 'error') {
      progress.textContent = eventPayload.message;
    } else if (eventPayload.type === 'complete') {
      summary.textContent = `${eventPayload.hits.length} passed / ${eventPayload.total} assessed`;
      eventPayload.hits.forEach((hit) => { const item = document.createElement('li'); const button = document.createElement('button'); button.textContent = `${hit.method.split('.').slice(-2).join('.')} — ${hit.score.toFixed(2)}`; button.addEventListener('click', () => showSource(payload.run_id, hit.method)); item.append(button); hits.append(item); });
      if (!eventPayload.total) progressBar.value = 0;
      progress.textContent = progress.textContent === 'Search evaluation failed.' ? progress.textContent : 'Assessment complete';
      stream.close(); setBusy(false);
    }
  };
  stream.onerror = () => { progress.textContent = 'Search stream disconnected.'; stream.close(); setBusy(false); };
  } catch(error) {
    progress.textContent = 'Could not start search. Check the server connection and try again.';
    setBusy(false);
  }
});

document.querySelectorAll('.nav').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.nav').forEach(item => item.classList.toggle('active', item === button));
  const settings = button.dataset.view === 'settings-view';
  document.querySelector('#settings-view').hidden = !settings;
  document.querySelector('#search-view').hidden = settings;
  document.querySelector('#breadcrumb').textContent = settings ? 'Settings' : 'Search';
}));
document.querySelectorAll('.chip').forEach(button => button.addEventListener('click', () => {
  if (goal.disabled || button.disabled) return;
  goal.value = button.textContent; goal.focus();
}));
let aiService = 'Copilot';
const strategyInputs = () => document.querySelectorAll('input[name="ai-strategy"]');
const chosenStrategy = () => (document.querySelector('input[name="ai-strategy"]:checked') || {value: 'copilot'}).value;
const serviceFor = strategy => ({minicpm: 'MiniCPM', copilot: 'Copilot'})[strategy] || 'AI';
async function loadSettings(keepStatus = false) {
  const response = await fetch('/api/settings'); const data = await response.json();
  const saved = ['auto', 'minicpm', 'copilot'].includes(data.enrichment_backend) ? data.enrichment_backend : 'copilot';
  if (!document.querySelector('input[name="ai-strategy"]:checked')) document.querySelector(`input[name="ai-strategy"][value="${saved}"]`).checked = true;
  updateAiText();
  document.querySelector('#source-root').value = (data.source_roots || [data.source_root]).join('\n');
  document.querySelector('#default-threshold').value = data.threshold;
  if (!keepStatus) document.querySelector('#catalog-status').textContent = `${(data.source_roots || [data.source_root]).length} library directories · ${data.catalog_methods === null ? 'Ready to build' : data.catalog_methods + ' methods cataloged'}`;
  document.querySelector('#catalog-warning').hidden = !data.catalog_health.needs_refresh;
  document.querySelector('#catalog-warning-reasons').textContent = data.catalog_health.reasons.join('; ');
}
document.querySelector('#settings-form').addEventListener('submit', async event => {
  event.preventDefault(); const status = document.querySelector('#settings-status');
  try {
    const response = await fetch('/api/settings', {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({source_root:document.querySelector('#source-root').value,threshold:Number(document.querySelector('#default-threshold').value)})});
    const data = await response.json(); if(!response.ok) throw new Error(data.detail);
    threshold.value = data.threshold; status.textContent = 'Settings saved. Refresh the catalog after changing its directory.'; await loadSettings();
  } catch(error) { status.textContent = error.message; }
});
document.querySelector('#build-catalog').addEventListener('click', async () => {
  const mode = document.querySelector('input[name="catalog-mode"]:checked').value;
  document.querySelectorAll('input[name="catalog-mode"]').forEach(input => input.disabled = true);
  const strategy = chosenStrategy(); strategyInputs().forEach(input => input.disabled = true);
  setBusy(true); const status = document.querySelector('#catalog-status'); status.textContent = mode === 'fast' ? 'Scanning Python files locally…' : 'Scanning Python files. New or changed snippets will be analyzed with ' + ({minicpm: 'the MiniCPM service…', copilot: 'your signed-in Microsoft 365 Copilot account…'}[strategy] || 'MiniCPM if its service is reachable, otherwise your Copilot account…');
  const bar = document.querySelector('#catalog-progress');
  const label = document.querySelector('#catalog-progress-label');
  const errors = document.querySelector('#catalog-errors'); errors.replaceChildren();
  let workersPanel = document.querySelector('#catalog-workers');
  if (!workersPanel) { workersPanel = document.createElement('div'); workersPanel.id = 'catalog-workers'; bar.parentElement.append(workersPanel); }
  workersPanel.replaceChildren(); workersPanel.hidden = true;
  const workerRows = new Map();
  let estimate = document.querySelector('#catalog-eta');
  if (!estimate) { estimate = document.createElement('p'); estimate.id = 'catalog-eta'; estimate.className = 'small'; bar.parentElement.append(estimate); }
  let estimateStart = performance.now(), queuedTasks = null, finishedTasks = 0, remaining = null;
  estimate.textContent = 'Estimating completion time…';
  const updateEstimate = () => {
    if (queuedTasks === null || finishedTasks === 0) return;
    const seconds = (performance.now() - estimateStart) / 1000 / finishedTasks * Math.max(0, queuedTasks - finishedTasks);
    remaining = seconds;
    const minutes = Math.ceil(seconds / 60);
    const finish = new Date(Date.now() + seconds * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
    estimate.textContent = seconds > 0 ? `Estimated remaining: ${minutes < 1 ? 'less than 1 minute' : `about ${minutes} min`} · Estimated finish: ${finish} (local time; estimate updates as methods finish)` : 'Method processing complete; finishing cleanup and saving…';
  };
  bar.removeAttribute('value'); label.textContent = 'Scanning Python files…';
  try {
    const response = await fetch(`/api/catalog/stream?mode=${mode}&strategy=${strategy}`, {method:'POST'});
    if (!response.ok) throw new Error((await response.json()).detail);
    const cancelButton = document.querySelector('#cancel-catalog'); cancelButton.hidden = false; cancelButton.disabled = false; cancelButton.textContent = 'Cancel build';
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let pending = '', completed = false;
    while (true) {
      const {value, done} = await reader.read(); if (done) break;
      pending += decoder.decode(value, {stream:true});
      let newline;
      while ((newline = pending.indexOf('\n')) >= 0) {
        const data = JSON.parse(pending.slice(0, newline)); pending = pending.slice(newline + 1);
        if (data.type === 'error') throw new Error(data.message);
        if (data.type === 'worker_progress') {
          workersPanel.hidden = false;
          let row = workerRows.get(data.worker_id);
          if (!row) {
            const element = document.createElement('section'); element.className = 'worker-progress';
            const title = document.createElement('strong');
            const workerBar = document.createElement('progress'); workerBar.setAttribute('aria-label', `Worker ${data.worker_id} progress`);
            const detail = document.createElement('p');
            element.append(title, workerBar, detail); workersPanel.append(element);
            row = {element, title, bar:workerBar, detail}; workerRows.set(data.worker_id, row);
            [...workerRows.entries()].sort((a,b) => a[0]-b[0]).forEach(([,item]) => workersPanel.append(item.element));
          }
          const names = {starting:'Starting', processing:'Processing / waiting for ' + aiService, success:'Enriched', reused:'Reused', error:'Method failed', cleanup:'Cleaning up', finished:'Finished', finished_with_errors:'Finished with errors', interrupted:'Interrupted — remaining methods pending'};
          const workerStatus = data.status === 'cancelled' ? `Cancelled · ${data.pending || 0} pending` : data.status === 'interrupted' ? `Interrupted · ${data.pending || 0} pending; failed method saved for retry` : names[data.status] || data.status;
          row.title.textContent = `Worker ${data.worker_id} · ${data.current} / ${data.total} · ${workerStatus}`;
          row.bar.max = Math.max(1, data.total); row.bar.value = data.current;
          row.element.dataset.status = data.status;
          if (data.method) row.detail.textContent = data.method;
        }
        if (data.type === 'method_error') {
          const item = document.createElement('li'); item.textContent = `${data.method}: ${data.message}`; errors.append(item);
        }
        if (data.type === 'progress') {
          if (data.stage === 'preparing') { queuedTasks = data.queued; estimateStart = performance.now(); }
          if (data.stage === 'enriching') {
            if (data.status !== 'reused') finishedTasks++;
            updateEstimate();
            bar.value = data.total ? 90 * data.current / data.total : 90;
            label.textContent = `${data.current} / ${data.total} methods · ${data.status} · ${data.method}`;
          } else {
            label.textContent = data.stage === 'cleanup' ? (aiService === 'MiniCPM' ? 'Cleaning up…' : 'Cleaning up Copilot conversation…') : data.stage === 'saving' ? 'Saving catalog…' : 'Scanning Python files…';
          }
        } else if (data.type === 'complete') {
          estimate.textContent = data.cancelled ? 'Build cancelled — saved results retained.' : data.interrupted ? 'Build interrupted — completion estimate unavailable.' : 'Build finished.';
          completed = true; if (data.strategy) aiService = serviceFor(data.strategy);
          bar.value = data.interrupted ? (data.methods ? 90 * (data.methods - data.pending) / data.methods : 0) : 100;
          label.textContent = data.cancelled ? 'Build cancelled — successful results saved; remaining methods pending' : data.interrupted ? 'Build interrupted — successful results saved; remaining methods pending' : 'Catalog build complete';
          await loadSettings();
          status.textContent = `${data.methods} methods · ${data.files} files · ${data.errors} skipped files · ${data.reused} reused · ${data.enriched} enriched · ${data.failed} failed · ${data.pending} pending. Catalog saved.${data.mode === 'fast' ? ' Fast scan complete; ${aiService} was not used. Existing unchanged explanations were preserved.' : data.interrupted ? (aiService === 'MiniCPM' ? ' Refresh to retry pending methods.' : ' Copilot window retained for inspection. Refresh to retry pending methods.') : aiService === 'MiniCPM' ? '' : data.conversation_deleted ? ' Copilot catalog conversation deleted.' : ' Copilot conversation deletion was not confirmed (or no conversation was needed).'}`;
        }
      }
    }
    if (!completed) throw new Error('Catalog progress connection ended before completion. Refresh to check catalog status.');
  } catch(error) { status.textContent = error.message; label.textContent = 'Build did not complete'; estimate.textContent = 'Completion estimate unavailable.'; bar.value = 0;
    workerRows.forEach(row => { if (!['finished','finished_with_errors','interrupted'].includes(row.element.dataset.status)) row.title.textContent += ' · Connection lost; current worker state unknown'; });
  }
  finally { setBusy(false); document.querySelector('#cancel-catalog').hidden = true; document.querySelectorAll('input[name="catalog-mode"]').forEach(input => input.disabled = false); strategyInputs().forEach(input => input.disabled = false); updateAiText(); }
});
document.querySelector('#cancel-catalog').addEventListener('click', async event => {
  const button = event.currentTarget; button.disabled = true; button.textContent = 'Cancelling…';
  try {
    const response = await fetch('/api/catalog/cancel', {method:'POST'});
    if (!response.ok) throw new Error((await response.json()).detail);
    document.querySelector('#catalog-progress-label').textContent = 'Cancelling — saving collected results and stopping workers…';
  } catch(error) { button.disabled = false; button.textContent = 'Cancel build'; document.querySelector('#catalog-status').textContent = error.message; }
});
function updateAiText() {
  const auto = chosenStrategy() === 'auto';
  aiService = serviceFor(chosenStrategy());
  const fast = (document.querySelector('input[name="catalog-mode"]:checked') || {}).value === 'fast';
  const strategyBox = document.querySelector('#ai-strategy'); if (strategyBox) strategyBox.disabled = fast || !document.querySelector('#cancel-catalog').hidden;
  const minicpm = aiService === 'MiniCPM';
  const note = document.querySelector('.catalog-note');
  if (note) note.textContent = auto ? 'Auto uses the MiniCPM service when it is reachable and otherwise opens Copilot with your signed-in Microsoft 365 account.' : minicpm ? 'Changed snippets are sent to the configured MiniCPM service.' : 'Changed snippets are analyzed using your signed-in Microsoft 365 Copilot account. The catalog conversation is deleted after completion.';
  const heading = document.querySelector('.catalog-heading p'); if (heading) heading.textContent = `Source index & ${aiService} explanations`;
  const help = document.querySelector('#catalog-mode-help');
  if (help && document.querySelector('input[name="catalog-mode"]:checked')) help.textContent = document.querySelector('input[name="catalog-mode"]:checked').value === 'fast'
    ? `Fast scans Python files locally without using ${aiService}. Existing explanations are preserved for unchanged methods; new or changed methods can be enriched later with AI-assisted mode.`
    : auto ? 'AI-assisted uses the MiniCPM service when it is reachable, otherwise a dedicated Copilot window. Do not interact with the Copilot window until cataloging finishes.'
    : aiService === 'MiniCPM' ? 'AI-assisted sends new or changed methods to the configured MiniCPM service to explain them. No browser window is opened.'
    : 'AI-assisted opens a dedicated Copilot window to explain new or changed methods. You can keep it in the background and use other windows, but do not type, navigate, close tabs, or otherwise interact with that Copilot window until cataloging finishes.';
}
document.querySelectorAll('input[name="catalog-mode"], input[name="ai-strategy"]').forEach(input => input.addEventListener('change', updateAiText));

document.querySelector('#refresh-warning').addEventListener('click', event => {
  event.preventDefault();
  document.querySelector('.nav[data-view="settings-view"]').click();
  document.querySelector('#catalog').scrollIntoView({behavior: 'smooth', block: 'center'});
  document.querySelector('#build-catalog').focus({preventScroll: true});
});
window.addEventListener('focus', () => { if (!submit.disabled) loadSettings(true).catch(() => {}); });
loadSettings().catch(() => { document.querySelector('#catalog-status').textContent = 'Could not load settings.'; });
