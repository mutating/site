/* Original vector studies; no remote assets, generated images, or API calls. */
(() => {
  const P = '#ff2c92', K = '#20121b', L = '#e5d4de', G = '#a78d9b', W = '#fffafb';
  const rect = (x,y,w,h,fill,rx=0,stroke='none',sw=1) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
  const circle = (x,y,r,fill,stroke='none',sw=1) => `<circle cx="${x}" cy="${y}" r="${r}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
  const line = (x1,y1,x2,y2,stroke=L,sw=1,extra='') => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke}" stroke-width="${sw}" ${extra}/>`;
  const path = (d,stroke,sw=1,fill='none',extra='') => `<path d="${d}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}" ${extra}/>`;
  const label = (x,y,s,fill=G,size=9,extra='') => `<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" ${extra}>${s}</text>`;
  const svg = (content,id) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 300" aria-hidden="true"><defs><linearGradient id="pink-${id}" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#ffb9da"/><stop offset=".5" stop-color="${P}"/><stop offset="1" stop-color="#b70a60"/></linearGradient><linearGradient id="silver-${id}" x1="0" y1="0" x2=".8" y2="1"><stop stop-color="#fff"/><stop offset=".5" stop-color="#f3e8ee"/><stop offset="1" stop-color="#bca7b3"/></linearGradient></defs>${content}</svg>`;
  const cube = (x,y,s,top='#fff7fb',left='#f0e3eb',right='#d9c2cf',stroke='#c6afbc') => {
    const a=s*.5,b=s*.29,h=s*.62;
    return `<g stroke="${stroke}" stroke-width="1.1" stroke-linejoin="round"><path d="M${x} ${y}l${a} ${-b} ${a} ${b} -${a} ${b}Z" fill="${top}"/><path d="M${x} ${y}l${a} ${b}v${h}l-${a} -${b}Z" fill="${left}"/><path d="M${x+a} ${y+b}l${a} -${b}v${h}l-${a} ${b}Z" fill="${right}"/></g>`;
  };
  const art = {
    1: () => line(45,145,550,145,'#c9b5c1') + circle(75,145,7,W,K,1.5) + circle(294,145,31,P) + circle(524,145,7,W,K,1.5) + label(45,205,'01 / ПОДКЛЮЧАЙТЕ',G,11)+label(242,205,'02 / ОБЪЕДИНЯЙТЕ',G,11)+label(442,205,'03 / СОЗДАВАЙТЕ',G,11),
    2: () => {
      let s=line(2,8,598,8,K)+line(2,286,598,286,K);
      for(let i=0;i<6;i++) {
        const x=2+i*101;
        s+=line(x,8,x,286,L)+label(x+8,30,`0${i+1}`,G,9);
        if(i===3) s+=rect(x+6,70,85,157,P)+path(`M${x+29} 148h39M${x+48.5} 128v39`,W,3);
        else for(let j=0;j<7;j++) s+=rect(x+17,78+j*21,12+(i*7+j*13)%56,9,j%3===0?K:'#ecdee5',1);
      }
      return s+line(598,8,598,286,L);
    },
    3: () => circle(300,143,89,P)+circle(300,143,126,'none',L,1)+circle(300,143,157,'none','#efe4ea',1)+circle(143,143,5,K)+circle(427,143,6,W,K,1)+circle(300,17,4,P)+label(300,282,'ОБЪЕДИНЯЕТ ВОЗМОЖНОСТИ',G,9,'text-anchor="middle"'),
    4: () => {
      let s=line(30,145,550,145,L);
      [63,200,337,474].forEach((x,i)=>{s+=rect(x-32,100,64,84,i===2?P:'none',9,i===2?P:L);s+=path(`M${x-10} 142h20M${x} 132v20`,i===2?W:G,1.5);s+=label(x,216,`NODE / 0${i+1}`,G,10,'text-anchor="middle"');});
      return s;
    },
    5: () => label(12,207,'1',K,145,'class="sans" font-weight="650"')+label(119,187,'+',G,74,'class="sans" font-weight="300"')+label(210,207,'1',K,145,'class="sans" font-weight="650"')+path('M318 141h64m-64 30h64',K,3)+label(416,207,'∞',P,154,'class="sans" font-weight="500"')+label(15,264,'БОЛЬШЕ, ЧЕМ СУММА ЧАСТЕЙ',G,11),
    6: () => {
      let s='';
      for(let i=0;i<7;i++) {
        const sy=25+i*39, ey=122+i*7;
        s+=path(`M8 ${sy}C155 ${sy} 139 ${ey} 335 ${ey}S466 ${ey} 552 ${ey}`,i===3?P:i%2?K:'#c6aebb',i===3?5:1.1);
        s+=circle(8,sy,3.2,i===3?P:K);
      }
      return s+rect(541,114,42,62,P,15)+path('M552 145h20m-7-7 7 7-7 7',W,1.6)+label(9,296,'МНОГО УСТРОЙСТВ',G,9)+label(451,252,'ОДНА СЕТЬ',P,9);
    },
    7: () => {
      const points=[[53,139],[102,67],[144,218],[220,125],[258,25],[307,244],[352,103],[425,37],[460,192],[552,123],[515,265],[38,260],[199,283]];
      const edges=[[0,1],[0,2],[0,3],[1,3],[1,4],[2,3],[2,5],[2,11],[3,4],[3,5],[3,6],[4,6],[4,7],[5,6],[5,8],[5,12],[6,7],[6,8],[6,9],[7,9],[8,9],[8,10],[9,10],[11,12],[2,12]];
      let s=edges.map(([a,b])=>line(...points[a],...points[b],(a===3||b===3)?'#ef9dc4':L,1)).join('');
      points.forEach(([x,y],i)=>{s+=circle(x,y,i===3?17:[6,8,1].includes(i)?8:3.5,i===3?P:W,i===3?P:K,1.2);if(i===3)s+=path(`M${x-6} ${y}h12m-6-6v12`,W,1.4);});
      return s+label(43,121,'YOU',P,8)+label(377,282,'DISTRIBUTED BY DESIGN',G,8);
    },
    8: () => {
      let s=path('M17 74H179q31 0 53 22l80 80q22 22 53 22h217',K,7,'none','stroke-linecap="round"')+path('M17 228h120q32 0 55-23l112-112q23-23 55-23h223',P,7,'none','stroke-linecap="round"')+path('M91 18v128q0 29 29 29h389q29 0 29 29v72','#d1b6c5',5,'none','stroke-linecap="round"');
      [[91,74],[91,175],[175,74],[244,153],[334,198],[404,70],[485,198],[538,70]].forEach(([x,y],i)=>s+=circle(x,y,8,W,i%2?P:K,2.5));
      return s+label(15,48,'ВАШИ УСТРОЙСТВА',G,9)+label(434,251,'ОБЩАЯ МОЩНОСТЬ',G,9);
    },
    9: () => {
      let s=''; const dots=[];
      for(let i=0;i<15;i++) {const a=i*Math.PI*2/15-.3;dots.push([300+Math.cos(a)*220,146+Math.sin(a)*118]);}
      dots.forEach(([x,y],i)=>{const [xx,yy]=dots[(i+5)%15];s+=line(x,y,xx,yy,i%3===0?'#f4a6ce':'#eadde5',.8);s+=line(x,y,300,146,i%3===0?'#f299c3':'#e9dbe3',.8);});
      dots.forEach(([x,y],i)=>s+=circle(x,y,i%3?3.4:6,i%3?K:P));
      return s+circle(300,146,30,P)+path('M289 146h22m-11-11v22',W,2)+label(300,297,'ВМЕСТЕ — БОЛЬШЕ',G,10,'text-anchor="middle"');
    },
    10: () => {
      let s='';const pts=[];
      for(let r=0;r<5;r++)for(let c=0;c<8;c++)pts.push([30+c*77+(r%2)*11,28+r*55]);
      pts.forEach(([x,y],i)=>{
        if(i%8<7)s+=line(x,y,...pts[i+1],L,.8);
        if(i<32)s+=line(x,y,...pts[i+8],L,.8);
        if(i<32&&i%8<7)s+=line(x,y,...pts[i+9],'#eddde6',.8);
      });
      pts.forEach(([x,y],i)=>s+=circle(x,y,[11,20,26].includes(i)?7:2.8,[11,20,26].includes(i)?P:i%5?K:'#ccb6c3'));
      return s+label(29,295,'NO CENTER. EVERY CONNECTION MATTERS.',G,9);
    },
    11: () => {
      let s=path('M60 235 290 103 537 243 307 300Z','#eadce4',1,'#fff6fa');
      s+=cube(93,170,105)+cube(198,109,105)+cube(303,170,105)+cube(198,230,105);
      s+=cube(198,101,105,'#ff85bd',P,'#d91c74','#d91c74');
      return s+line(74,253,74,82,L,1,'stroke-dasharray="3 5"')+label(82,76,'СОБИРАЕТСЯ В БОЛЬШЕЕ',G,9);
    },
    12: () => {
      let s='';
      [[28,65,101],[145,120,101],[262,65,101],[379,120,101],[496,65,76]].forEach(([x,y,w],i)=>{
        s+=rect(x,y,w,101,i===2?P:W,20,i===2?P:L,1.2);
        const mx=x+w/2,my=y+50;
        if(i===2)s+=path(`M${mx-14} ${my}h28m-14-14v28`,W,2.2);
        else {s+=rect(mx-15,my-15,30,30,'none',7,G);s+=circle(mx,my,3,G);}
        if(i<4)s+=path(`M${x+w} ${y+50}h8q8 0 8 8v39q0 8 8 8h8`,L,1.3);
      });
      return s+label(29,276,'MODULAR BY NATURE',G,10);
    },
    13: () => {
      let s=path('M49 224 284 89 554 245 319 300Z','#e3d3dc',1,'#faf0f6');
      for(let row=0;row<3;row++)for(let col=0;col<4;col++) {
        const x=110+col*69-row*35,y=157+col*40+row*21-((col+row)%3)*30;
        const hot=col===1&&row===1;
        s+=cube(x,y,67,hot?'#ff9bc9':'#fffcfe',hot?P:'#eadce5',hot?'#c41669':'#c9b2c0',hot?'#d01d73':'#c3aab9');
      }
      return s+label(55,58,'ИНФРАСТРУКТУРА ВАШЕЙ СЕТИ',G,9)+line(55,70,55,170,L,1,'stroke-dasharray="2 4"');
    },
    14: () => {
      let s='';
      [85,44,0].forEach((dy,i)=>{
        const fill=i===1?'#ff65ad':i===2?'#fff7fc':'#e9d9e3';
        s+=path(`M64 ${119+dy} 298 ${25+dy} 536 ${119+dy} 301 ${215+dy}Z`,i===1?P:'#cfb7c5',1,fill);
        s+=path(`M64 ${119+dy}v9l237 96 235-96v-9`,i===1?'#bd1464':'#cfb7c5',1,i===1?'#d91e79':'#e5d2de');
        if(i===2) {for(let j=1;j<6;j++)s+=line(64+j*39,119-j*15.6,301+j*39,215-j*15.6,'#e5d2de',.7);s+=circle(300,119,15,P);}
      });
      return s+label(492,29,'LAYER 01',G,8)+label(492,71,'LAYER 02',P,8)+label(492,113,'LAYER 03',G,8);
    },
    15: () => {
      let s=rect(196,45,200,200,'none',0,K,1.2)+circle(296,145,97,'none',P,1.2)+circle(296,145,65,'none',L,1)+line(75,145,520,145,G,.8,'stroke-dasharray="3 5"')+line(296,5,296,285,G,.8,'stroke-dasharray="3 5"');
      [[196,45],[396,45],[196,245],[396,245]].forEach(([x,y])=>{s+=path(`M${x-9} ${y}h18m-9-9v18`,K,1);s+=circle(x,y,2,P);});
      s+=rect(272,121,48,48,P,4)+path('M284 145h24m-12-12v24',W,2);
      return s+line(196,270,396,270,G,.7)+path('M196 265v10m200-10v10',G,.7)+label(296,288,'ОБЩИЙ РЕСУРС',G,8,'text-anchor="middle"')+label(414,43,'FIG. 01',K,9)+label(75,135,'INPUT',G,8)+label(477,135,'OUTPUT',G,8);
    },
    16: () => {
      let s='';
      for(let i=0;i<23;i++) {
        const k=i*6.2;
        s+=path(`M${-50+i*3} 298C${65+k} ${203-k*.7} ${89+k} ${232-k*.65} ${177+k} ${147-k*.45}S${368+k*.68} ${224-k*.93} ${667-i*3} ${21+i*10}`,i%4===0?P:'#f2a3c9',i%4===0?1.5:.8);
      }
      return s+circle(432,143,6,P);
    },
    17: () => {
      const d='M300 149C198 18 75 55 92 162S261 249 300 149 498 46 511 153 401 266 300 149';
      return `<path d="${d}" fill="none" stroke="#ecd3e0" stroke-width="61" stroke-linecap="round"/><path d="${d}" fill="none" stroke="url(#pink-17)" stroke-width="49" stroke-linecap="round"/>`+path('M96 166C103 214 176 253 237 218','#ffbfdd',1.5)+label(300,298,'СВЯЗАНЫ ВОЗМОЖНОСТЯМИ',G,9,'text-anchor="middle"');
    },
    18: () => {
      let s='';
      [[-21,K,1.4],[28,'#d3b4c5',1],[-52,P,1.5],[64,'#d3b4c5',1]].forEach(([a,c,w])=>{s+=`<ellipse cx="300" cy="146" rx="193" ry="66" fill="none" stroke="${c}" stroke-width="${w}" transform="rotate(${a} 300 146)"/>`;});
      return '<g transform="translate(102 50) scale(.66)">'+s+circle(300,146,34,P)+circle(468,82,9,K)+circle(210,275,5,P)+circle(187,34,5,W,P,1.3)+path('M288 146h24m-12-12v24',W,1.8)+'</g>';
    },
    19: () => {
      let s='';
      for(let i=0;i<18;i++){
        let d='';
        for(let x=-20;x<=620;x+=5){const y=146+Math.sin(x/88+i*.16)*(30+i*3.7)+Math.cos(x/161)*i*2;d+=`${x===-20?'M':'L'}${x} ${y.toFixed(2)}`;}
        s+=path(d,i<6?K:i<11?'#bc8aa3':P,i===13?2.3:.9);
      }
      return s+label(30,287,'В ЕДИНОМ РИТМЕ',G,9);
    },
    20: () => {
      let s='';
      for(let i=0;i<12;i++){
        const r=24+i*9;
        s+=path(`M175 145C${175-r} ${145-r*1.1} ${425+r} ${145-r*1.1} 425 145`,i%3?L:P,i%3?.9:1.4);
        s+=path(`M175 145C${175-r} ${145+r*1.1} ${425+r} ${145+r*1.1} 425 145`,i%3?L:K,i%3?.9:1.2);
      }
      return s+circle(175,145,25,K)+circle(425,145,25,P)+path('M164 145h22m-11-11v22',W,1.5)+path('M414 145h22m-11-11v22',W,1.5)+label(175,291,'ВАШИ РЕСУРСЫ',G,8,'text-anchor="middle"')+label(425,291,'ВАША СЕТЬ',G,8,'text-anchor="middle"');
    },
    21: () => {
      let s='';
      const card=(x,y,w,h,title,hot,rotate)=>`<g transform="rotate(${rotate} ${x+w/2} ${y+h/2})">${rect(x+3,y+8,w,h,'#21101a06',17)}${rect(x,y,w,h,hot?P:W,17,hot?P:L)}${label(x+17,y+28,title,hot?'#681535':G,9)}${hot?path(`M${x+17} ${y+64}h${w-34}m-${w/2-17}-18v36`,'#fdd9e9',2):rect(x+17,y+53,40,40,'none',8,G)}${label(x+17,y+h-19,hot?'ВМЕСТЕ':'ПОДКЛЮЧЕНО',hot?W:G,8)}</g>`;
      s+=card(28,54,163,189,'CPU / ВЫЧИСЛЕНИЯ',false,-8)+card(213,12,163,213,'NETWORK / СЕТЬ',true,4)+card(397,59,163,189,'RAM / ПАМЯТЬ',false,9);
      return s;
    },
    22: () => rect(12,14,576,269,K,15)+circle(33,35,3.5,'#81606e')+circle(46,35,3.5,'#81606e')+circle(59,35,3.5,'#81606e')+label(300,39,'tmc / your-network', '#9d788c',9,'text-anchor="middle"')+line(13,55,587,55,'#3e2734')+label(36,91,'$ tmc connect','#f3dfeb',13)+label(36,124,'→ devices.find()','#a98c9c',12)+label(36,153,'→ resources.combine()','#a98c9c',12)+label(36,197,'✓ Сеть начинается с вас.',P,16)+label(36,251,'>','#e8d0dd',13)+rect(55,238,8,17,P,1),
    23: () => {
      let s=line(42,155,557,155,'#d9bdce',1.5);
      for(let i=0;i<6;i++){
        const x=17+i*97,y=i===3?58:81;
        s+=rect(x,y,80,141,i===3?P:'#fffafb',13,i===3?P:L);
        for(let j=0;j<4;j++)s+=rect(x+20+j*11,y-5,5,8,i===3?P:'#dac5d1',1);
        s+=rect(x+16,y+30,48,48,'none',8,i===3?'#ffcce3':G,1)+path(`M${x+28} ${y+54}h24m-12-12v24`,i===3?W:G,1.5)+label(x+40,y+113,`0${i+1}`,i===3?W:G,10,'text-anchor="middle"');
      }
      return s+label(299,281,'У КАЖДОГО ЕСТЬ СВОЁ МЕСТО',G,9,'text-anchor="middle"');
    },
    24: () => label(18,176,'n',P,132,'class="sans" font-weight="650"')+path('M138 104l39 39m0-39-39 39',K,3)+rect(214,72,101,82,'none',7,K,2.5)+path('M239 173h52m-26-19v19',K,2.5)+path('M350 125h69m-14-14 14 14-14 14',K,2)+label(459,169,'∞',P,135,'class="sans" font-weight="500"')+label(17,254,'МНОГО КОМПЬЮТЕРОВ',G,10)+label(400,254,'ОБЩИЕ ВОЗМОЖНОСТИ',G,10),
    25: () => {
      let s=rect(13,13,574,273,'#fff',18,L)+rect(13,13,151,273,'#fbf1f7',18,L)+line(164,14,164,285,L);
      s+=label(33,41,'ВАША СЕТЬ',G,9)+label(185,41,'РЕСУРСЫ',G,9);
      const points=[[54,113],[123,94],[111,178],[51,215]];
      points.forEach(([x,y],i)=>{s+=line(x,y,...points[(i+1)%4],'#e3adcb',1);s+=line(x,y,84,153,'#e3adcb',1);s+=circle(x,y,5,P);});
      s+=circle(84,153,16,P)+path('M78 153h12m-6-6v12',W,1.4)+label(185,83,'CPU',K,10)+label(185,183,'RAM',K,10);
      s+=path('M187 144h44v-17h38v-24h48v17h58v-37h44v18h56v-29h75',P,2.5);
      s+=path('M187 149h363',L,1)+path('M187 249h363',L,1);
      for(let i=0;i<18;i++)s+=rect(187+i*20,208,13,29,i<11?P:'#f4d9e6',3);
      return s;
    },
    26: () => {
      let s='';
      for(let r=0;r<8;r++)for(let c=0;c<17;c++){
        const x=34+c*33,y=25+r*33,dist=Math.hypot(c-8,r-3.5);
        s+=circle(x,y,Math.max(1.1,7.4-dist*.66),(r===3||r===4||c===8)?P:'#654051');
      }
      s+=path('M274 128h52m-26-26v52','#fff3fa',2);
      return s;
    },
    27: () => {
      let s=path('M117 274V149a183 123 0 0 1 366 0v125Z','#ffaad1',0,'#ffaad1');
      s+=path('M174 274V171a126 90 0 0 1 252 0v103Z',P,0,P);
      s+=path('M226 274V190a74 58 0 0 1 148 0v84Z','#20101a',0,'#20101a');
      s+=circle(300,190,20,'#fff8fc');
      return s+label(20,294,'МЫШЛЕНИЕ БЕЗ ГРАНИЦ','#691338',9);
    },
    28: () => {
      let s=circle(300,147,132,'#ffdb00')+path('M168 147h264m-132-132v264','#e6bb00',1);
      s+=`<ellipse cx="300" cy="147" rx="57" ry="95" fill="none" stroke="#e7bd00"/><ellipse cx="300" cy="147" rx="104" ry="95" fill="none" stroke="#e7bd00"/><ellipse cx="300" cy="147" rx="131" ry="47" fill="none" stroke="#e7bd00"/>`;
      s+=`<ellipse cx="300" cy="147" rx="13" ry="47" fill="${K}" transform="rotate(-8 300 147)"/>`;
      return s+circle(459,66,7,P)+line(453,72,421,104,P,1)+label(470,61,'TMC',G,10)+label(300,297,'ВАША ТОЧКА ПРИТЯЖЕНИЯ',G,9,'text-anchor="middle"');
    },
    29: () => {
      let s=rect(0,8,600,272,'none',0,K,2)+rect(0,8,200,272,P,0,K,2)+rect(400,8,200,272,K,0,K,2)+line(200,8,200,280,K,2)+line(400,8,400,280,K,2);
      s+=path('M45 143h110m-55-55v110',K,17);
      for(let i=0;i<9;i++)s+=circle(235+(i%3)*32,73+Math.floor(i/3)*53,10,K);
      s+=path('M445 93h105v105H445Z',W,2)+path('M445 93l105 105m0-105L445 198',W,2);
      return s;
    },
    30: () => {
      let s='';
      for(let i=0;i<9;i++){
        const x=100+i*17,w=400-i*34,h=262-i*23,y=11+i*20,r=w/2;
        s+=path(`M${x} 292V${y+r*.72}A${r} ${r*.72} 0 0 1 ${x+w} ${y+r*.72}V292`,i%3===0?P:i%3===1?'#351322':'#c894af',i===0?2:1.3);
      }
      s+=path('M240 292v-87a60 45 0 0 1 120 0v87',K,0,K)+circle(300,216,14,P);
      s+=line(37,292,563,292,L)+label(22,270,'ВХОД В БОЛЬШЕЕ',G,9);
      return s;
    }
  };
  const directions = [
    ['Чистая редактура','type','Воздух, точная типографика и одна акцентная точка.'],
    ['Швейцарская сетка','type','Строгая сетка, крупный набор и ритм модулей.'],
    ['Большая точка','type','Центральная композиция и один простой выразительный знак.'],
    ['На полях','type','Асимметрия, вертикальная ось и спокойная иерархия.'],
    ['Манифест','type','Контрастный набор и формула совместных возможностей.'],
    ['Один поток','network','Тонкие линии собирают разные устройства в единый поток.'],
    ['Живая сеть','network','Лёгкая топология из узлов без рамок и карточек.'],
    ['Общие маршруты','network','Графичный язык транспортных схем: линии, станции, связи.'],
    ['Точки контакта','network','Кольцевая структура с открытой, прозрачной геометрией.'],
    ['Сеть без центра','network','Распределённая сетка, в которой важен каждый узел.'],
    ['Изометрия','geometry','Объёмные модули собираются в одну общую структуру.'],
    ['Модульная система','geometry','Скруглённые детали, ясный ритм и акцентный модуль.'],
    ['Архитектура','geometry','Абстрактный вычислительный ландшафт из простых объёмов.'],
    ['Ресурсный слой','geometry','Три парящих слоя: инженерная точность и лёгкий объём.'],
    ['Чертёж','geometry','Координатная сетка, тонкие оси и инженерная графика.'],
    ['Поле возможностей','flow','Мягкие контуры формируют общий рельеф сети.'],
    ['Лента Мёбиуса','flow','Непрерывная объёмная лента как образ общего процесса.'],
    ['Орбита','flow','Лаконичная пространственная графика без компьютерных иконок.'],
    ['В едином ритме','flow','Чёрные и розовые волны сходятся в общий рисунок.'],
    ['Магнитное поле','flow','Два простых полюса и множество связей между ними.'],
    ['Карточки ресурсов','product','Мягкий предметный образ: вычисления, сеть и память.'],
    ['Командная строка','product','Тёмный терминал и технический характер платформы.'],
    ['У каждого своё место','product','Последовательность слотов как образ подключаемых устройств.'],
    ['Общий знаменатель','product','Крупная визуальная формула вместо иллюстрации сети.'],
    ['Панель управления','product','Абстрактный интерфейс показывает смысл продукта.'],
    ['Чёрный постер','brand','Тёмная плоскость, светлый набор и яркая матрица точек.'],
    ['Розовый монолит','brand','Насыщенный фирменный цвет и сильный архитектурный знак.'],
    ['Точка притяжения','brand','Жёлтый акцент от логотипа и графичная сфера.'],
    ['Брутальная сетка','brand','Жёсткая рамка, плотная типографика и простые символы.'],
    ['Портал','brand','Перспектива из вложенных арок: вход в общее пространство.'],
  ];
  const groups={type:'Типографика',network:'Сеть',geometry:'Геометрия',flow:'Пластика',product:'Продукт',brand:'Смелые'};
  function markup(id) {
    let extras='';
    if(id===4)extras='<div class="margin-line"></div><div class="margin-text">DISTRIBUTED BY DESIGN</div>';
    if(id===12)extras='<div class="top-module">+</div>';
    const second=id===1?'Ваши<br>возможности<em>.</em>':id===5?'Ваши<br><em>возможности.</em>':'Ваши<br>возможности.';
    return `<div class="kicker">THE MUTATING COMPANY <b>/</b> ЛИЧНЫЙ КАБИНЕТ</div>${extras}<div class="copy"><h1><span>Ваша сеть.</span><span class="second">${second}</span></h1><p class="description">Объединяйте компьютеры, распределяйте работу и создавайте вместе.</p></div><div class="art">${svg(art[id](),id)}</div><div class="scene-footer"><i></i> Инфраструктура для ваших задач</div>`;
  }
  window.AuthDesigns = directions.map(([name,group,description],i)=>({id:i+1,name,group,groupName:groups[group],description,markup:markup(i+1)}));
  let instance=0;
  window.createAuthScene = (design) => {
    const el=document.createElement('div');
    el.className=`scene v${String(design.id).padStart(2,'0')}`;
    const suffix=++instance;
    el.innerHTML=design.markup.replaceAll(`pink-${design.id}`,`pink-${design.id}-${suffix}`).replaceAll(`silver-${design.id}`,`silver-${design.id}-${suffix}`);
    return el;
  };
})();
