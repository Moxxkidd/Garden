const byId = (id) => document.getElementById(id);
const articles = TREE.flatMap(group => group.items.map(item => ({ ...item, category: group.cat })));
const articleById = new Map(articles.map(item => [item.id, item]));
const sidebar = byId('sidebar');
const reader = byId('docContent');
const menu = byId('mobileNav');
const dialog = byId('searchDialog');
const searchInput = byId('searchInput');
const homeTitle = document.title;

function articleLink(item) {
  const link = document.createElement('a');
  link.href = '#docs/' + item.id;
  link.textContent = item.title;
  return link;
}

sidebar.innerHTML = '<h2>文档目录</h2>';
for (const group of TREE) {
  const section = document.createElement('details');
  const label = document.createElement('summary');
  label.textContent = group.cat;
  section.append(label, ...group.items.map(articleLink));
  sidebar.append(section);
}

function setMenu(open) {
  menu.hidden = !open;
  byId('menuBtn').setAttribute('aria-expanded', String(open));
  byId('menuBtn').setAttribute('aria-label', open ? '关闭导航' : '打开导航');
}

function pageLink(item, label, className) {
  const link = articleLink(item);
  const caption = document.createElement('small');
  caption.textContent = label;
  link.prepend(caption);
  link.className = className;
  return link;
}

function renderRoute() {
  const hash = location.hash.slice(1);
  const item = hash.startsWith('docs/') && articleById.get(hash.slice(5));
  setMenu(false);
  sidebar.classList.remove('visible');
  byId('openDirectory').hidden = !item;
  byId('home').hidden = Boolean(item);
  byId('docs').hidden = !item;
  if (!item) {
    document.title = homeTitle;
    (byId(hash) || byId('home')).scrollIntoView();
    return;
  }
  document.title = item.title + ' · Garden 文档';
  const activeLink = sidebar.querySelector('[aria-current]');
  if (activeLink) activeLink.removeAttribute('aria-current');
  const link = sidebar.querySelector('a[href="#docs/' + item.id + '"]');
  link.setAttribute('aria-current', 'page');
  link.closest('details').open = true;
  reader.innerHTML = '<div class="crumb"></div>' + PAGES[item.id].h;
  reader.firstElementChild.textContent = '使用文档 / ' + item.category;
  const pager = document.createElement('nav');
  pager.className = 'doc-pager';
  pager.setAttribute('aria-label', '文档翻页');
  const index = articles.indexOf(item);
  if (index > 0) pager.append(pageLink(articles[index - 1], '上一节', 'prev'));
  if (index + 1 < articles.length) pager.append(pageLink(articles[index + 1], '下一节', 'next'));
  reader.append(pager);
  window.scrollTo(0, 0);
  reader.focus({ preventScroll: true });
}
window.addEventListener('hashchange', renderRoute);
renderRoute();

byId('menuBtn').addEventListener('click', () => setMenu(menu.hidden));
menu.addEventListener('click', event => {
  if (event.target.closest('a')) setMenu(false);
});
byId('openDirectory').addEventListener('click', () => {
  setMenu(false);
  sidebar.classList.add('visible');
  sidebar.scrollIntoView();
  sidebar.querySelector('[aria-current]').focus({ preventScroll: true });
});
sidebar.addEventListener('click', event => {
  if (event.target.closest('a')?.hash === location.hash) {
    sidebar.classList.remove('visible');
    reader.focus();
  }
});
document.querySelector('.skip').addEventListener('click', event => {
  event.preventDefault();
  (byId('docs').hidden ? byId('main-content') : reader).focus();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !menu.hidden) {
    setMenu(false);
    byId('menuBtn').focus();
  }
  if (event.key === '/' && !dialog.open && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.matches('input, textarea')) {
    event.preventDefault();
    openSearch();
  }
});

function showMatches() {
  const query = searchInput.value.trim().toLowerCase();
  const matches = query ? articles.filter(item => (item.title + ' ' + item.category).toLowerCase().includes(query)) : articles.slice(0, 4);
  byId('searchStatus').textContent = query ? (matches.length ? matches.length + ' 篇匹配文档' : '没有匹配的文档，试试「安装」或「项目」。') : '快速开始';
  byId('searchResults').replaceChildren(...matches.map(item => {
    const link = articleLink(item);
    const group = document.createElement('small');
    group.textContent = item.category;
    link.append(group);
    return link;
  }));
}
function openSearch() {
  setMenu(false);
  searchInput.value = '';
  showMatches();
  dialog.showModal();
  searchInput.focus();
}
byId('searchTrigger').addEventListener('click', openSearch);
byId('closeSearch').addEventListener('click', () => dialog.close());
searchInput.addEventListener('input', showMatches);
searchInput.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.isComposing) byId('searchResults').querySelector('a')?.click();
});
byId('searchResults').addEventListener('click', event => {
  if (event.target.closest('a')) dialog.close();
});

const previews = {
  plan: { title: '多入口计划', alt: '两个入口的身份绑定与固定预算', caption: '提交前核对两个入口、各自的身份与请求配额。', height: 1000 },
  delivery: { title: '项目交付', alt: '项目交付页面展示概要、资产与证据', caption: '按当前筛选生成完整交付，核对资产主体与身份观察。', height: 2322 }
};
for (const button of document.querySelectorAll('[data-view]')) {
  button.addEventListener('click', () => {
    const view = button.dataset.view;
    const preview = previews[view];
    const url = 'quality/v2-assets/' + view + '-desktop.png';
    byId('previewTitle').textContent = preview.title;
    byId('previewCaption').textContent = preview.caption;
    byId('previewFull').href = url;
    const img = byId('previewImg');
    img.alt = preview.alt;
    img.height = preview.height;
    img.src = url;
    img.parentElement.scrollTop = 0;
    for (const option of document.querySelectorAll('[data-view]')) option.setAttribute('aria-pressed', String(option === button));
  });
}

let copyReset;
byId('copyCmd').addEventListener('click', async () => {
  const status = byId('copyCmd').querySelector('span');
  try {
    await navigator.clipboard.writeText(byId('commands').textContent);
    status.textContent = '已复制';
  } catch {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(byId('commands'));
    selection.removeAllRanges();
    selection.addRange(range);
    status.textContent = '已选中，请复制';
  }
  clearTimeout(copyReset);
  copyReset = setTimeout(() => { status.textContent = '复制命令'; }, 2400);
});
