/* Second series: every design has exactly one mixed, vertical organization list. */
(() => {
  'use strict';
  const groups={minimal:'Минимализм',cards:'Карточки',editorial:'Типографика',product:'Рабочие панели',graphic:'Графика',dark:'Тёмные темы'};
  const definitions=[
    ['minimal','Чистый список','Открытые строки на общем фоне. Роль и ресурсы читаются в одной горизонтали с названием.'],
    ['minimal','Воздух между строк','Широкие интервалы, небольшие аватары и тонкие разделители. Форма находится под общим списком.'],
    ['minimal','Компактный кабинет','Плотный список без второстепенных подписей и короткая форма создания в верхней строке.'],
    ['minimal','Узкий фокус','Одна центральная колонка с общим списком. Новая организация создаётся в конце ленты.'],
    ['minimal','Поля и заметки','Небольшой заголовок слева от единой рабочей колонки. Роль остаётся частью строки организации.'],
    ['cards','Одна поверхность','Форма, заголовок и общий список собраны внутри одной белой поверхности с мягкими углами.'],
    ['cards','Лента карточек','Каждая организация — широкая отдельная карточка. Все роли перемешаны в одной вертикальной ленте.'],
    ['cards','Цветная кромка','Общий контейнер списка с выразительными левыми метками. Форма выделена узкой розовой полосой.'],
    ['cards','Плавающая панель','Список на светлой панели поверх лилового фона. Создание занимает самостоятельную нижнюю строку.'],
    ['cards','Раскрывающаяся форма','Список занимает основное пространство. Форма раскрывается над ним по нажатию на строку создания.'],
    ['editorial','Большой указатель','Крупный заголовок и пронумерованные строки организаций. Чёткие линейки вместо множества карточек.'],
    ['editorial','Редакционная лента','Антиква, молочная бумага и общий список как каталог небольших команд. Форма завершает страницу.'],
    ['editorial','Индекс пространств','Большой номер раздела рядом с единым индексом организаций. Компактные роли встроены в строки.'],
    ['editorial','Типографический каталог','Крупные названия команд становятся главным графическим элементом. Данные выстроены справа.'],
    ['editorial','Печатный реестр','Моноширинная таблица с номером записи, ролью и ресурсами. Одна последовательность для всех организаций.'],
    ['product','Рабочая таблица','Понятная табличная шапка и компактные строки. Создание вынесено в боковую рабочую панель.'],
    ['product','Найти команду','Поиск над общим списком и хорошо заметные роли. Форма остаётся в нижней части экрана.'],
    ['product','Командная палитра','Тёмная поисковая строка над светлым списком. Никаких вкладок по принадлежности организаций.'],
    ['product','Список с мастером','Все организации слева, создание новой справа. Разделение относится к действиям, а список остаётся единым.'],
    ['product','Быстрое создание','Новая организация добавляется через компактную нижнюю панель. Основной экран занят единым списком.'],
    ['graphic','Розовая полоса','Узкая фирменная полоса объединяет заголовок и форму. Все организации идут одной спокойной лентой ниже.'],
    ['graphic','Схема связей','Небольшая схема команды в шапке и единый список с круглыми аватарами и мягкими плашками ролей.'],
    ['graphic','Чертёж списка','Координатная сетка, прямые углы и технические подписи. Все организации — в одном общем реестре.'],
    ['graphic','Тёплая бумага','Кремовый фон, терракотовые акценты и горизонтальные карточки, похожие на бумажные вкладки.'],
    ['graphic','Светлая перспектива','Мягкий цветовой переход за заголовком и полупрозрачная поверхность единого списка.'],
    ['dark','Графитовый каталог','Графит, тонкие разделители и компактные розовые метки ролей. Все организации в одной колонке.'],
    ['dark','Глубокий индиго','Синий фон и светлые лиловые акценты. Общий список расположен на цельной тёмной поверхности.'],
    ['dark','Консоль организаций','Моноширинные строки, зелёные служебные метки и команда создания внизу общего списка.'],
    ['dark','Розовый каталог','Крупная розовая типографика на чёрном. Названия команд и роли оформлены как плакатный перечень.'],
    ['dark','Тёплый вечер','Медные метки, кремовые названия и спокойная антиква. Форма и единый список образуют один разворот.'],
  ];
  const designs=definitions.map(([group,name,description],i)=>({id:i+1,group,name,description,groupName:groups[group]}));
  const paths={grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',cube:'<path d="m12 3 9 5-9 5-9-5 9-5ZM3 8v9l9 5 9-5V8M12 13v9M7.5 5.5l9 5"/>',cloud:'<path d="M6 18a4 4 0 1 1 1-8 6 6 0 0 1 11-1 4.5 4.5 0 0 1 0 9Z"/>',users:'<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5"/>',plus:'<path d="M12 5v14M5 12h14"/>',arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',exit:'<path d="M9 4H4v16h5m5-12 4 4-4 4M8 12h12"/>',search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',chevron:'<path d="m9 5 7 7-7 7"/>',check:'<path d="m5 12 4 4L19 6"/>'};
  const icon=name=>`<svg class="oi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]||paths.users}</svg>`;
  const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const demoRows=()=>[{name:'Acme Studio',members:4,computers:12,role:'Owner'},{name:'Open Compute',members:8,computers:24,role:'Member'},{name:'Research Lab',members:2,computers:6,role:'Owner'}];
  const cta=()=>`<button type="button" class="primary header-cta" data-action="create">${icon('plus')}<span>Create organization</span></button>`;
  function heading(action=false){return `<header class="page-heading"><p class="overline">04 / ORGANIZATIONS</p><div class="heading-line"><h1>Organizations<em>.</em></h1>${action?cta():''}</div><p class="page-lead">Your people and shared resources. All in one place.</p></header>`;}
  function form(){return `<section class="create-block"><p class="overline form-kicker">GIVE YOUR TEAM A HOME</p><h2>Create organization</h2><p class="form-intro">A new shared space starts with a name.</p><form class="create-form"><label>Organization name<input name="organization" placeholder="e.g. Acme Studio" autocomplete="off" required maxlength="200"></label><button type="submit" class="primary" disabled>${icon('plus')}<span>Create organization</span>${icon('arrow')}</button></form><p class="form-note">You’ll be the owner. Invite your team next.</p></section>`;}
  function search(){return `<label class="org-search">${icon('search')}<input type="search" name="search" aria-label="Find an organization" placeholder="Find an organization…" autocomplete="off"><span class="search-caption">ALL SPACES</span></label>`;}
  function list(rows,withSearch=false){return `<section class="list-block organization-list"><div class="list-heading"><div><p class="overline list-kicker">YOUR SHARED SPACES</p><h2>All organizations <span class="count">${String(rows.length).padStart(2,'0')}</span></h2></div><span class="list-explanation">One list. Every team.</span></div>${withSearch?search():''}${rows.length?`<div class="table-heading" aria-hidden="true"><span>ORGANIZATION</span><span>YOUR ROLE</span><span>MEMBERS</span><span>COMPUTERS</span><span></span></div><div class="org-rows" role="list">${rows.map((row,i)=>`<article class="org-row" role="listitem" data-search="${escape(row.name.toLowerCase())}"><span class="row-index">${String(i+1).padStart(2,'0')}</span><span class="org-monogram monogram-${i%3}">${escape(row.name.split(/\s+/).slice(0,2).map(s=>s[0]).join('').toUpperCase())}</span><div class="row-identity"><h3>${escape(row.name)}</h3><p>Shared workspace <span> / ${String(i+1).padStart(3,'0')}</span></p></div><span class="role-badge ${row.role==='Member'?'role-member':'role-owner'}">${escape(row.role||'Owner')}</span><span class="member-count">${icon('users')}<b>${row.members??1}</b><small>members</small></span><span class="resource-count">${icon('cube')}<b>${row.computers??0}</b><small>computers</small></span><button type="button" class="row-open" data-demo="${escape(row.name)}" aria-label="Open ${escape(row.name)}">${icon('arrow')}</button></article>`).join('')}</div><p class="search-empty" role="status" hidden>No organizations match your search.</p>`:`<div class="unified-empty">${icon('users')}<h3>Your team starts here.</h3><p>Create an organization or accept an invitation.<br>All your organizations will appear in this list.</p><button type="button" class="text-action" data-action="create">Create your first organization ↗</button></div>`}<div class="list-footer"><span>${rows.length} ${rows.length===1?'organization':'organizations'}</span><span>Shared resources start with a team.</span></div></section>`;}
  function network(){return `<svg class="network-art" viewBox="0 0 300 100" fill="none" aria-hidden="true"><path d="M21 55 85 22l61 48 65-52 66 39M21 55l125 15 131-13M85 22l126-4" stroke="currentColor" opacity=".25"/>${[[21,55],[85,22],[146,70],[211,18],[277,57]].map(([x,y],i)=>`<circle cx="${x}" cy="${y}" r="${i===2?18:10}" fill="${i===2?'var(--accent)':'var(--surface)'}" stroke="currentColor" stroke-opacity=".2"/>${i===2?`<path d="M${x-6} ${y}h12m-6-6v12" stroke="var(--accent-ink)"/>`:''}`).join('')}</svg>`;}
  function layout(id,rows){
    const H=heading,F=form,L=(s=false)=>list(rows,s);
    switch(id){
      case 1:return H()+F()+L();
      case 2:return H(true)+L()+F();
      case 3:return `<div class="compact-heading">${H()}${F()}</div>`+L();
      case 4:return `<div class="focused-column">${H()}${L()}${F()}</div>`;
      case 5:return `<div class="margin-layout"><div class="margin-caption"><p class="overline">04 / ACCOUNT</p><h1>Organizations<span>.</span></h1><p>Every team.<br>One place.</p></div><div>${F()}${L()}</div></div>`;
      case 6:return H()+`<div class="single-surface">${F()}${L()}</div>`;
      case 7:return H(true)+L()+F();
      case 8:return H()+`<div class="pink-ribbon">${F()}</div>`+L();
      case 9:return H()+`<div class="floating-list">${L()}${F()}</div>`;
      case 10:return H()+`<details class="create-disclosure"><summary>${icon('plus')}<span>Create a new organization</span><span class="disclosure-arrow">↗</span></summary>${F()}</details>`+L();
      case 11:return H()+`<div class="swiss-create"><span class="section-marker">+</span>${F()}</div>`+L();
      case 12:return H(true)+`<div class="editorial-intro"><p>A directory of the people<br>you build things with.</p><span class="overline">TMC / SHARED WORKSPACES</span></div>`+L()+F();
      case 13:return `<div class="index-layout"><div class="index-margin"><p class="overline">YOUR ACCOUNT</p><span class="index-number">04<em>↗</em></span><h1>Organizations<span>.</span></h1><p>Different teams.<br>Shared possibilities.</p></div><div>${F()}${L()}</div></div>`;
      case 14:return H()+L()+`<div class="catalogue-create"><p>Make space<br>for what’s next.</p>${F()}</div>`;
      case 15:return `<div class="registry-label"><span>TMC / WORKSPACE DIRECTORY</span><span>SECTION 004</span></div>`+H()+F()+L()+`<p class="registry-end">— END OF DIRECTORY —</p>`;
      case 16:return H()+`<div class="table-layout">${L()}<aside>${F()}<p class="aside-note">Create a space, invite your team, share your resources.</p></aside></div>`;
      case 17:return H(true)+L(true)+F();
      case 18:return H()+`<div class="palette-list">${L(true)}</div>`+F();
      case 19:return H()+`<div class="master-create">${L()}<aside>${F()}<div class="creation-tip">${icon('users')}<h3>Better, together.</h3><p>Bring people and computing resources into one shared space.</p></div></aside></div>`;
      case 20:return H()+L()+`<div class="quick-create">${F()}</div>`;
      case 21:return `<div class="color-heading">${H()}${F()}</div>`+L();
      case 22:return `<div class="connected-heading">${H()}${network()}</div>`+L()+F();
      case 23:return `<div class="blueprint-caption"><span>FIG. 04 / ORGANIZATION DIRECTORY</span><span>PEOPLE × RESOURCES</span></div>`+H()+F()+L();
      case 24:return H()+`<div class="paper-stamp"><span>GOOD COMPANY.</span><span>SHARED POSSIBILITIES.</span></div>`+L()+F();
      case 25:return `<div class="gradient-heading">${H(true)}<span class="gradient-orbit" aria-hidden="true"></span></div><div class="glass-list">${L()}${F()}</div>`;
      case 26:return H()+F()+L();
      case 27:return H(true)+`<div class="indigo-surface">${L()}${F()}</div>`;
      case 28:return `<div class="terminal-label"><span><i></i> WORKSPACE / CONNECTED</span><span>USER: EVGENIY</span></div>`+H()+L()+`<div class="terminal-create"><span class="command-label">$ organization.create</span>${F()}</div>`;
      case 29:return `<p class="overline">THE MUTATING COMPANY / 004</p>`+H()+L()+F();
      case 30:return H()+`<div class="evening-layout"><div>${L()}</div><aside><p class="evening-caption">Good company.<br>Great possibilities.</p>${F()}</aside></div>`;
      default:return H()+F()+L();
    }
  }
  function scene(design,state='filled',customRows=null){const rows=customRows??(state==='filled'?demoRows():[]);return `<div class="org-scene d${design.id} group-${design.group}" data-design="${design.id}"><aside class="org-sidebar"><a class="sidebar-wordmark" href="index.html" target="_top" aria-label="Design gallery">m<span>.</span></a><p class="sidebar-label">ACCOUNT</p><nav aria-label="Account navigation">${[['grid','Overview'],['cube','Power Cells'],['cloud','Cloud'],['users','Organizations']].map(([name,label])=>`<button type="button" class="sidebar-nav ${label==='Organizations'?'selected':''}" ${label==='Organizations'?'aria-current="page"':''} data-demo="${label}">${icon(name)}<span>${label}</span>${label==='Cloud'?'<i></i>':''}</button>`).join('')}</nav><div class="sidebar-identity"><span class="account-avatar">E</span><div><strong>Evgeniy</strong><small>zhenl-b@yandex.ru</small></div><button type="button" aria-label="Sign out" data-demo="Sign out">${icon('exit')}</button></div></aside><main class="org-main">${layout(design.id,rows)}</main><div class="scene-toast" role="status" aria-live="polite"></div></div>`;}
  window.OrganizationStudies={designs,groups,scene,icon,demoRows};
})();
