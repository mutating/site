(() => {
  'use strict';
  const { designs, scene } = window.OrganizationStudies;
  const $ = id => document.getElementById(id);
  const number = id => String(id).padStart(2,'0');
  let favorites=new Set(), comparison=[], filter='all', onlyFavorites=false, state='filled', previewState='filled', comparisonState='filled', device='desktop', current=1, previousFocus=null, toastTimer;
  try {const saved=JSON.parse(localStorage.getItem('tmc-organization-designs-v2-favorites')||'[]');if(Array.isArray(saved))favorites=new Set(saved.filter(id=>Number.isInteger(id)&&id>=1&&id<=30));} catch {}
  const cards=new Map(), rendered=new Set();
  const scaling = new ResizeObserver(entries=>{for(const {target,contentRect} of entries){const content=target.querySelector('.card-canvas,iframe');if(content)content.style.transform=`scale(${contentRect.width/1280})`;}});
  const lazy = new IntersectionObserver(entries=>{for(const {target,isIntersecting} of entries){if(isIntersecting)renderCard(Number(target.dataset.design));}},{rootMargin:'650px'});
  function renderCard(id){const card=cards.get(id);if(!card||rendered.has(id))return;card.querySelector('.card-canvas').innerHTML=scene(designs[id-1],state);rendered.add(id);}
  function toast(message){$('gallery-toast').textContent=message;$('gallery-toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('gallery-toast').classList.remove('visible'),2300);}
  const compareIcon='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><rect x="3" y="4" width="7" height="16" rx="1"/><rect x="14" y="4" width="7" height="16" rx="1"/></svg>';
  for(const d of designs){
    const card=document.createElement('article');card.className='design-card';card.id=`design-${number(d.id)}`;card.dataset.design=d.id;
    card.innerHTML=`<div class="card-preview" role="button" tabindex="0" aria-label="Открыть вариант ${number(d.id)}: ${d.name}"><div class="card-canvas" inert aria-hidden="true"></div><span class="card-preview-label">Посмотреть вариант ↗</span></div><div class="card-meta"><span class="card-number">${number(d.id)}</span><div class="card-copy"><h2>${d.name}</h2><p>${d.description}</p><span class="card-group">${d.groupName.toUpperCase()}</span></div><div class="card-actions"><button class="card-icon-button" data-compare="${d.id}" aria-pressed="false" aria-label="Добавить вариант ${number(d.id)} в сравнение" title="Сравнить">${compareIcon}</button><button class="card-icon-button" data-favorite="${d.id}" aria-pressed="false" aria-label="В избранное: ${number(d.id)}" title="В избранное">♡</button></div></div>`;
    card.querySelector('.card-preview').addEventListener('click',()=>openPreview(d.id));
    card.querySelector('.card-preview').addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openPreview(d.id);}});
    $('gallery').append(card);cards.set(d.id,card);scaling.observe(card.querySelector('.card-preview'));lazy.observe(card);
    const page=document.createElement('button');page.textContent=number(d.id);page.dataset.page=d.id;page.setAttribute('aria-label',`${number(d.id)}. ${d.name}`);page.addEventListener('click',()=>openPreview(d.id));$('pagination').append(page);
  }
  function sync(){
    let visible=0;
    for(const d of designs){const card=cards.get(d.id);card.hidden=(filter!=='all'&&filter!==d.group)||(onlyFavorites&&!favorites.has(d.id));if(!card.hidden)visible++;}
    $('favorite-count').textContent=favorites.size;
    $('favorites-toggle').setAttribute('aria-pressed',String(onlyFavorites));
    $('visible-count').textContent=`${visible} ${visible%10===1&&visible!==11?'вариант':visible%10>=2&&visible%10<=4&&(visible<12||visible>14)?'варианта':'вариантов'}`;
    $('gallery-empty').hidden=visible>0;
    $('gallery-empty').textContent=favorites.size&&onlyFavorites?'В этом направлении нет избранных вариантов. Переключитесь на «Все».':'Отметьте понравившиеся варианты сердечком — здесь появится ваша подборка.';
    document.querySelectorAll('[data-favorite]').forEach(button=>{const id=Number(button.dataset.favorite),saved=favorites.has(id);button.textContent=saved?'♥':'♡';button.setAttribute('aria-pressed',String(saved));button.setAttribute('aria-label',`${saved?'Убрать из избранного':'В избранное'}: ${number(id)}. ${designs[id-1].name}`);});
    document.querySelectorAll('[data-compare]').forEach(button=>{const id=Number(button.dataset.compare),selected=comparison.includes(id);button.setAttribute('aria-pressed',String(selected));button.setAttribute('aria-label',`${selected?'Убрать из сравнения':'Сравнить'}: ${number(id)}. ${designs[id-1].name}`);});
    document.querySelectorAll('[data-filter]').forEach(button=>{const active=button.dataset.filter===filter;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
    $('preview-favorite').textContent=favorites.has(current)?'♥ В избранном':'♡ В избранное';$('preview-favorite').setAttribute('aria-pressed',String(favorites.has(current)));
    document.querySelectorAll('[data-page]').forEach(button=>{const id=Number(button.dataset.page);button.classList.toggle('active',id===current);button.classList.toggle('saved',favorites.has(id));if(id===current)button.setAttribute('aria-current','true');else button.removeAttribute('aria-current');});
    $('comparison-tray').hidden=comparison.length===0;
    $('comparison-selection').textContent=comparison.map(id=>`${number(id)} · ${designs[id-1].name}`).join(' / ');
    $('open-comparison').disabled=comparison.length!==2;$('open-comparison').textContent=comparison.length===2?'Сравнить два варианта ↗':'Выберите второй вариант';
  }
  function toggleFavorite(id){if(favorites.has(id))favorites.delete(id);else favorites.add(id);try{localStorage.setItem('tmc-organization-designs-v2-favorites',JSON.stringify([...favorites]));}catch{}sync();}
  function updateStateButtons(attribute,value){document.querySelectorAll(`[data-${attribute}]`).forEach(button=>button.setAttribute('aria-pressed',String(button.dataset[attribute.replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]===value)));}
  function changeState(next){state=next;rendered.clear();for(const [id,card] of cards){if(card.querySelector('.org-scene'))renderCard(id);}updateStateButtons('state',state);}
  function setFrame(){const url=`design.html?design=${number(current)}&state=${previewState}`;$('preview-frame').src=url;$('standalone-link').href=url;$('preview-frame').title=`Вариант ${number(current)}: ${designs[current-1].name}`;$('preview').querySelector('.preview-stage').classList.toggle('mobile',device==='mobile');updateStateButtons('preview-state',previewState);updateStateButtons('device',device);}
  function showDialog(dialog){previousFocus=document.activeElement;dialog.showModal();document.body.classList.add('dialog-open');}
  function openPreview(id,writeHash=true){const d=designs[id-1];if(!d)return;current=id;if(!$('preview').open){previewState=state;showDialog($('preview'));}$('preview-number').textContent=`${number(id)} / 30`;$('preview-title').textContent=d.name;$('preview-group').textContent=d.groupName;$('preview-description').textContent=d.description;setFrame();sync();if(writeHash)history.replaceState(null,'',`#${number(id)}`);document.title=`${number(id)}. ${d.name} — Organizations`;}
  function closePreview(){ $('preview').close();history.replaceState(null,'',location.pathname+location.search);document.title='Один список — 30 новых вариантов Organizations'; }
  function renderComparison(){scaling.disconnect();for(const card of cards.values())scaling.observe(card.querySelector('.card-preview'));$('comparison-panels').innerHTML=comparison.map(id=>`<section class="comparison-panel"><h3><span>${number(id)}</span>${designs[id-1].name}</h3><div class="comparison-viewport"><iframe src="design.html?design=${number(id)}&state=${comparisonState}" title="${number(id)}. ${designs[id-1].name}" tabindex="-1" aria-hidden="true"></iframe></div></section>`).join('');$('comparison-panels').querySelectorAll('.comparison-viewport').forEach(el=>scaling.observe(el));updateStateButtons('comparison-state',comparisonState);}
  document.addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;if(button.dataset.favorite)toggleFavorite(Number(button.dataset.favorite));if(button.dataset.compare){const id=Number(button.dataset.compare);if(comparison.includes(id))comparison=comparison.filter(n=>n!==id);else if(comparison.length<2)comparison.push(id);else toast('Уже выбраны два варианта. Уберите один, чтобы заменить его.');sync();}if(button.dataset.filter){filter=button.dataset.filter;sync();}if(button.dataset.state)changeState(button.dataset.state);if(button.dataset.previewState){previewState=button.dataset.previewState;setFrame();}if(button.dataset.device){device=button.dataset.device;$('preview').querySelector('.preview-stage').classList.toggle('mobile',device==='mobile');updateStateButtons('device',device);}if(button.dataset.comparisonState){comparisonState=button.dataset.comparisonState;renderComparison();}});
  $('favorites-toggle').addEventListener('click',()=>{onlyFavorites=!onlyFavorites;sync();});
  $('preview-favorite').addEventListener('click',()=>toggleFavorite(current));
  $('close-preview').addEventListener('click',closePreview);
  $('preview').addEventListener('cancel',event=>{event.preventDefault();closePreview();});
  $('previous-design').addEventListener('click',()=>openPreview((current+28)%30+1));
  $('next-design').addEventListener('click',()=>openPreview(current%30+1));
  $('clear-comparison').addEventListener('click',()=>{comparison=[];sync();});
  $('open-comparison').addEventListener('click',()=>{if(comparison.length!==2)return;comparisonState=state;showDialog($('comparison'));renderComparison();});
  $('close-comparison').addEventListener('click',()=>$('comparison').close());
  $('original-button').addEventListener('click',()=>showDialog($('original')));
  $('close-original').addEventListener('click',()=>$('original').close());
  for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('close',()=>{document.body.classList.remove('dialog-open');previousFocus?.focus({preventScroll:true});});
  document.addEventListener('keydown',event=>{if(!$('preview').open||event.altKey||event.metaKey||event.ctrlKey)return;if(event.key==='ArrowRight'){event.preventDefault();openPreview(current%30+1);}if(event.key==='ArrowLeft'){event.preventDefault();openPreview((current+28)%30+1);}});
  function restoreHash(){const match=location.hash.match(/^#(?:design-)?(\d{1,2})$/);if(match&&Number(match[1])>=1&&Number(match[1])<=30)openPreview(Number(match[1]),false);}
  window.addEventListener('hashchange',restoreHash);
  updateStateButtons('state',state);sync();restoreHash();
})();
