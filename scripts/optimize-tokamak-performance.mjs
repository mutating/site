import fs from "node:fs";

const filePath = new URL("../public/tokamak-reactor.html", import.meta.url);
let html = fs.readFileSync(filePath, "utf8");

const revision = "demand-batched-v5";
const previousRevision = "demand-batched-v4";
const lightPaletteRevision = "warm-site-pink-v2";
const previousLightPaletteRevision = "warm-site-pink-v1";

function replaceOnce(label, previousValue, nextValue) {
  const matches = html.split(previousValue).length - 1;

  if (matches !== 1) {
    throw new Error(`${label}: expected one source match, found ${matches}`);
  }

  html = html.replace(previousValue, nextValue);
}

if (
  html.includes(`dataset.performanceRevision="${revision}"`) &&
  !html.includes(`dataset.lightPalette="${lightPaletteRevision}"`)
) {
  const hasPreviousLightPalette = html.includes(
    `dataset.lightPalette="${previousLightPaletteRevision}"`,
  );
  replaceOnce(
    "warm light-theme plasma core",
    hasPreviousLightPalette
      ? 'lightCoreColor=new Bt("#ff2c70").multiplyScalar(1.5)'
      : 'lightCoreColor=new Bt("#ff2c92").multiplyScalar(1.5)',
    'lightCoreColor=new Bt("#ff2c58").multiplyScalar(1.5)',
  );
  replaceOnce(
    "warm light-theme plasma outer shader color",
    hasPreviousLightPalette
      ? "vec3 outerColor=vec3(1.0,0.173,0.439);"
      : "vec3 outerColor=vec3(1.0,0.173,0.573);",
    "vec3 outerColor=vec3(1.0,0.173,0.345);",
  );
  replaceOnce(
    "warm light-theme plasma inner shader color",
    hasPreviousLightPalette
      ? "vec3 innerColor=vec3(1.0,0.4,0.565);"
      : "vec3 innerColor=vec3(1.0,0.4,0.682);",
    "vec3 innerColor=vec3(1.0,0.4,0.52);",
  );
  replaceOnce(
    "warm light-theme plasma halo",
    hasPreviousLightPalette
      ? '[.17,.12,16723056,"light","halo"]'
      : '[.17,.12,16723090,"light","halo"]',
    '[.17,.12,16723032,"light","halo"]',
  );
  replaceOnce(
    "warm light-theme wireframe",
    hasPreviousLightPalette
      ? 'LIGHT_WIRE_COLOR="#ff2c70"'
      : 'LIGHT_WIRE_COLOR="#ff2c92"',
    'LIGHT_WIRE_COLOR="#ff2c58"',
  );
  replaceOnce(
    "light palette diagnostics",
    hasPreviousLightPalette
      ? `s.domElement.dataset.lightPalette="${previousLightPaletteRevision}",`
      : "s.domElement.dataset.darkWireColor=DARK_WIRE_COLOR,",
    hasPreviousLightPalette
      ? `s.domElement.dataset.lightPalette="${lightPaletteRevision}",`
      : `s.domElement.dataset.lightPalette="${lightPaletteRevision}",s.domElement.dataset.darkWireColor=DARK_WIRE_COLOR,`,
  );
  fs.writeFileSync(filePath, html);
  process.exit(0);
}

if (html.includes(`dataset.performanceRevision="${revision}"`)) {
  process.exit(0);
}

const originalPlasmaRing =
  'function mL(e){let i=new Ln;i.name="energy-ring-front";let s=new Ln;s.name="energy-ring-back",e.add(s,i);let a=new We(new Dc(2.02,3.32,160),new Ji({color:16720991,transparent:!0,opacity:.05,side:oi,blending:ba,depthTest:!1,depthWrite:!1}));a.rotation.x=-Math.PI/2,a.position.y=.46-.02,a.renderOrder=3,e.add(a);for(let[r,o,l]of[[.22,.085,16722024],[.125,.15,16723051],[.052,1,hL]])for(let[c,h]of[[s,!1],[i,!0]]){let p=new Bo(2.55,r,12,84,Math.PI);h||p.rotateZ(Math.PI);let u=new We(p,new Ji({color:l,toneMapped:r!==.052,transparent:!0,opacity:o,blending:ba,depthTest:!1,depthWrite:!1}));u.rotation.x=Math.PI/2,u.position.y=.46,u.renderOrder=h?r===.052?9:8:r===.052?3.5:3,c.add(u)}}';
