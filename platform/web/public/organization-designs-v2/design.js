(() => {
  'use strict';
  const params=new URLSearchParams(location.search);
  const {designs,scene,demoRows}=window.OrganizationStudies;
  const design=designs.find(d=>d.id===Number(params.get('design')))||designs[0];
  const state=params.get('state')==='empty'?'empty':'filled';
  let rows=state==='filled'?demoRows():[],timer;
  function render(){document.getElementById('design-root').innerHTML=scene(design,state,rows);}
  function toast(message){const el=document.querySelector('.scene-toast');el.textContent=message;el.classList.add('visible');clearTimeout(timer);timer=setTimeout(()=>el.classList.remove('visible'),2200);}
  function focusCreate(){const details=document.querySelector('.create-disclosure');if(details)details.open=true;const input=document.querySelector('input[name="organization"]');input?.focus();}
  document.title=`${String(design.id).padStart(2,'0')}. ${design.name} — Единый список`;
  render();
  document.addEventListener('input',event=>{
    if(event.target.name==='organization')event.target.form.querySelector('[type="submit"]').disabled=!event.target.value.trim();
    if(event.target.name==='search'){
      const query=event.target.value.trim().toLocaleLowerCase();let matches=0;
      document.querySelectorAll('.org-row').forEach(row=>{row.hidden=!row.dataset.search.includes(query);if(!row.hidden)matches++;});
      const empty=document.querySelector('.search-empty');if(empty)empty.hidden=matches>0;
    }
  });
  document.addEventListener('submit',event=>{
    if(!event.target.matches('.create-form'))return;event.preventDefault();
    const name=event.target.elements.organization.value.trim();if(!name)return;
    rows.push({name,members:1,computers:0,role:'Owner'});render();
    document.querySelector('.org-row:last-child')?.scrollIntoView({block:'nearest',behavior:'smooth'});
    toast('Organization added to the shared list in this preview.');
  });
  document.addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;if(button.dataset.action==='create')focusCreate();if(button.dataset.demo)toast(`${button.dataset.demo} · design preview`);});
})();
