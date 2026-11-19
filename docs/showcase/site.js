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

const contents = byId('contents');
const readerDialog = byId('readerDialog');
const docHeading = byId('docHeading');
let currentArticle;
let sections = [];
let tocLinks = [];

function renderArticle(item) {
  document.title = item.title + ' · Garden 文档';
  sidebar.querySelector('[aria-current]')?.removeAttribute('aria-current');
  const link = sidebar.querySelector('a[href="#docs/' + item.id + '"]');
  link.setAttribute('aria-current', 'page');
  link.closest('details').open = true;
  reader.innerHTML = PAGES[item.id].h;
  const title = reader.querySelector('h1');
  const lead = title.nextElementSibling;
  docHeading.innerHTML = '<div class="crumb"></div>';
  docHeading.firstElementChild.textContent = '使用文档 / ' + item.category;
  docHeading.append(title);
  if (lead?.matches('p')) docHeading.append(lead);

  sections = [...reader.querySelectorAll('h2')];
  contents.innerHTML = '<strong>本页章节</strong>';
  tocLinks = sections.map((heading, index) => {
    heading.id = 'section-' + (index + 1);
    heading.tabIndex = -1;
    const anchor = document.createElement('a');
    anchor.href = '#docs/' + item.id + '/' + heading.id;
    anchor.textContent = heading.textContent;
    contents.append(anchor);
    return anchor;
  });
  contents.hidden = !sections.length;
  byId('contentsButton').hidden = !sections.length;
  for (const block of reader.querySelectorAll('pre')) {
    const frame = document.createElement('div');
    frame.className = 'code-block';
    const toolbar = document.createElement('div');
    toolbar.className = 'term-head';
    toolbar.innerHTML = '<span><svg class="icon" aria-hidden="true"><use href="#i-code"/></svg> 代码与示例</span><button type="button"><svg class="icon" aria-hidden="true"><use href="#i-copy"/></svg><span aria-live="polite">复制</span></button>';
    toolbar.querySelector('button').addEventListener('click', event => copyText(block, event.currentTarget));
    block.before(frame);
    frame.append(toolbar, block);
  }
  const pager = document.createElement('nav');
  pager.className = 'doc-pager';
  pager.setAttribute('aria-label', '文档翻页');
  const index = articles.indexOf(item);
  if (index > 0) pager.append(pageLink(articles[index - 1], '上一节', 'prev'));
  if (index + 1 < articles.length) pager.append(pageLink(articles[index + 1], '下一节', 'next'));
  reader.append(pager);
}

function updateContents() {
  if (!currentArticle || !sections.length) return;
  const top = parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop) + 6;
  let index = 0;
  for (let i = 0; i < sections.length; i++) {
    if (sections[i].getBoundingClientRect().top > top) break;
    index = i;
  }
  if (Math.ceil(window.scrollY + window.innerHeight) >= document.documentElement.scrollHeight) {
    const linked = sections.findIndex(heading => location.hash.endsWith('/' + heading.id) && heading.getBoundingClientRect().top >= top - 10);
    index = linked < 0 ? sections.length - 1 : linked;
  }
  const previous = contents.querySelector('[aria-current]');
  if (previous !== tocLinks[index]) {
    previous?.removeAttribute('aria-current');
    tocLinks[index].setAttribute('aria-current', 'location');
  }
}
let scrollFrame = 0;
function scheduleContents() {
  if (!currentArticle || scrollFrame) return;
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = 0;
    updateContents();
  });
}
window.addEventListener('scroll', scheduleContents, {passive: true});
window.addEventListener('resize', scheduleContents);
document.fonts.ready.then(scheduleContents);

function renderRoute() {
  const hash = location.hash.slice(1);
  const [prefix, id, section] = hash.split('/');
  const item = prefix === 'docs' && articleById.get(id);
  setMenu(false);
  document.body.classList.toggle('reading', Boolean(item));
  byId('home').hidden = Boolean(item);
  byId('docs').hidden = !item;
  if (!item) {
    currentArticle = null;
    document.title = homeTitle;
    (byId(hash) || byId('home')).scrollIntoView();
    return;
  }
  if (item !== currentArticle) renderArticle(item);
  currentArticle = item;
  const heading = sections.find(element => element.id === section);
  if (heading) {
    heading.focus({preventScroll:true});
    heading.scrollIntoView();
  } else {
    window.scrollTo(0, 0);
    docHeading.focus({preventScroll:true});
  }
  updateContents();
}
window.addEventListener('hashchange', renderRoute);
docHeading.tabIndex = -1;
renderRoute();