const wideLightPlasmaRing =
  'function pM(e,t){return new Ue({uniforms:{uColor:{value:new Bt(t)},uOpacity:{value:e}},vertexShader:`varying vec3 vNormal;varying vec3 vViewDir;void main(){vec4 mvPosition=modelViewMatrix*vec4(position,1.0);vNormal=normalize(normalMatrix*normal);vViewDir=normalize(-mvPosition.xyz);gl_Position=projectionMatrix*mvPosition;}`,fragmentShader:`uniform vec3 uColor;uniform float uOpacity;varying vec3 vNormal;varying vec3 vViewDir;void main(){float facing=abs(dot(normalize(vNormal),normalize(vViewDir)));float feather=pow(smoothstep(0.0,1.0,facing),4.0);gl_FragColor=vec4(uColor,uOpacity*feather);}`,transparent:!0,blending:ba,depthTest:!1,depthWrite:!1,side:oi,toneMapped:!1})}function plasmaLightMaterial(e){return new Ue({uniforms:{uOpacity:{value:e}},vertexShader:`varying vec3 vNormal;varying vec3 vViewDir;void main(){vec4 mvPosition=modelViewMatrix*vec4(position,1.0);vNormal=normalize(normalMatrix*normal);vViewDir=normalize(-mvPosition.xyz);gl_Position=projectionMatrix*mvPosition;}`,fragmentShader:`uniform float uOpacity;varying vec3 vNormal;varying vec3 vViewDir;void main(){float facing=abs(dot(normalize(vNormal),normalize(vViewDir)));float outer=pow(smoothstep(0.0,1.0,facing),1.5);float inner=pow(smoothstep(0.0,1.0,facing),8.0);vec3 outerColor=vec3(1.0,0.025,0.28);vec3 innerColor=vec3(1.0,0.62,0.82);vec3 glowColor=mix(outerColor,innerColor,inner);float alpha=uOpacity*(0.32*outer+0.68*inner);gl_FragColor=vec4(glowColor,alpha);}`,transparent:!0,blending:ba,depthTest:!1,depthWrite:!1,side:oi,toneMapped:!1})}function mL(e){let i=new Ln;i.name="energy-ring-front";let s=new Ln;s.name="energy-ring-back",e.add(s,i);let a=new We(new Dc(2.02,3.32,160),new Ji({color:16720991,transparent:!0,opacity:.05,side:oi,blending:ba,depthTest:!1,depthWrite:!1}));a.rotation.x=-Math.PI/2,a.position.y=.46-.02,a.renderOrder=3,e.add(a);for(let[r,o,l,z]of[[.36,.18,16722024,"dark"],[.48,.14,16722024,"light"],[.052,1,hL,"both"],[.018,1,16777215,"light"]])for(let[c,h]of[[s,!1],[i,!0]]){let p=new Bo(2.55,r,12,84,Math.PI);h||p.rotateZ(Math.PI);let u=new We(p,r===.36?pM(o,l):r===.48?plasmaLightMaterial(o):new Ji({color:r===.018?new Bt(l).multiplyScalar(2.8):l,toneMapped:!1,transparent:!0,opacity:o,blending:ba,depthTest:!1,depthWrite:!1}));u.userData.plasmaTheme=z,u.rotation.x=Math.PI/2,u.position.y=.46,u.renderOrder=h?r<=.052?9:8:r<=.052?3.5:3,c.add(u)}}';
