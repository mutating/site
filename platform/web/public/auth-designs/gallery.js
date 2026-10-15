(() => {
  const designs=window.AuthDesigns;
  const $=id=>document.getElementById(id);
  const num=id=>String(id).padStart(2,'0');
  let favorites=new Set();
  try{const saved=JSON.parse(localStorage.getItem('tmc-auth-design-favorites')||'[]');if(Array.isArray(saved))favorites=new Set(saved.filter(id=>Number.isInteger(id)&&id>=1&&id<=30));}catch{}
  let activeFilter='all',shortlistOnly=false,current=1,toastTimer,lastFocused=null;
  const cards=new Map();
  const scales=new ResizeObserver(entries=>{for(const {target,contentRect} of entries){const child=target.querySelector('.canvas-scale');if(child)child.style.transform=`scale(${contentRect.width/600})`;}});
  const sceneCache=new Map();
  function announce(message){$('toast').textContent=message;$('toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('visible'),1800);}
  function syncFavoriteButtons(){
    $('favorite-count').textContent=String(favorites.size);
    document.querySelectorAll('[data-favorite]').forEach(b=>{
      const id=Number(b.dataset.favorite),saved=favorites.has(id);
      b.textContent=saved?'♥':'♡';b.setAttribute('aria-pressed',String(saved));b.setAttribute('aria-label',`${saved?'Убрать из избранного':'В избранное'}: ${num(id)}. ${designs[id-1].name}`);
    });
    const saved=favorites.has(current);
    $('preview-favorite').innerHTML=`${saved?'♥':'♡'} <span>${saved?'В избранном':'В избранное'}</span>`;
    $('preview-favorite').setAttribute('aria-pressed',String(saved));
    document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('saved',favorites.has(Number(b.dataset.page))));
  }
  function toggleFavorite(id){
    const added=!favorites.has(id);
    if(added)favorites.add(id);else favorites.delete(id);
    try{localStorage.setItem('tmc-auth-design-favorites',JSON.stringify([...favorites]));}catch{}
    syncFavoriteButtons();filterCards();announce(`${num(id)} ${added?'добавлен в избранное':'убран из избранного'}`);
  }
  function filterCards(){
    let visible=0;
    for(const d of designs){const show=(activeFilter==='all'||d.group===activeFilter)&&(!shortlistOnly||favorites.has(d.id));cards.get(d.id).hidden=!show;if(show)visible++;}
    $('visible-count').textContent=`${visible} ${visible===1?'вариант':visible>=2&&visible<=4?'варианта':'вариантов'}`;
    $('empty-state').hidden=visible!==0;
    $('empty-state').textContent=favorites.size&&shortlistOnly?'В этом направлении пока нет избранных вариантов. Выберите «Все».':'Сохраните понравившиеся варианты сердечком — здесь будет ваша подборка.';
  }
  for(const d of designs){
    const article=document.createElement('article');article.className='design-card';article.id=`design-${num(d.id)}`;article.dataset.design=d.id;
    const canvas=document.createElement('div');canvas.className='card-canvas';canvas.tabIndex=0;canvas.setAttribute('role','button');canvas.setAttribute('aria-label',`Открыть вариант ${num(d.id)}: ${d.name}`);
    const scale=document.createElement('div');scale.className='canvas-scale';scale.setAttribute('aria-hidden','true');scale.append(window.createAuthScene(d));canvas.append(scale);
    const open=document.createElement('span');open.className='card-open';open.textContent='Смотреть на странице ↗';canvas.append(open);
    canvas.addEventListener('click',()=>openPreview(d.id));canvas.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openPreview(d.id);}});
    const info=document.createElement('div');info.className='card-info';
    info.innerHTML=`<span class="card-number">${num(d.id)}</span><div class="card-titles"><h2>${d.name}</h2><p>${d.groupName}</p></div>`;
    const favorite=document.createElement('button');favorite.className='favorite-button';favorite.dataset.favorite=d.id;favorite.addEventListener('click',()=>toggleFavorite(d.id));info.append(favorite);
    article.append(canvas,info);$('gallery').append(article);cards.set(d.id,article);scales.observe(canvas);
    const pagination=document.createElement('button');pagination.textContent=num(d.id);pagination.dataset.page=d.id;pagination.setAttribute('aria-label',`Вариант ${num(d.id)}: ${d.name}`);pagination.addEventListener('click',()=>openPreview(d.id));$('design-pagination').append(pagination);
  }
  function resizeContext(){
    const width=Math.min(1440,Math.max(280,innerWidth- (innerWidth<600?16:48)));
    $('context-viewport').style.width=`${width}px`;
    $('context-viewport').style.height=`${width*900/1440}px`;
    $('context-page').style.transform=`scale(${width/1440})`;
  }
  function openPreview(id,writeHash=true){
    const d=designs[id-1];if(!d)return;
    current=id;
    if(!$('preview-dialog').open){lastFocused=document.activeElement;$('preview-dialog').showModal();document.body.style.overflow='hidden';}
    $('preview-number').textContent=num(id)+' / 30';$('preview-title').textContent=d.name;$('preview-description').textContent=d.description;
    if(!sceneCache.has(id))sceneCache.set(id,window.createAuthScene(d));
    $('context-scene').replaceChildren(sceneCache.get(id));
    document.querySelectorAll('[data-page]').forEach(b=>{const selected=Number(b.dataset.page)===id;b.classList.toggle('active',selected);if(selected)b.setAttribute('aria-current','true');else b.removeAttribute('aria-current');});
    syncFavoriteButtons();resizeContext();
    if(writeHash)history.replaceState(null,'',`#${num(id)}`);
    document.title=`${num(id)}. ${d.name} — TMC / 30 композиций`;
  }
  function closePreview(){
    $('preview-dialog').close();document.body.style.overflow='';history.replaceState(null,'',location.pathname+location.search);document.title='30 композиций — The Mutating Company';
    if(lastFocused&&typeof lastFocused.focus==='function')lastFocused.focus({preventScroll:true});
  }
  $('close-preview').addEventListener('click',closePreview);
  $('preview-dialog').addEventListener('cancel',event=>{event.preventDefault();closePreview();});
  $('preview-favorite').addEventListener('click',()=>toggleFavorite(current));
  $('previous-design').addEventListener('click',()=>openPreview(current===1?30:current-1));
  $('next-design').addEventListener('click',()=>openPreview(current===30?1:current+1));
  $('filters').addEventListener('click',event=>{
    const button=event.target.closest('[data-filter]');if(!button)return;
    activeFilter=button.dataset.filter;
    document.querySelectorAll('[data-filter]').forEach(b=>{const active=b===button;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});filterCards();
  });
  $('shortlist-toggle').addEventListener('click',()=>{shortlistOnly=!shortlistOnly;$('shortlist-toggle').setAttribute('aria-pressed',String(shortlistOnly));filterCards();});
  document.addEventListener('keydown',e=>{
    if(!$('preview-dialog').open||e.altKey||e.ctrlKey||e.metaKey)return;
    if(e.key==='ArrowRight'){e.preventDefault();openPreview(current===30?1:current+1);}
    if(e.key==='ArrowLeft'){e.preventDefault();openPreview(current===1?30:current-1);}
    if(e.key.toLowerCase()==='f'){e.preventDefault();toggleFavorite(current);}
  });
  function restoreHash(){const id=Number(location.hash.slice(1));if(Number.isInteger(id)&&id>=1&&id<=30)openPreview(id,false);else if($('preview-dialog').open)closePreview();}
  window.addEventListener('resize',resizeContext);window.addEventListener('hashchange',restoreHash);
  syncFavoriteButtons();restoreHash();
})();
