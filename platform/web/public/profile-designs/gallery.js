(() => {
  'use strict';
  const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const avatar = (extra = '') => `<span class="avatar ${extra}" aria-hidden="true"></span>`;
  const copy = '<span class="identity-copy"><strong>Evgeniy</strong><small>zhenl-b@yandex.ru</small></span>';
  const identity = (extra = '', av = '') => `<button class="identity ${extra}" data-demo="profile" aria-label="Открыть профиль Evgeniy">${avatar(av)}${copy}</button>`;
  const exit = (extra = '', text = 'Выйти') => `<button class="exit ${extra}" data-demo="exit" aria-label="Выйти из аккаунта">${icon('exit')}<span>${text}</span></button>`;
  const exitIcon = (extra = '') => `<button class="exit-icon ${extra}" data-demo="exit" aria-label="Выйти из аккаунта" title="Выйти из аккаунта">${icon('exit')}</button>`;
  const profile = (extra = '') => `<button class="profile-action ${extra}" data-demo="profile">${icon('user')}<span>Профиль</span></button>`;
  const groups = { minimal: 'Минимум', cards: 'Карточки', layout: 'Компоновка', menus: 'Меню', bold: 'Характер' };
  const designs = [
    ['minimal', 'Тихая строка', 'Аватар и подписи остаются главными. Выход выровнен с текстом и не перетягивает внимание.', `${identity()}${exit()}`],
    ['minimal', 'Тонкая граница', 'Небольшой отступ и одна горизонтальная линия отделяют профиль от остальной навигации.', `<div class="hairline"></div>${identity()}${exit()}`],
    ['minimal', 'В одну линию', 'Профиль и выход собраны в один ряд. Компактная кнопка выхода подписана во всплывающей подсказке.', `<div class="row">${identity()}${exitIcon()}</div>`],
    ['minimal', 'Без аватара', 'Типографический вариант: имя, почта и спокойная текстовая ссылка на выход.', `<div class="text-only"><button class="identity" data-demo="profile">${copy}</button>${exit('', 'Выйти ↗')}</div>`],
    ['minimal', 'Инициал', 'Буква вместо фотографии делает блок более графичным. Выход продолжает линию имени.', `<div class="row">${identity('', 'initial')}${exitIcon()}</div>`],
    ['minimal', 'Боковой акцент', 'Тонкая розовая линия связывает профиль с фирменным цветом. Остальные элементы остаются нейтральными.', `<div class="accent-rail">${identity()}${exit()}</div>`],
    ['cards', 'Мягкая карточка', 'Белая подложка с мягкой рамкой. Выход отделён внутри карточки и выровнен по левому краю.', `<div class="panel">${identity()}<div class="separator"></div>${exit('spread')}</div>`],
    ['cards', 'Парящая', 'Лёгкая тень вместо явной рамки. Круглый аватар и выход у правого края создают цельную карточку.', `<div class="panel floating"><div class="row">${identity('', 'round')}${exitIcon()}</div></div>`],
    ['cards', 'Розовая вуаль', 'Светлый фирменный фон, крупный радиус и короткая кнопка выхода рядом с профилем.', `<div class="panel blush">${identity('', 'round')}${exit('pill')}</div>`],
    ['cards', 'Два этажа', 'Отдельные зоны для профиля и выхода. Нижняя полоса читается как самостоятельное действие.', `<div class="panel split">${identity()}${exit('bottom-action')}</div>`],
    ['cards', 'Капсула', 'Одна компактная овальная форма. Фотография и круглая кнопка балансируют края.', `<div class="capsule">${identity('', 'round')}${exitIcon()}</div>`],
    ['cards', 'Профиль на обложке', 'Тонкая розовая полоса и аватар на стыке. Выход расположен внизу карточки.', `<div class="cover-card"><div class="cover-band"></div>${identity('', 'round')}${exit()}</div>`],
    ['layout', 'По центру', 'Симметричная композиция с увеличенной фотографией. Почта и выход находятся под именем.', `<div class="centered">${identity('', 'round av-large')}${exit('pill')}</div>`],
    ['layout', 'Кнопка во всю ширину', 'Профиль над отдельной широкой кнопкой. Самый явный и удобный для нажатия выход.', `${identity()}${exit('full-button')}`],
    ['layout', 'Почта отдельной строкой', 'В верхнем ряду — аватар, имя и выход. Почта вынесена ниже, поэтому подписи не сжимаются.', `<div class="row"><button class="identity" data-demo="profile">${avatar('round')}<strong>Evgeniy</strong></button>${exitIcon()}</div><button class="email-line" data-demo="profile">zhenl-b@yandex.ru ${icon('arrow')}</button>`],
    ['layout', 'Профиль и выход', 'Две одинаковые по размеру кнопки под данными пользователя. Действия легко различить.', `${identity()}<div class="action-pair">${profile()}${exit()}</div>`],
    ['layout', 'Вертикальная ось', 'Аватар задаёт левую колонку; имя, почта и выход образуют единую текстовую ось.', `<div class="axis">${avatar('round')}<div><button class="identity" data-demo="profile">${copy}</button>${exit()}</div></div>`],
    ['layout', 'Имя крупным', 'Более выразительная типографика. Фотография вынесена к правому краю, выход завершает композицию.', `<div class="name-first"><button class="identity" data-demo="profile">${copy}${avatar('round')}</button>${exit()}</div>`],
    ['menus', 'Меню по троеточию', 'Кнопка с троеточием раскрывает профиль и выход. В галерее меню показано открытым; его можно свернуть.', `<details class="account-menu dots-menu" open><summary>${avatar()}${copy}<span class="menu-trigger">${icon('dots')}</span></summary><div class="menu-surface">${profile()}${exit()}</div></details>`],
    ['menus', 'Всплывающий список', 'Меню открывается над компактной строкой профиля. Такой блок удобно закрепить внизу боковой панели.', `<details class="account-menu popover-menu" open><summary>${avatar('round')}${copy}${icon('chevron')}</summary><div class="menu-surface">${profile()}<div class="separator"></div>${exit()}</div></details>`],
    ['menus', 'Раскрывающаяся карточка', 'Профиль работает как единая кнопка. Выход раскрывается внутри того же контура.', `<details class="account-menu accordion-menu" open><summary>${avatar()}${copy}${icon('chevron')}</summary><div class="menu-surface">${exit('spread')}</div></details>`],
    ['menus', 'Аватар как кнопка', 'Минимальный док для узкой панели. Нажатие на фотографию раскрывает имя, почту и выход.', `<details class="account-menu avatar-menu" open><summary aria-label="Открыть меню профиля">${avatar('round')}<span class="tiny-chevron">${icon('chevron')}</span></summary><div class="menu-surface"><button class="identity" data-demo="profile">${copy}</button><div class="separator"></div>${exit()}</div></details>`],
    ['menus', 'Быстрые действия', 'Фотография, имя и две небольшие круглые кнопки ниже. Подписи к действиям видны при наведении.', `<div class="quick-profile">${identity('', 'round')}<div class="quick-actions"><button data-demo="profile" aria-label="Открыть профиль" title="Открыть профиль">${icon('user')}</button>${exitIcon()}</div></div>`],
    ['menus', 'Командная панель', 'Информация и действия собраны в небольшой док. Фон выделяет только ряд кнопок.', `<div class="command-dock">${identity('', 'round')}<div class="dock-actions">${profile()}${exit()}</div></div>`],
    ['bold', 'Тёмный контур', 'Угольная карточка на светлой панели, увеличенный аватар и мягко выделенная кнопка выхода.', `<div class="panel dark-card">${identity('', 'round')}${exit('full-button')}</div>`],
    ['bold', 'Розовый модуль', 'Насыщенный фирменный цвет и чёткое разделение. Выход занимает отдельную правую колонку.', `<div class="pink-module">${identity('', 'round')}${exitIcon()}</div>`],
    ['bold', 'Лимонная заметка', 'Тёплая жёлтая карточка с контрастной рамкой и нижней строкой выхода.', `<div class="note-card"><span class="micro-label">ЛИЧНЫЙ КАБИНЕТ</span>${identity()}${exit('underlined')}</div>`],
    ['bold', 'Техническая бирка', 'Моноширинные подписи, прямые углы и пунктир. Структура напоминает небольшую системную панель.', `<div class="technical"><div class="tech-label">ACCOUNT <span>↗</span></div>${identity('','square')}<div class="dash"></div>${exit('spread', 'ВЫЙТИ')}</div>`],
    ['bold', 'Билет', 'Разделение перфорацией и небольшие выемки по краям. Профиль сверху, выход — отрывной нижней строкой.', `<div class="ticket">${identity('', 'round')}<div class="perforation"></div>${exit('spread')}</div>`],
    ['bold', 'Стекло и свет', 'Полупрозрачный слой поверх розового и лилового свечения. Выход находится на отдельной стеклянной плашке.', `<div class="glass-orbit"><div class="glass-card">${identity('', 'round')}${exit('glass-exit')}</div></div>`],
  ].map(([group, title, description, html], i) => ({ id: i + 1, group, title, description, html }));
  let favorites = new Set();
  try { const saved = JSON.parse(localStorage.getItem('tmc-profile-designs-favorites') || '[]'); if (Array.isArray(saved)) favorites = new Set(saved.filter(n => Number.isInteger(n) && n >= 1 && n <= 30)); } catch {}
  let filter = 'all', shortlist = false, current = 1, returnFocus = null;
  const $ = id => document.getElementById(id);
  const number = n => String(n).padStart(2, '0');
  const scene = d => `<div class="profile-design d${number(d.id)}">${d.html}</div>`;
  $('gallery').innerHTML = designs.map(d => `<article class="design-card" data-id="${d.id}" data-group="${d.group}"><div class="card-heading"><span class="card-number">${number(d.id)}</span><span class="card-category">${groups[d.group]}</span><button class="favorite icon-button" data-favorite="${d.id}" aria-label="Вариант ${number(d.id)}: в избранное" aria-pressed="false">${icon('heart')}</button></div><div class="stage">${scene(d)}</div><div class="card-bottom"><div><h2>${d.title}</h2><p>${d.description.split('.')[0]}.</p></div><button class="open-preview icon-button" data-preview="${d.id}" aria-label="Увеличить вариант ${number(d.id)}: ${d.title}">${icon('expand')}</button></div></article>`).join('');
  $('pagination').innerHTML = designs.map(d => `<button data-preview="${d.id}" aria-label="Вариант ${number(d.id)}">${number(d.id)}</button>`).join('');

  let toastTimer;
  function toast(text) { const el = $($('preview').open ? 'preview-toast' : 'toast'); document.querySelectorAll('.toast').forEach(t => t.classList.remove('visible')); el.textContent = text; el.classList.add('visible'); clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('visible'), 2300); }
  function update() {
    let count = 0;
    document.querySelectorAll('.design-card').forEach(card => { const id = Number(card.dataset.id); card.hidden = !(filter === 'all' || card.dataset.group === filter) || (shortlist && !favorites.has(id)); if (!card.hidden) count++; card.classList.toggle('saved', favorites.has(id)); });
    document.querySelectorAll('[data-favorite]').forEach(b => { const saved = favorites.has(Number(b.dataset.favorite)); b.setAttribute('aria-pressed', String(saved)); b.setAttribute('aria-label', `Вариант ${number(b.dataset.favorite)}: ${saved ? 'убрать из избранного' : 'в избранное'}`); });
    $('favorite-count').textContent = favorites.size;
    $('visible-count').textContent = `${count} ${count === 1 ? 'вариант' : count > 1 && count < 5 ? 'варианта' : 'вариантов'}`;
    $('empty').hidden = count > 0;
    $('shortlist').setAttribute('aria-pressed', String(shortlist));
    document.querySelectorAll('.filter').forEach(b => { const active = b.dataset.filter === filter; b.classList.toggle('active', active); b.setAttribute('aria-pressed', String(active)); });
    const saved = favorites.has(current);
    $('preview-favorite').setAttribute('aria-pressed', String(saved));
    $('preview-favorite').querySelector('span').textContent = saved ? 'В избранном' : 'В избранное';
    document.querySelectorAll('#pagination button').forEach(b => { const active = Number(b.dataset.preview) === current; b.classList.toggle('active', active); b.classList.toggle('saved', favorites.has(Number(b.dataset.preview))); if (active) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current'); });
  }
  function toggleFavorite(id) { if (favorites.has(id)) favorites.delete(id); else favorites.add(id); try { localStorage.setItem('tmc-profile-designs-favorites', JSON.stringify([...favorites])); } catch {} update(); }
  function openPreview(id) {
    const d = designs[id - 1]; if (!d) return;
    current = id;
    $('preview-title').textContent = `${number(id)}. ${d.title}`;
    $('preview-group').textContent = groups[d.group];
    $('preview-description').textContent = d.description;
    $('dialog-counter').textContent = `${number(id)} / 30`;
    $('context-design').innerHTML = scene(d);
    if (!$('preview').open) { returnFocus = document.activeElement; $('preview').showModal(); document.body.classList.add('preview-open'); }
    const url = new URL(location.href); url.hash = `design-${number(id)}`; history.replaceState(null, '', url);
    update();
  }
  function closePreview() { $('preview').close(); }
  function move(delta) { openPreview((current - 1 + delta + 30) % 30 + 1); }
  document.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.favorite) toggleFavorite(Number(b.dataset.favorite));
    if (b.dataset.preview) openPreview(Number(b.dataset.preview));
    if (b.dataset.filter) { filter = b.dataset.filter; update(); }
    if (b.dataset.demo === 'exit') toast('Предпросмотр кнопки «Выйти»');
    if (b.dataset.demo === 'profile') toast('Предпросмотр перехода в профиль');
  });
  $('shortlist').onclick = () => { shortlist = !shortlist; update(); };
  $('preview-favorite').onclick = () => toggleFavorite(current);
  $('close-preview').onclick = closePreview;
  $('close-icon').onclick = closePreview;
  $('prev').onclick = () => move(-1);
  $('next').onclick = () => move(1);
  $('preview').addEventListener('close', () => { document.body.classList.remove('preview-open'); history.replaceState(null, '', location.pathname + location.search); returnFocus?.focus(); });
  document.addEventListener('keydown', e => { if (!$('preview').open) return; if (e.key === 'ArrowRight') { e.preventDefault(); move(1); } if (e.key === 'ArrowLeft') { e.preventDefault(); move(-1); } });
  update();
  const initial = location.hash.match(/^#design-(\d{1,2})$/); if (initial) openPreview(Number(initial[1]));
})();