byId('menuBtn').addEventListener('click', () => setMenu(menu.hidden));
menu.addEventListener('click', event => {
  if (event.target.closest('a')) setMenu(false);
});
document.querySelector('.skip').addEventListener('click', event => {
  event.preventDefault();
  (byId('docs').hidden ? byId('main-content') : docHeading).focus();
});
function openDrawer(source, title) {
  const drawer = byId('drawerContents');
  drawer.replaceChildren(...[...source.children].slice(1).map(child => child.cloneNode(true)));
  byId('readerDialogTitle').textContent = title;
  readerDialog.showModal();
}
byId('libraryButton').addEventListener('click', () => openDrawer(sidebar, '文档目录'));
byId('contentsButton').addEventListener('click', () => openDrawer(contents, '本页章节'));
for (const btn of document.querySelectorAll('[data-dismiss]')) {
  btn.addEventListener('click', () => btn.closest('dialog').close());
}
function followDialogLink(event) {
  const link = event.target.closest('a');
  if (!link) return;
  event.currentTarget.closest('dialog').close();
  if (link.hash === location.hash) renderRoute();
}
byId('drawerContents').addEventListener('click', followDialogLink);
contents.addEventListener('click', event => {
  if (event.target.closest('a')?.hash === location.hash) renderRoute();
});

document.addEventListener('keydown', event => {
  if (event.isComposing || event.altKey) return;
  if (event.key === 'Escape' && !menu.hidden) {
    setMenu(false);
    byId('menuBtn').focus();
  }
  const slash = event.key === '/' && !event.target.matches('input, textarea');
  const shortcut = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k';
  if ((slash || shortcut) && !document.querySelector('dialog[open]')) {
    event.preventDefault();
    openSearch();
  }
});
const searchIndex = articles.flatMap(item => {
  const template = document.createElement('template');
  template.innerHTML = PAGES[item.id].h;
  const headings = [...template.content.querySelectorAll('h2')];
  return [
    item,
    ...headings.map((heading, index) => ({
      id: item.id + '/section-' + (index + 1), title: heading.textContent, category: item.title
    }))
  ];
});
function showMatches() {
  const query = searchInput.value.trim().toLowerCase();
  const matches = query ? searchIndex.filter(item => item.title.toLowerCase().includes(query) || (!item.id.includes('/') && item.category.toLowerCase().includes(query))) : articles.slice(0, 4);
  byId('searchStatus').textContent = query ? (matches.length ? matches.length + ' 个匹配结果' : '没有匹配结果，试试「安装」或「项目」。') : '快速开始';
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
dialog.addEventListener('keydown', event => {
  if (event.isComposing) return;
  const links = [...byId('searchResults').children];
  if (event.key === 'Enter' && event.target === searchInput) links[0]?.click();
  if (!['ArrowDown', 'ArrowUp'].includes(event.key) || !links.length) return;
  event.preventDefault();
  const index = links.indexOf(document.activeElement);
  const next = index + (event.key === 'ArrowDown' ? 1 : -1);
  if (next < 0) searchInput.focus();
  else links[Math.min(next, links.length - 1)].focus();
});
byId('searchResults').addEventListener('click', followDialogLink);

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

const imageDialog = byId('imageDialog');
const scaleButton = byId('imageScale');
byId('inspectShot').addEventListener('click', () => {
  const preview = byId('previewImg');
  const image = byId('largeImage');
  byId('imageTitle').textContent = byId('previewTitle').textContent;
  image.src = preview.src;
  image.alt = preview.alt;
  image.parentElement.classList.remove('actual-size');
  scaleButton.setAttribute('aria-pressed', 'false');
  imageDialog.showModal();
  image.parentElement.scrollTo(0, 0);
});
scaleButton.addEventListener('click', () => {
  const zoom = byId('largeImage').parentElement.classList.toggle('actual-size');
  scaleButton.setAttribute('aria-pressed', String(zoom));
});

const sampleDetails = {
  plan: ['2 个入口', '计划为每个入口固定分配 20 个请求。'],
  observations: ['5 条观察', '匿名观察 1 条，两个登录身份各 2 条。'],
  delivery: ['5 项资产', '交付样本中 1 项人工保留，4 项待复核。']
};
const pins = [...document.querySelectorAll('[data-sample]')];
for (const pin of pins) {
  pin.addEventListener('click', () => {
    const [title, detail] = sampleDetails[pin.dataset.sample];
    for (const option of pins) option.setAttribute('aria-pressed', String(option === pin));
    const caption = byId('sampleCaption');
    caption.querySelector('strong').textContent = title;
    caption.querySelector('span').textContent = detail;
  });
}
async function copyText(block, button) {
  const status = button.querySelector('span');
  const label = block.id === 'commands' ? '复制命令' : '复制';
  try {
    await navigator.clipboard.writeText(block.textContent);
    status.textContent = '已复制';
  } catch {
    const range = document.createRange();
    range.selectNodeContents(block);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    status.textContent = '已选中，请复制';
  }
  clearTimeout(Number(button.dataset.reset));
  button.dataset.reset = setTimeout(() => { status.textContent = label; }, 2000);
}
byId('copyCmd').addEventListener('click', event => copyText(byId('commands'), event.currentTarget));