const themeAwarePlasmaRing =
  'function pM(e,t){return new Ue({uniforms:{uColor:{value:new Bt(t)},uOpacity:{value:e}},vertexShader:`varying vec3 vNormal;varying vec3 vViewDir;void main(){vec4 mvPosition=modelViewMatrix*vec4(position,1.0);vNormal=normalize(normalMatrix*normal);vViewDir=normalize(-mvPosition.xyz);gl_Position=projectionMatrix*mvPosition;}`,fragmentShader:`uniform vec3 uColor;uniform float uOpacity;varying vec3 vNormal;varying vec3 vViewDir;void main(){float facing=abs(dot(normalize(vNormal),normalize(vViewDir)));float feather=pow(smoothstep(0.0,1.0,facing),4.0);gl_FragColor=vec4(uColor,uOpacity*feather);}`,transparent:!0,blending:ba,depthTest:!1,depthWrite:!1,side:oi,toneMapped:!1})}function plasmaLightMaterial(e){return new Ue({uniforms:{uOpacity:{value:e}},vertexShader:`varying vec3 vNormal;varying vec3 vViewDir;void main(){vec4 mvPosition=modelViewMatrix*vec4(position,1.0);vNormal=normalize(normalMatrix*normal);vViewDir=normalize(-mvPosition.xyz);gl_Position=projectionMatrix*mvPosition;}`,fragmentShader:`uniform float uOpacity;varying vec3 vNormal;varying vec3 vViewDir;void main(){float facing=abs(dot(normalize(vNormal),normalize(vViewDir)));float outer=pow(smoothstep(0.0,1.0,facing),1.8);float inner=pow(smoothstep(0.0,1.0,facing),8.0);vec3 outerColor=vec3(1.0,0.025,0.28);vec3 innerColor=vec3(1.0,0.42,0.66);vec3 glowColor=mix(outerColor,innerColor,inner);float alpha=uOpacity*(0.35*outer+0.65*inner);gl_FragColor=vec4(glowColor,alpha);}`,transparent:!0,blending:ba,depthTest:!1,depthWrite:!1,side:oi,toneMapped:!1})}function mL(e){let i=new Ln;i.name="energy-ring-front";let s=new Ln;s.name="energy-ring-back",e.add(s,i);let a=new We(new Dc(2.02,3.32,160),new Ji({color:16720991,transparent:!0,opacity:.05,side:oi,blending:ba,depthTest:!1,depthWrite:!1}));a.userData.plasmaTheme="dark",a.rotation.x=-Math.PI/2,a.position.y=.46-.02,a.renderOrder=3,e.add(a);for(let[r,o,l,z,k]of[[.36,.18,16722024,"dark","halo"],[.052,1,hL,"dark","core"],[.18,.08,16722024,"light","halo"],[.078,1,16723090,"light","core"],[.032,1,16777215,"light","hot"]])for(let[c,h]of[[s,!1],[i,!0]]){let p=new Bo(2.55,r,12,84,Math.PI);h||p.rotateZ(Math.PI);let u=new We(p,k==="halo"?(z==="dark"?pM(o,l):plasmaLightMaterial(o)):new Ji({color:l,toneMapped:!1,transparent:!0,opacity:o,blending:ba,depthTest:!1,depthWrite:!1}));u.userData.plasmaTheme=z,u.rotation.x=Math.PI/2,u.position.y=.46,u.renderOrder=h?r<=.052?9:8:r<=.052?3.5:3,c.add(u)}}';
const standardPlasmaVisibility =
  'if(F?.blending===ba){w.visible=!0;return}';
const themeAwarePlasmaVisibility =
  'if(w.userData.plasmaTheme&&w.userData.plasmaTheme!=="both"){w.visible=w.userData.plasmaTheme===H;return}if(F?.blending===ba){w.visible=!0;return}';

if (html.includes('dataset.performanceRevision="demand-batched-v3"')) {
  replaceOnce(
    "compact light plasma glow",
    wideLightPlasmaRing,
    themeAwarePlasmaRing,
  );
  if (!html.includes(themeAwarePlasmaVisibility)) {
    replaceOnce(
      "theme-aware plasma visibility",
      standardPlasmaVisibility,
      themeAwarePlasmaVisibility,
    );
  }
  replaceOnce(
    "compact light-theme bloom",
    'H=k,n.background=null,M.uniforms.lightMode.value=H==="light"?1:0;',
    'H=k,n.background=null,d.radius=H==="light"?.04:.18,d.strength=H==="light"?.28:1.15,s.domElement.dataset.bloomRadius=String(d.radius),s.domElement.dataset.bloomStrength=String(d.strength),M.uniforms.lightMode.value=H==="light"?1:0;',
  );
  replaceOnce(
    "legacy performance revision",
    'dataset.performanceRevision="demand-batched-v3"',
    `dataset.performanceRevision="${previousRevision}"`,
  );
  replaceOnce(
    "plasma halo diagnostics",
    's.domElement.dataset.plasmaHalo="theme-aware-light-v1";',
    's.domElement.dataset.plasmaHalo="theme-aware-light-v2";',
  );
}

