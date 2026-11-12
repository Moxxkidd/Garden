/* Local display preferences only; the server remains authoritative for selection and totals. */
(function () {
  const picker = document.getElementById('column-picker');
  if (!picker) return;
  const storageKey = 'garden.workbench.columns.v1';
  let hidden;
  try { hidden = new Set(JSON.parse(localStorage.getItem(storageKey) || '[]')); }
  catch (error) { if (!(error instanceof SyntaxError || error instanceof DOMException)) throw error; hidden = new Set(); }
  function columns() {
    picker.querySelectorAll('[data-column]').forEach(input => {
      input.checked = !hidden.has(input.dataset.column);
      document.querySelectorAll('[data-col="' + input.dataset.column + '"]').forEach(cell => { cell.hidden = !input.checked; });
    });
  }
  columns();
  picker.addEventListener('change', event => {
    const input = event.target;
    if (input.checked) hidden.delete(input.dataset.column); else hidden.add(input.dataset.column);
    columns();
    try { localStorage.setItem(storageKey, JSON.stringify([...hidden])); }
    catch (error) { if (!(error instanceof DOMException)) throw error; }
  });
  const count = document.getElementById('selected-count');
  function selectedCount() {
    if (!count) return;
    count.textContent = '已选 ' + document.querySelectorAll('[name="asset_ids"][form="bulk-review"]:checked').length + ' 个资产；只修改明确勾选的 ID。';
  }
  document.addEventListener('change', selectedCount);
  window.addEventListener('pageshow', selectedCount);
  selectedCount();
})();
