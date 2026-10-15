(() => {
  'use strict';
  const params = new URLSearchParams(location.search);
  const id = Number(params.get('design')) || 1;
  const state = params.get('state') === 'filled' ? 'filled' : 'empty';
  const { designs, scene } = window.OrganizationStudies;
  const design = designs.find(d=>d.id===id) || designs[0];
  let rows = state==='filled' ? [{name:'Acme Studio',members:4},{name:'Research Lab',members:2},{name:'Open Compute',members:8,joined:true}] : [];
  let timer;
  function render() { document.getElementById('design-root').innerHTML=scene(design,state,rows); }
  function toast(text) { const el=document.querySelector('.scene-toast');el.textContent=text;el.classList.add('visible');clearTimeout(timer);timer=setTimeout(()=>el.classList.remove('visible'),2400); }
  document.title=`${String(design.id).padStart(2,'0')}. ${design.name} — Organizations`;
  render();
  document.addEventListener('input',event=>{if(event.target.name==='organization')event.target.form.querySelector('button[type="submit"]').disabled=!event.target.value.trim();});
  document.addEventListener('submit',event=>{
    if(!event.target.matches('.create-form'))return;
    event.preventDefault();const input=event.target.elements.organization;const name=input.value.trim();if(!name)return;
    rows.push({name,members:1});render();toast('Organization created in this preview.');
    document.querySelector('.owned-list')?.scrollIntoView({behavior:'smooth',block:'nearest'});
  });
  document.addEventListener('click',event=>{
    const button=event.target.closest('button');if(!button)return;
    if(button.dataset.action==='create') { const input=document.querySelector('input[name="organization"]');input?.focus(); }
    if(button.dataset.demo)toast(`${button.dataset.demo} · design preview`);
    if(button.dataset.tab) {
      const joined=button.dataset.tab==='joined';
      document.querySelector('.tab-panel').classList.toggle('show-joined',joined);
      document.querySelectorAll('[data-tab]').forEach(b=>b.setAttribute('aria-selected',String(b===button)));
    }
  });
})();