if (html.includes(`dataset.performanceRevision="${previousRevision}"`)) {
  replaceOnce(
    "remove production GPU timer setup",
    `let jG=s.getContext(),qG=jG.getExtension("EXT_disjoint_timer_query_webgl2"),pG=!1,zG=!1;
s.domElement.dataset.gpuTimer=qG?"supported":"unavailable";
let yG=w=>{
  if(zG){jG.deleteQuery(w),pG=!1;return}
  if(jG.getQueryParameter(w,jG.QUERY_RESULT_AVAILABLE)){
    let F=jG.getParameter(qG.GPU_DISJOINT_EXT);
    F||(s.domElement.dataset.gpuFrameMs=(jG.getQueryParameter(w,jG.QUERY_RESULT)/1e6).toFixed(3)),jG.deleteQuery(w),pG=!1
  }else setTimeout(()=>yG(w),16)
};`,
    's.domElement.dataset.gpuTimer="disabled";',
  );
  replaceOnce(
    "remove per-frame GPU timer query",
    `render(){
    let w=null;
    qG&&!pG&&(w=jG.createQuery(),w&&(pG=!0,jG.beginQuery(qG.TIME_ELAPSED_EXT,w)));
    s.info.reset(),k.renderToScreen=!1,k.render(s,A,p,0,!1),d.renderToScreen=!1,d.render(s,A,p,0,!1),v.renderToScreen=!1,v.render(s,S,A,0,!1),M.renderToScreen=!0,M.render(s,A,S,0,!1);
    w&&(jG.endQuery(qG.TIME_ELAPSED_EXT),yG(w))
  },`,
    `render(){
    s.info.reset(),k.renderToScreen=!1,k.render(s,A,p,0,!1),d.renderToScreen=!1,d.render(s,A,p,0,!1),v.renderToScreen=!1,v.render(s,S,A,0,!1),M.renderToScreen=!0,M.render(s,A,S,0,!1)
  },`,
  );
  replaceOnce(
    "remove GPU timer cleanup",
    "return()=>{zG=!0,x&&cancelAnimationFrame(x),",
    "return()=>{x&&cancelAnimationFrame(x),",
  );
  replaceOnce(
    "performance revision",
    `dataset.performanceRevision="${previousRevision}"`,
    `dataset.performanceRevision="${revision}"`,
  );
  fs.writeFileSync(filePath, html);
  process.exit(0);
}

replaceOnce(
  "renderer default-framebuffer antialiasing",
  'let s=new Kd({antialias:!0,alpha:!0,powerPreference:"high-performance"});',
  'let s=new Kd({antialias:!1,alpha:!0,powerPreference:"high-performance"});',
);

replaceOnce(
  "theme-aware light plasma glow",
  originalPlasmaRing,
  themeAwarePlasmaRing,
);

replaceOnce(
  "shared tokamak materials",
  'function hp(e,t=Av,n=!1){return new Lc({color:t,emissive:new Bt(3213590),emissiveIntensity:.38,metalness:.72,roughness:.3,transparent:!0,opacity:e,side:oi,depthWrite:n})}function cu(e,t=Ev){return new Lo({color:t,transparent:!0,opacity:Math.min(1,e*fL),depthWrite:!1})}',
  'let bM=new Map,wM=new Map;function hp(e,t=Av,n=!1){let i=`${e}|${t}|${n}`,s=bM.get(i);return s||(s=new Lc({color:t,emissive:new Bt(3213590),emissiveIntensity:.38,metalness:.72,roughness:.3,transparent:!0,opacity:e,side:oi,depthWrite:n,forceSinglePass:!0}),bM.set(i,s)),s}function cu(e,t=Ev){let n=`${e}|${t}`,i=wM.get(n);return i||(i=new Lo({color:t,transparent:!0,opacity:Math.min(1,e*fL),depthWrite:!1}),wM.set(n,i)),i}',
);

