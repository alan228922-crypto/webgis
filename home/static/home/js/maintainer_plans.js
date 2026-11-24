(function () {
  const editor = document.getElementById('plan-editor');
  const form = document.getElementById('plan-form');
  const table = document.getElementById('plan-table');
  const result = document.getElementById('plan-form-result');
  const cancel = document.getElementById('plan-cancel');
  if (!editor || !form || !table || !result || !cancel) return;

  const slug = editor.dataset.projectSlug;
  const plansEndpoint = editor.dataset.plansEndpoint || '/api/projects/' + slug + '/plans/';
  const metadataEndpoint = plansEndpoint.replace(/\/$/, '') + '/ui-metadata/';
  let plans = [];
  let capabilities = new Map();
  let editingId = null;
  let editingLockVersion = null;

  function apiMessage(response, fallback) {
    return response.json().catch(() => ({})).then(payload => {
      if (response.status === 401) return Promise.reject(new Error('請先登入。'));
      if (response.status === 403) return Promise.reject(new Error('你沒有執行此操作的權限。'));
      if (response.status === 404) return Promise.reject(new Error('找不到或無法查看此資料。'));
      if (response.status === 409) return Promise.reject(new Error((payload.error && payload.error.message) || '資料已更新，請重新載入後再操作。'));
      if (!response.ok) return Promise.reject(new Error((payload.error && payload.error.message) || fallback));
      return payload;
    });
  }

  function resetForm() {
    editingId = null;
    editingLockVersion = null;
    form.reset();
    form.elements.version.value = 1;
    cancel.hidden = true;
    result.textContent = '';
  }

  function addAction(row, label, action, planId) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'secondary-button';
    button.dataset.action = action;
    button.dataset.planId = planId;
    button.textContent = label;
    row.appendChild(button);
  }

  function render() {
    table.replaceChildren();
    if (!plans.length) {
      const empty = document.createElement('span');
      empty.className = 'muted';
      empty.textContent = '目前沒有可顯示的規劃。';
      table.appendChild(empty);
      return;
    }
    plans.forEach(plan => {
      const row = document.createElement('div');
      row.className = 'layer-row';
      const title = document.createElement('strong');
      title.textContent = plan.name || '(未命名)';
      row.appendChild(title);
      row.appendChild(document.createTextNode(' · ' + plan.status + ' · v' + plan.version + ' '));
      const caps = capabilities.get(String(plan.id)) || {};
      if (caps.can_edit) addAction(row, '編輯', 'edit', plan.id);
      if (caps.can_submit) addAction(row, '提交審核', 'submit', plan.id);
      if (caps.can_publish) addAction(row, '審核通過', 'publish', plan.id);
      if (caps.can_review) addAction(row, '退回修訂', 'return', plan.id);
      if (caps.can_select_geology_source) addAction(row, '選為正式地質來源', 'geology-source', plan.id);
      if (caps.can_archive) addAction(row, '封存', 'archive', plan.id);
      if (caps.can_delete) addAction(row, '刪除', 'delete', plan.id);
      if (caps.can_view_versions) addAction(row, '查看版本', 'versions', plan.id);
      if (caps.can_export) addAction(row, '匯出', 'export', plan.id);
      table.appendChild(row);
    });
  }

  function load() {
    return Promise.all([
      fetch(plansEndpoint).then(response => apiMessage(response, '載入規劃失敗。')),
      fetch(metadataEndpoint).then(response => apiMessage(response, '載入操作權限失敗。')),
    ]).then(([planPayload, metadata]) => {
      plans = planPayload.plans || [];
      capabilities = new Map((metadata.plans || []).map(item => [String(item.id), item.capabilities || {}]));
      render();
    }).catch(error => {
      table.textContent = error.message || '載入失敗，請稍後再試。';
    });
  }

  function fillForm(plan) {
    editingId = plan.id;
    editingLockVersion = plan.lock_version;
    form.elements.name.value = plan.name || '';
    form.elements.purpose.value = plan.purpose || '';
    form.elements.version.value = plan.version;
    form.elements.geometry.value = JSON.stringify(plan.geometry || {}, null, 2);
    cancel.hidden = false;
    result.textContent = '正在編輯規劃。';
  }

  function actionRequest(plan, action, body) {
    const planId = plan.id;
    const base = plansEndpoint.replace(/\/$/, '') + '/' + planId + '/';
    let endpoint = base;
    let method = 'POST';
    if (action === 'submit') endpoint += 'submit/';
    if (action === 'publish' || action === 'return') endpoint += 'review/';
    if (action === 'archive') endpoint += 'archive/';
    if (action === 'delete') method = 'DELETE';
    if (action === 'versions') { endpoint += 'versions/'; method = 'GET'; }
    if (action === 'geology-source') endpoint += 'geology-source/';
    if (action === 'export') { window.location.href = base + 'export/'; return Promise.resolve(); }
    return fetch(endpoint, {
      method: method,
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value, ...(method !== 'GET' ? {'X-WORKSITE-Lock-Version': String(plan.lock_version)} : {})},
      body: method === 'DELETE' || method === 'GET' ? undefined : JSON.stringify(body || {}),
    }).then(response => apiMessage(response, '操作失敗，請稍後再試。'));
  }

  table.addEventListener('click', function (event) {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const plan = plans.find(item => String(item.id) === button.dataset.planId);
    if (!plan) return;
    const action = button.dataset.action;
    if (action === 'edit') return fillForm(plan);
    let operation;
    if (action === 'return') {
      const reason = window.prompt('請輸入退回原因：');
      if (!reason) return;
      operation = actionRequest(plan, action, {status: 'needs_revision', reason: reason});
    } else if (action === 'publish') {
      operation = actionRequest(plan, action, {status: 'published'});
    } else if (action === 'versions') {
      actionRequest(plan, action).then(payload => { result.textContent = '版本數：' + (payload.versions || []).length; }).catch(error => { result.textContent = error.message; });
      return;
    } else if (action === 'delete' && !window.confirm('確定要刪除此草稿嗎？')) {
      return;
    } else {
      operation = actionRequest(plan, action);
    }
    result.textContent = '操作處理中。';
    operation.then(() => load()).then(() => { result.textContent = '操作完成。'; }).catch(error => { result.textContent = error.message; });
  });

  load();
  cancel.addEventListener('click', resetForm);
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    const data = new FormData(form);
    let geometry;
    try { geometry = JSON.parse(data.get('geometry') || '{}'); } catch (error) { result.textContent = 'geometry 必須是有效 JSON。'; return; }
    const endpoint = editingId ? plansEndpoint.replace(/\/$/, '') + '/' + editingId + '/' : plansEndpoint;
    const method = editingId ? 'PUT' : 'POST';
    fetch(endpoint, { method: method, headers: {'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value, ...(editingId ? {'X-WORKSITE-Lock-Version': String(editingLockVersion)} : {})}, body: JSON.stringify({name: data.get('name'), purpose: data.get('purpose'), version: Number(data.get('version')), geometry: geometry}) })
      .then(response => apiMessage(response, '儲存失敗，請稍後再試。'))
      .then(() => { result.textContent = editingId ? '已更新規劃。' : '已建立規劃。'; resetForm(); return load(); })
      .catch(error => { result.textContent = error.message; });
  });
}());
