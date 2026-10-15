/* Thirty independent layout studies for the Organizations page. No API calls. */
(() => {
  'use strict';
  const groups = {
    minimal: 'Минимализм', cards: 'Карточки', editorial: 'Типографика',
    product: 'Рабочие панели', graphic: 'Графика', dark: 'Тёмные темы',
  };
  const definitions = [
    ['minimal', 'Тихий интерфейс', 'Компактная форма в одну строку, свободное пространство и спокойное пустое состояние.'],
    ['minimal', 'В центре внимания', 'Узкая центральная колонка. Создание команды становится единственным заметным действием.'],
    ['minimal', 'Ритм секций', 'Нумерация слева и открытые секции справа. Иерархия строится отступами и тонкими линиями.'],
    ['minimal', 'Открытый холст', 'Большое пространство для организаций слева и постоянная форма в правой колонке.'],
    ['minimal', 'Одна линия', 'Крупный заголовок, линейная форма и списки без карточек. Минимум визуального шума.'],
    ['cards', 'Мягкое бенто', 'Асимметричная сетка: большое пространство команды, отдельная форма и нижняя полоса списков.'],
    ['cards', 'Две двери', 'Две равноправные карточки: создать свою организацию или увидеть команды, в которые вас пригласили.'],
    ['cards', 'Парящий лист', 'Белая рабочая поверхность на розовом фоне. Форма и список собраны в один цельный объект.'],
    ['cards', 'Стопка заметок', 'Смещённые цветные слои и небольшая карточка формы рядом со списком организаций.'],
    ['cards', 'Сетка пространств', 'Модульная раскладка с отдельной плиткой создания и двумя карточками списков.'],
    ['editorial', 'Швейцарская сетка', 'Большой гротеск, чёрные линейки, строгая сетка. Фирменный розовый работает точечно.'],
    ['editorial', 'Указатель 04', 'Крупный номер раздела в левой колонке и компактные рабочие блоки справа.'],
    ['editorial', 'Редакционный', 'Контрастная антиква, тёплая бумага и композиция журнального разворота.'],
    ['editorial', 'Сначала команда', 'Типографический манифест рядом с формой. Списки отделены широкой горизонтальной линией.'],
    ['editorial', 'Реестр', 'Моноширинные подписи, табличные строки и структура аккуратного печатного реестра.'],
    ['product', 'Рабочая консоль', 'Невысокий заголовок, переключение списков и быстрая форма над рабочей областью.'],
    ['product', 'Список и детали', 'Левая панель отдана организациям, правая — созданию новой команды и её возможностям.'],
    ['product', 'Первый шаг', 'Три понятных этапа: создать организацию, пригласить участников, поделиться ресурсами.'],
    ['product', 'Центр ресурсов', 'Широкая рабочая колонка и узкая справочная панель. Компактный экран для повседневной работы.'],
    ['product', 'Две колонки', 'Собственные и присоединённые организации всегда видны рядом. Форма занимает верхнюю полосу.'],
    ['graphic', 'Орбиты', 'Круговая схема команды на лиловом фоне. Спокойная форма отделена от выразительного первого экрана.'],
    ['graphic', 'Розовый блок', 'Насыщенная фирменная плоскость с крупным сообщением слева и белая форма справа.'],
    ['graphic', 'Чертёж', 'Координатная сетка, схема соединений и технические подписи вокруг привычной формы.'],
    ['graphic', 'Бумажная композиция', 'Кремовый фон, геометрические фигуры и тёплая редакционная подача без лишних рамок.'],
    ['graphic', 'Световое поле', 'Мягкие цветовые пятна, полупрозрачные поверхности и форма, парящая над фоном.'],
    ['dark', 'Карбон', 'Графитовый интерфейс с тонкими границами. Розовый акцент выделяет основное действие.'],
    ['dark', 'Полночное бенто', 'Глубокий синий фон, лиловые акценты и крупные карточки с разной плотностью информации.'],
    ['dark', 'Терминал', 'Моноширинный интерфейс с командной строкой создания организации и зелёными служебными метками.'],
    ['dark', 'Чёрный / розовый', 'Плакатная типографика, прямые углы и насыщенная розовая полоса формы на чёрном фоне.'],
    ['dark', 'Вечерняя студия', 'Тёплый угольный фон, кремовая антиква и медные акценты в спокойной двухколоночной композиции.'],
  ];
  const designs = definitions.map(([group, name, description], i) => ({ id: i + 1, group, name, description, groupName: groups[group] }));
  const paths = {
    grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    cube: '<path d="m12 3 9 5-9 5-9-5 9-5ZM3 8v9l9 5 9-5V8M12 13v9M7.5 5.5l9 5"/>',
    cloud: '<path d="M6 18a4 4 0 1 1 1-8 6 6 0 0 1 11-1 4.5 4.5 0 0 1 0 9Z"/>',
    users: '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    exit: '<path d="M9 4H4v16h5m5-12 4 4-4 4M8 12h12"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/>',
  };
  const icon = name => `<svg class="oi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.users}</svg>`;
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function art(kind = 'orbit') {
    const dot = (x,y,r=13,hot=false) => `<circle cx="${x}" cy="${y}" r="${r}" fill="${hot?'var(--accent)':'var(--surface)'}" stroke="var(--line)"/><circle cx="${x}" cy="${y-3}" r="3" fill="${hot?'var(--accent-ink)':'var(--muted)'}"/><path d="M${x-5} ${y+6}q0-7 5-7t5 7" fill="none" stroke="${hot?'var(--accent-ink)':'var(--muted)'}"/>`;
    let content = '';
    if (kind === 'orbit') {
      content = '<circle cx="180" cy="145" r="108" class="art-line"/><circle cx="180" cy="145" r="70" class="art-line" stroke-dasharray="3 6"/><path d="m92 82 88 63 98-12M180 145l-39 101M180 145l65-89" class="art-line"/>' + dot(180,145,31,true)+dot(92,82,22)+dot(278,133,22)+dot(141,246,22)+dot(245,56,18);
    } else if (kind === 'tiles') {
      content = '<path d="M80 100h200M130 70v170m100-170v170" class="art-line"/>';
      [[88,85],[171,85],[254,85],[88,168],[171,168],[254,168]].forEach(([x,y],i)=>{content+=`<rect x="${x-30}" y="${y-15}" width="66" height="66" rx="16" fill="${i===1?'var(--accent)':'var(--surface)'}" stroke="var(--line)"/>`;content+= i===1?`<path d="M${x-8} ${y+18}h22m-11-11v22" stroke="var(--accent-ink)" stroke-width="2"/>`:dot(x+3,y+18,15);});
    } else if (kind === 'stack') {
      content='<rect x="91" y="58" width="170" height="178" rx="18" fill="var(--soft)" stroke="var(--line)" transform="rotate(-13 176 147)"/><rect x="91" y="58" width="170" height="178" rx="18" fill="var(--surface)" stroke="var(--line)" transform="rotate(8 176 147)"/><rect x="91" y="58" width="170" height="178" rx="18" fill="var(--surface)" stroke="var(--line)"/>' +dot(176,128,29,true)+'<path d="M128 187h96m-79 15h62" class="art-line" stroke-width="5" stroke-linecap="round"/>';
    } else if (kind === 'network') {
      content='<path d="m50 144 84-86 95 25 81 91-101 73-159-103Zm84-86 75 189 20-164L50 144l260 30-176-116" class="art-line"/>' + [[50,144],[134,58],[229,83],[310,174],[209,247]].map(([x,y],i)=>dot(x,y,i===2?29:18,i===2)).join('');
    } else if (kind === 'paper') {
      content='<circle cx="114" cy="121" r="70" fill="var(--soft)"/><path d="M162 55h123v176H162z" fill="var(--accent)" transform="rotate(13 224 143)"/><path d="m65 238 77-130 78 130Z" fill="var(--ink)"/>'+dot(223,133,30,true)+'<circle cx="275" cy="238" r="22" fill="var(--surface)" stroke="var(--ink)"/>';
    } else {
      content = '<circle cx="180" cy="145" r="90" fill="var(--soft)"/>'+dot(146,145,35)+dot(213,145,35,true)+'<path d="M105 224q76 20 150-2" class="art-line"/>';
    }
    return `<div class="team-art"><svg viewBox="0 0 360 300" fill="none" aria-hidden="true">${content}</svg></div>`;
  }
  function heading(extra = '', title = 'Organizations') { return `<header class="page-heading"><p class="overline">04 / YOUR WORKSPACE</p><div class="heading-line"><h1>${title}<em>.</em></h1>${extra}</div><p class="page-lead">Shared resources start with a team.</p></header>`; }
  function form() {
    return `<section class="create-block"><p class="overline form-kicker">01 / A NEW SPACE</p><h2>Create organization</h2><p class="form-intro">A shared home for your people and resources.</p><form class="create-form"><label>Organization name<input name="organization" placeholder="e.g. Acme Studio" required maxlength="200" autocomplete="off"></label><button class="primary" type="submit" disabled>${icon('plus')}<span>Create organization</span>${icon('arrow')}</button></form><p class="form-note">You’ll be the owner. Invite your team next.</p></section>`;
  }
  function organizationRows(rows) {
    return `<div class="org-rows">${rows.map((row,i) => `<article class="org-row"><span class="org-monogram">${escape(row.name.slice(0,1).toUpperCase())}</span><div><h3>${escape(row.name)}</h3><span>${row.joined?'Member':'Owner'} · ${row.members || 1} ${row.members===1||!row.members?'member':'members'}</span></div><button class="row-open" type="button" data-demo="Organization details" aria-label="Open ${escape(row.name)}">${icon('arrow')}</button></article>`).join('')}</div>`;
  }
  function list(which, rows, options = {}) {
    const joined = which === 'joined'; const found = rows.filter(r=>!!r.joined===joined);
    return `<section class="list-block ${joined?'joined-list':'owned-list'}"><div class="list-heading"><h2>${joined?'Joined organizations':'Created by you'}</h2><span class="count">${found.length.toString().padStart(2,'0')}</span></div>${found.length?organizationRows(found):`<div class="list-empty">${icon(joined?'mail':'users')}<div><h3>${joined?'No invitations yet':'Your team belongs here'}</h3><p>${joined?'Organizations you join will appear here.':'You haven’t created any organizations yet.'}</p></div>${options.action&&!joined?'<button class="text-action" type="button" data-action="create">Create your first organization ↗</button>':''}</div>`}</section>`;
  }
  function hero(rows, kind = 'orbit', copy = 'A place for your people.') {
    if (rows.some(r=>!r.joined)) return `<section class="hero-block populated-hero"><div class="hero-caption"><span class="overline">YOUR SHARED SPACES</span><h2>Your people.<br>Your possibilities.</h2><p>Build something greater, together.</p></div>${art(kind)}</section>`;
    return `<section class="hero-block">${art(kind)}<div class="hero-caption"><span class="overline">BETTER, TOGETHER</span><h2>${copy}</h2><p>Create an organization to bring your team<br class="desktop-br"> and shared resources together.</p></div></section>`;
  }
  const cta = () => `<button class="primary header-cta" type="button" data-action="create">${icon('plus')}Create organization</button>`;
  const help = () => `<aside class="help-block"><p class="overline">A SHARED WORKSPACE</p><h2>One team.<br>More possibilities.</h2><ul><li>${icon('users')}Bring people together</li><li>${icon('cube')}Share computing resources</li><li>${icon('check')}Manage access in one place</li></ul><p>Start with a name.<br>The rest grows with your team.</p></aside>`;
  function layout(id, rows) {
    const H=()=>heading(), F=form, O=()=>list('owned',rows), J=()=>list('joined',rows), B=(kind,copy)=>hero(rows,kind,copy);
    switch (id) {
      case 1: return H()+F()+O()+J();
      case 2: return H()+B('people','Make room for your team.')+F()+`<div class="lists-pair">${O()}${J()}</div>`;
      case 3: return H()+`<div class="numbered-section"><span class="section-number">01<br><small>CREATE</small></span>${F()}</div><div class="numbered-section"><span class="section-number">02<br><small>ORGANIZATIONS</small></span><div>${O()}${J()}</div></div>`;
      case 4: return H()+`<div class="split-layout"><div>${B('orbit')}${O()}${J()}</div>${F()}</div>`;
      case 5: return H()+`<div class="inline-intro"><span>Start something<br>together.</span>${F()}</div>`+O()+J();
      case 6: return H()+`<div class="bento-layout">${B('tiles','Small team.<br>Shared ambition.')}${F()}<div class="lists-pair">${O()}${J()}</div></div>`;
      case 7: return H()+`<div class="doors-layout">${F()}<div class="invitation-door">${art('people')}${J()}</div></div>`+O();
      case 8: return H()+`<div class="floating-sheet"><div class="sheet-top">${F()}${art('stack')}</div><div class="lists-pair">${O()}${J()}</div></div>`;
      case 9: return H()+`<div class="notes-layout"><div class="note-stack">${F()}</div><div class="notes-lists">${O()}${J()}<p class="small-caption">A little structure for a lot of possibility.</p></div></div>`;
      case 10: return H()+`<div class="tiles-layout">${F()}${O()}${J()}</div><div class="quiet-banner">${icon('users')}<p>Good things happen when people work together.</p><span>YOUR NEXT CHAPTER ↗</span></div>`;
      case 11: return H()+`<div class="swiss-row"><span class="section-number">01</span>${F()}</div><div class="swiss-row"><span class="section-number">02</span><div class="lists-pair">${O()}${J()}</div></div><p class="swiss-footer">INDIVIDUAL RESOURCES. COLLECTIVE POTENTIAL. <span>THE MUTATING COMPANY ↗</span></p>`;
      case 12: return `<div class="index-layout"><div class="index-title"><span class="big-index">04<em>↗</em></span><h1>Organizations<span>.</span></h1><p>Shared resources<br>start with a team.</p></div><div class="index-work">${F()}${O()}${J()}</div></div>`;
      case 13: return H()+`<div class="magazine-layout"><div class="magazine-story">${B('paper','The art of<br>working together.')}<p class="overline">PEOPLE / RESOURCES / POSSIBILITIES</p></div><div>${F()}${O()}${J()}</div></div>`;
      case 14: return `<p class="overline">04 / ORGANIZATIONS</p><div class="manifesto-layout"><div class="manifesto"><h1>Great work.<br>Starts with<br><em>a team.</em></h1><p>Shared people. Shared resources.<br>Something greater, together.</p></div>${F()}</div><div class="lists-pair">${O()}${J()}</div>`;
      case 15: return `<div class="ledger-heading"><p class="overline">TMC / ACCOUNT / 004</p><span>ORGANIZATION REGISTER</span></div>${H()}<div class="register-section"><div class="register-label">[01] NEW ENTRY</div>${F()}</div><div class="register-section"><div class="register-label">[02] OWNED</div>${O()}</div><div class="register-section"><div class="register-label">[03] MEMBERSHIPS</div>${J()}</div><p class="register-end">— END OF REGISTER —</p>`;
      case 16: return heading(cta())+`<div class="console-layout">${F()}<div class="tabs" role="tablist" aria-label="Organization lists"><button type="button" role="tab" aria-selected="true" data-tab="owned">Created by you <span>${rows.filter(r=>!r.joined).length}</span></button><button type="button" role="tab" aria-selected="false" data-tab="joined">Joined organizations <span>${rows.filter(r=>r.joined).length}</span></button></div><div class="tab-panel" role="tabpanel">${O()}${J()}</div></div>`;
      case 17: return H()+`<div class="master-detail"><div class="master-list">${O()}${J()}</div><div class="detail-panel">${F()}${help()}</div></div>`;
      case 18: return H()+`<div class="setup-steps"><span class="current"><b>01</b>Create a space</span><span><b>02</b>Invite your team</span><span><b>03</b>Share resources</span></div><div class="setup-content">${F()}${B('network','Build your team’s next chapter.')}</div><div class="lists-pair">${O()}${J()}</div>`;
      case 19: return heading(cta())+`<div class="resource-layout"><div>${F()}${O()}${J()}</div>${help()}</div>`;
      case 20: return H()+F()+`<div class="board-layout">${O()}${J()}</div>`;
      case 21: return H()+`<div class="orbit-layout">${B('orbit','Your people.<br>In one orbit.')}${F()}</div><div class="lists-pair">${O()}${J()}</div>`;
      case 22: return `<div class="color-split"><div class="pink-poster"><p class="overline">04 / ORGANIZATIONS</p><h1>Better.<br>Together<span>↗</span></h1><p>Shared resources start with a team.</p>${art('network')}</div>${F()}</div><div class="lists-pair">${O()}${J()}</div>`;
      case 23: return H()+`<div class="blueprint-layout"><div class="blueprint-art"><span class="overline">FIG. 01 / COLLECTIVE CAPACITY</span>${art('network')}<span class="coordinate">X: PEOPLE · Y: POSSIBILITIES</span></div>${F()}</div><div class="lists-pair">${O()}${J()}</div>`;
      case 24: return H()+`<div class="paper-layout"><div class="paper-message"><h2>Different people.<br>One shared space.</h2><p>Give your team room to grow.</p></div>${art('paper')}</div>`+F()+`<div class="lists-pair">${O()}${J()}</div>`;
      case 25: return H()+`<div class="light-field"><div class="field-caption"><span class="overline">A LITTLE MORE CONNECTED</span><h2>Space for<br><em>what’s next.</em></h2><p>Bring your people and resources together.</p>${art('orbit')}</div>${F()}</div><div class="lists-pair">${O()}${J()}</div>`;
      case 26: return heading(cta())+`<div class="carbon-layout">${F()}${B('people','A shared space.<br>A stronger team.')}</div><div class="lists-pair">${O()}${J()}</div>`;
      case 27: return H()+`<div class="midnight-grid">${B('orbit','Everything starts<br>with your people.')}${F()}${O()}${J()}</div>`;
      case 28: return `<div class="terminal-top"><span><i></i> TMC / WORKSPACE</span><span>SESSION: EVGENIY</span></div>${heading()}<div class="terminal-command"><span class="command-prompt">$ organization.create</span>${F()}</div><div class="terminal-output"><p class="overline">OUTPUT / ORGANIZATION DIRECTORY</p>${O()}${J()}<span class="terminal-cursor">▍</span></div>`;
      case 29: return `<p class="overline">04 / THE MUTATING COMPANY</p>${heading()}<div class="black-pink-form">${F()}</div><div class="lists-pair">${O()}${J()}</div><div class="poster-footer">ONE TEAM <span>∞ POSSIBILITIES</span></div>`;
      case 30: return H()+`<div class="studio-layout"><div class="studio-story"><p class="overline">A PLACE TO COME TOGETHER</p><h2>Good company.<br>Great possibilities.</h2>${art('stack')}<p>A shared home for people<br>who build things together.</p></div><div>${F()}${O()}${J()}</div></div>`;
      default: return H()+F()+O()+J();
    }
  }
  function scene(design, state = 'empty', customRows = null) {
    const rows = customRows || (state === 'filled' ? [{name:'Acme Studio',members:4},{name:'Research Lab',members:2},{name:'Open Compute',members:8,joined:true}] : []);
    return `<div class="org-scene d${design.id} group-${design.group}" data-design="${design.id}"><aside class="org-sidebar"><a class="sidebar-wordmark" href="index.html" aria-label="Design gallery">m<span>.</span></a><p class="sidebar-label">ACCOUNT</p><nav aria-label="Account navigation">${[['grid','Overview'],['cube','Power Cells'],['cloud','Cloud'],['users','Organizations']].map(([i,label])=>`<button type="button" class="sidebar-nav ${label==='Organizations'?'selected':''}" ${label==='Organizations'?'aria-current="page"':''} data-demo="${label}">${icon(i)}<span>${label}</span>${label==='Cloud'?'<i></i>':''}</button>`).join('')}</nav><div class="sidebar-identity"><span class="account-avatar">E</span><div><strong>Evgeniy</strong><small>zhenl-b@yandex.ru</small></div><button type="button" aria-label="Sign out" data-demo="Sign out">${icon('exit')}</button></div></aside><main class="org-main">${layout(design.id,rows)}</main><div class="scene-toast" role="status" aria-live="polite"></div></div>`;
  }
  window.OrganizationStudies = { designs, groups, scene, icon };
})();