replaceOnce(
  "static line batching",
  'function SL(){let e=new Ln;return gL(e),vL(e),yL(e),e}',
  `function BL(e){
  e.updateMatrixWorld(!0);
  let t=[],n=new Map;
  e.traverse(i=>{
    if(!(i instanceof Qi)||Array.isArray(i.material))return;
    let s=i.geometry?.getAttribute("position");
    if(!s)return;
    t.push(i);
    let a=\`\${i.renderOrder}|\${i.material.uuid}\`,r=n.get(a);
    r?r.push(i):n.set(a,[i])
  });
  for(let i of n.values()){
    if(i.length<2)continue;
    let s=0;
    for(let l of i){
      let c=l.geometry.getAttribute("position"),h=l.geometry.index;
      s+=h?h.count:c.count
    }
    let a=new Float32Array(s*3),r=0,o=new R;
    for(let l of i){
      let c=l.geometry.getAttribute("position"),h=l.geometry.index,p=h?h.count:c.count;
      for(let u=0;u<p;u++){
        let d=h?h.getX(u):u;
        o.fromBufferAttribute(c,d).applyMatrix4(l.matrixWorld);
        a[r++]=o.x,a[r++]=o.y,a[r++]=o.z
      }
    }
    let l=new ze;
    l.setAttribute("position",new re(a,3)),l.computeBoundingSphere();
    let c=new Qi(l,i[0].material);
    c.renderOrder=i[0].renderOrder,e.add(c);
    for(let h of i)h.parent?.remove(h),h.geometry.dispose()
  }
  return{source:t.length,batched:[...n.values()].reduce((i,s)=>i+(s.length>1?1:s.length),0)}
}function SL(){let e=new Ln;gL(e),vL(e),yL(e);let t=BL(e);return e.userData.sourceWireObjects=t.source,e.userData.batchedWireObjects=t.batched,e}`,
);

replaceOnce(
  "bloom output buffer swapping",
  'this.clearColor=new Bt(0,0,0),this.needsSwap=!1,this.renderTargetsHorizontal=[]',
  'this.clearColor=new Bt(0,0,0),this.needsSwap=!0,this.renderTargetsHorizontal=[]',
);

replaceOnce(
  "bloom base texture uniform",
  'uniforms:{blurTexture1:{value:null},blurTexture2:{value:null},blurTexture3:{value:null},blurTexture4:{value:null},blurTexture5:{value:null},bloomStrength:{value:1}',
  'uniforms:{baseTexture:{value:null},blurTexture1:{value:null},blurTexture2:{value:null},blurTexture3:{value:null},blurTexture4:{value:null},blurTexture5:{value:null},bloomStrength:{value:1}',
);

replaceOnce(
  "bloom base texture declaration",
  `				uniform sampler2D blurTexture1;
				uniform sampler2D blurTexture2;`,
  `				uniform sampler2D baseTexture;
				uniform sampler2D blurTexture1;
				uniform sampler2D blurTexture2;`,
);

replaceOnce(
  "fused bloom composition",
  `					float bloomAlpha = max( bloom.r, max( bloom.g, bloom.b ) );
					gl_FragColor = vec4( bloom, bloomAlpha );`,
  `					float bloomAlpha = max( bloom.r, max( bloom.g, bloom.b ) );
					gl_FragColor = texture2D( baseTexture, vUv ) + vec4( bloom, bloomAlpha );`,
);

replaceOnce(
  "single-pass bloom output",
  'this._fsQuad.material=this.compositeMaterial,this.compositeMaterial.uniforms.bloomStrength.value=this.strength,this.compositeMaterial.uniforms.bloomRadius.value=this.radius,this.compositeMaterial.uniforms.bloomTintColors.value=this.bloomTintColors,t.setRenderTarget(this.renderTargetsHorizontal[0]),t.clear(),this._fsQuad.render(t),this._fsQuad.material=this.blendMaterial,this.copyUniforms.tDiffuse.value=this.renderTargetsHorizontal[0].texture,a&&t.state.buffers.stencil.setTest(!0),this.renderToScreen?(t.setRenderTarget(null),this._fsQuad.render(t)):(t.setRenderTarget(i),this._fsQuad.render(t)),t.setClearColor(this._oldClearColor,this._oldClearAlpha),t.autoClear=r',
  'this._fsQuad.material=this.compositeMaterial,this.compositeMaterial.uniforms.baseTexture.value=i.texture,this.compositeMaterial.uniforms.bloomStrength.value=this.strength,this.compositeMaterial.uniforms.bloomRadius.value=this.radius,this.compositeMaterial.uniforms.bloomTintColors.value=this.bloomTintColors,a&&t.state.buffers.stencil.setTest(!0),t.setRenderTarget(this.renderToScreen?null:n),t.clear(),this._fsQuad.render(t),t.setClearColor(this._oldClearColor,this._oldClearAlpha),t.autoClear=r',
);

replaceOnce(
  "paired bilinear gaussian sampling",
  `					for ( int i = 1; i < KERNEL_RADIUS; i ++ ) {

						float x = float( i );
						float w = gaussianCoefficients[i];
						vec2 uvOffset = direction * invSize * x;
						vec3 sample1 = texture2D( colorTexture, vUv + uvOffset ).rgb;
						vec3 sample2 = texture2D( colorTexture, vUv - uvOffset ).rgb;
						diffuseSum += ( sample1 + sample2 ) * w;

					}`,
  `					for ( int i = 1; i < KERNEL_RADIUS - 1; i += 2 ) {

						float w1 = gaussianCoefficients[i];
						float w2 = gaussianCoefficients[i + 1];
						float w = w1 + w2;
						float x = ( float( i ) * w1 + float( i + 1 ) * w2 ) / w;
						vec2 uvOffset = direction * invSize * x;
						vec3 sample1 = texture2D( colorTexture, vUv + uvOffset ).rgb;
						vec3 sample2 = texture2D( colorTexture, vUv - uvOffset ).rgb;
						diffuseSum += ( sample1 + sample2 ) * w;

					}

					float finalX = float( KERNEL_RADIUS - 1 );
					float finalW = gaussianCoefficients[KERNEL_RADIUS - 1];
					vec2 finalOffset = direction * invSize * finalX;
					vec3 finalSample1 = texture2D( colorTexture, vUv + finalOffset ).rgb;
					vec3 finalSample2 = texture2D( colorTexture, vUv - finalOffset ).rgb;
					diffuseSum += ( finalSample1 + finalSample2 ) * finalW;`,
);

replaceOnce(
  "tokamak runtime",
  `let H="",P=new WeakMap,C=T=>{let A=T.data;if(A&&A.type==="tokamak-state"){Number.isFinite(A.rotationX)&&(o.rotation.x=A.rotationX),Number.isFinite(A.modelScale)&&o.scale.setScalar(A.modelScale);let S=A.theme==="light"?"light":"dark",k=0,I=0;S!==H&&(H=S,n.background=null,M.uniforms.lightMode.value=H==="light"?1:0,o.traverse(w=>{if(w instanceof Qi&&(k+=1),w instanceof We){let D=w.material;if(!D)return;if(D.blending===ba){w.visible=!0;return}P.has(D)||P.set(D,{visible:w.visible,colorWrite:D.colorWrite,depthWrite:D.depthWrite,depthTest:D.depthTest,transparent:D.transparent}),w.visible=!0,H==="light"?(D.colorWrite=!1,D.depthWrite=!0,D.depthTest=!0,D.transparent=!1,I+=1):Object.assign(D,P.get(D)),D.needsUpdate=!0}}),s.domElement.dataset.reactorTheme=H,s.domElement.dataset.wireObjects=String(k),s.domElement.dataset.lightDepthOccluders=String(I)),window.parent!==window&&window.parent.postMessage({type:"tokamak-state-applied",theme:H,wireObjects:k,depthOccluders:I},"*")}};window.addEventListener("message",C),window.parent!==window&&window.parent.postMessage({type:"tokamak-ready"},"*");
let l=o.getObjectByName("energy-ring-front"),c=o.getObjectByName("energy-ring-back"),h=new tp(i,s.domElement);h.target.copy(Tv),h.enabled=!1,h.enableDamping=!0,h.dampingFactor=.055,h.rotateSpeed=.62,h.zoomSpeed=.72,h.minDistance=7.1,h.maxDistance=22,h.screenSpacePanning=!1,h.update();let p=new Be(1,1,{type:Ke,samples:Math.min(4,s.capabilities.maxSamples)});p.texture.name="ReactorComposer.MSAA";let u=new ip(s,p);u.addPass(new ap(n,i));let d=new Zo(new Q(t.clientWidth,t.clientHeight),1.15,.18,1.05);s.domElement.dataset.bloomRadius=".18",u.addPass(d);let v=new op;u.addPass(v);let M=new sp;u.addPass(M);let m=()=>{i.position.copy(D1),h.target.copy(Tv),h.update()},f=T=>{T.key.toLowerCase()==="r"&&m()};window.addEventListener("keydown",f),s.domElement.addEventListener("dblclick",m);let g=()=>{let T=t.clientWidth,A=t.clientHeight;i.aspect=T/A,i.updateProjectionMatrix(),s.setSize(T,A),u.setSize(T,A)},_=new ResizeObserver(g);_.observe(t),g();let x=0,E=()=>{x=requestAnimationFrame(E),h.update();let T=Math.atan2(i.position.x,i.position.z);l&&(l.rotation.y=T),c&&(c.rotation.y=T),u.render()};return E(),()=>{cancelAnimationFrame(x),_.disconnect(),window.removeEventListener("message",C),window.removeEventListener("keydown",f),s.domElement.removeEventListener("dblclick",m),h.dispose(),d.dispose(),v.dispose(),M.dispose(),u.dispose(),s.dispose(),t.removeChild(s.domElement),n.traverse(T=>{if(T instanceof We||T instanceof Qi){T.geometry?.dispose();let A=T.material;Array.isArray(A)?A.forEach(S=>S.dispose()):A?.dispose()}})}},[]),(0,L1.jsx)("div",{ref:e,className:"reactor-scene"})}`,
  `let H="",P=new WeakMap,O=new Ji({color:0,side:oi,colorWrite:!1,depthWrite:!0,depthTest:!0,toneMapped:!1}),V=window.parent===window,E=()=>{},C=T=>{
  let A=T.data;
  if(!A||A.type!=="tokamak-state")return;
  let S=!1;
  Number.isFinite(A.rotationX)&&o.rotation.x!==A.rotationX&&(o.rotation.x=A.rotationX,S=!0);
  Number.isFinite(A.modelScale)&&o.scale.x!==A.modelScale&&(o.scale.setScalar(A.modelScale),S=!0);
  typeof A.visible==="boolean"&&V!==A.visible&&(V=A.visible,S=!0);
  let k=A.theme==="light"?"light":"dark",I=0,D=0;
  if(k!==H){
    H=k,n.background=null,d.radius=H==="light"?.04:.18,d.strength=H==="light"?.28:1.15,s.domElement.dataset.bloomRadius=String(d.radius),s.domElement.dataset.bloomStrength=String(d.strength),M.uniforms.lightMode.value=H==="light"?1:0;
    o.traverse(w=>{
      w instanceof Qi&&(I+=1);
      if(!(w instanceof We))return;
      let F=w.material;
      if(F?.blending===ba){w.visible=!0;return}
      P.has(w)||P.set(w,F),w.visible=!0;
      H==="light"?(w.material=O,D+=1):P.has(w)&&(w.material=P.get(w))
    });
    S=!0
  }else{
    o.traverse(w=>{w instanceof Qi&&(I+=1),H==="light"&&w instanceof We&&w.material===O&&(D+=1)})
  }
  s.domElement.dataset.reactorTheme=H,s.domElement.dataset.wireObjects=String(I),s.domElement.dataset.sourceWireObjects=String(o.userData.sourceWireObjects||I),s.domElement.dataset.lightDepthOccluders=String(D),s.domElement.dataset.renderVisible=String(V);
  S&&E();
  window.parent!==window&&window.parent.postMessage({type:"tokamak-state-applied",theme:H,wireObjects:I,depthOccluders:D},"*")
};window.addEventListener("message",C);
let l=o.getObjectByName("energy-ring-front"),c=o.getObjectByName("energy-ring-back"),h=new tp(i,s.domElement);
h.target.copy(Tv),h.enabled=!1,h.update();
let T=Math.atan2(i.position.x,i.position.z);l&&(l.rotation.y=T),c&&(c.rotation.y=T);
let p=new Be(1,1,{type:Ke,samples:Math.min(4,s.capabilities.maxSamples)});
p.texture.name="ReactorScene.MSAA";
let A=new Be(1,1,{type:Ke,depthBuffer:!1}),S=A.clone();
A.texture.name="ReactorPost.A",S.texture.name="ReactorPost.B";
let k=new ap(n,i),d=new Zo(new Q(t.clientWidth,t.clientHeight),1.15,.18,1.05),v=new op,M=new sp;
M.renderToScreen=!0,s.info.autoReset=!1;
s.domElement.dataset.gpuTimer="disabled";
let u={
  setSize(w,F){
    let N=s.getPixelRatio(),L=Math.max(1,Math.round(w*N)),B=Math.max(1,Math.round(F*N));
    p.setSize(L,B),A.setSize(L,B),S.setSize(L,B),k.setSize(L,B),d.setSize(L,B),v.setSize(L,B),M.setSize(L,B)
  },
  render(){
    s.info.reset(),k.renderToScreen=!1,k.render(s,A,p,0,!1),d.renderToScreen=!1,d.render(s,A,p,0,!1),v.renderToScreen=!1,v.render(s,S,A,0,!1),M.renderToScreen=!0,M.render(s,A,S,0,!1)
  },
  dispose(){p.dispose(),A.dispose(),S.dispose(),k.dispose(),d.dispose(),v.dispose(),M.dispose()}
};
s.domElement.dataset.bloomRadius=".18";
s.domElement.dataset.performanceRevision="${revision}";
s.domElement.dataset.renderMode="demand";
s.domElement.dataset.canvasMsaa="0";
s.domElement.dataset.sceneMsaa=String(p.samples);
s.domElement.dataset.postprocessSamples="0";
s.domElement.dataset.postprocessStages="16";
s.domElement.dataset.bloomSampling="paired-bilinear";
s.domElement.dataset.plasmaHalo="theme-aware-light-v2";
s.domElement.dataset.transparentPassMode="single";
let m=()=>{i.position.copy(D1),h.target.copy(Tv),h.update(),E()},f=w=>{w.key.toLowerCase()==="r"&&m()};
window.addEventListener("keydown",f),s.domElement.addEventListener("dblclick",m);
let x=0,G=0,q=!0,K=!1,R=()=>{
  x=0;
  if(!V||document.hidden||!q)return;
  q=!1;
  let w=performance.now();
  u.render(),G+=1;
  s.domElement.dataset.renderFrames=String(G),s.domElement.dataset.renderCalls=String(s.info.render.calls),s.domElement.dataset.renderTriangles=String(s.info.render.triangles),s.domElement.dataset.renderLines=String(s.info.render.lines),s.domElement.dataset.lastFrameMs=(performance.now()-w).toFixed(3),s.domElement.dataset.renderPaused="false"
};
E=()=>{
  q=!0;
  if(!K){s.domElement.dataset.shaderCompilation="pending";return}
  if(!V||document.hidden){
    x&&(cancelAnimationFrame(x),x=0),s.domElement.dataset.renderPaused="true";
    return
  }
  x||(x=requestAnimationFrame(R))
};
let g=()=>{
  let w=t.clientWidth,F=t.clientHeight;
  if(!w||!F)return;
  i.aspect=w/F,i.updateProjectionMatrix(),s.setSize(w,F),u.setSize(w,F),E()
},_=new ResizeObserver(g),W=()=>E();
_.observe(t),document.addEventListener("visibilitychange",W),g();
window.__tokamakDiagnostics=()=>({frames:G,calls:s.info.render.calls,triangles:s.info.render.triangles,lines:s.info.render.lines,visible:V,paused:s.domElement.dataset.renderPaused==="true",sceneSamples:p.samples,postSamples:0});
window.parent!==window&&window.parent.postMessage({type:"tokamak-ready"},"*");
s.compileAsync(n,i).catch(()=>{}).then(()=>{K=!0,s.domElement.dataset.shaderCompilation="complete",E()});
return()=>{x&&cancelAnimationFrame(x),_.disconnect(),document.removeEventListener("visibilitychange",W),window.removeEventListener("message",C),window.removeEventListener("keydown",f),s.domElement.removeEventListener("dblclick",m),h.dispose(),u.dispose(),O.dispose(),new Set(P.values()).forEach(w=>w?.dispose()),s.dispose(),t.removeChild(s.domElement),n.traverse(w=>{if(w instanceof We||w instanceof Qi){w.geometry?.dispose();let F=w.material;Array.isArray(F)?F.forEach(N=>N.dispose()):F?.dispose()}})}},[]),(0,L1.jsx)("div",{ref:e,className:"reactor-scene"})}`,
);

replaceOnce(
  "theme-aware plasma visibility",
  standardPlasmaVisibility,
  themeAwarePlasmaVisibility,
);

fs.writeFileSync(filePath, html);
