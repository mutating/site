import fs from "node:fs";

const filePath = new URL("../public/tokamak-reactor.html", import.meta.url);
let html = fs.readFileSync(filePath, "utf8");

function upgrade(label, finalValue, previousValues) {
  if (html.includes(finalValue)) return;

  for (const previousValue of previousValues) {
    const matches = html.split(previousValue).length - 1;
    if (matches === 1) {
      html = html.replace(previousValue, finalValue);
      return;
    }
  }

  throw new Error(`${label}: no supported source variant found`);
}

upgrade(
  "embedded viewport sizing",
  `html, body, #root, .scene-shell, .reactor-scene {
  width: 100%;
  height: 100%;
  min-height: 0 !important;
  background: transparent !important;
}`,
  [
    "html, body, .scene-shell, .reactor-scene { background: transparent !important; }",
  ],
);

upgrade(
  "initial scene background",
  "let n=new dc;n.background=new Bt(0);",
  ["let n=new dc;n.background=null;"],
);

upgrade(
  "theme-aware model state",
  'let H="",C=T=>{let A=T.data;if(A&&A.type==="tokamak-state"){Number.isFinite(A.rotationX)&&(o.rotation.x=A.rotationX),Number.isFinite(A.modelScale)&&o.scale.setScalar(A.modelScale);let S=A.theme==="light"?"light":"dark";S!==H&&(H=S,n.background=null,M.uniforms.lightMode.value=H==="light"?1:0,o.traverse(w=>{w instanceof We&&(w.visible=H!=="light"||w.material?.blending===ba)})),window.parent!==window&&window.parent.postMessage({type:"tokamak-state-applied"},"*")}};',
  [
    'let H="",C=T=>{let A=T.data;if(A&&A.type==="tokamak-state"){Number.isFinite(A.rotationX)&&(o.rotation.x=A.rotationX),Number.isFinite(A.cameraScale)&&(i.position.copy(Tv).addScaledVector(D1.clone().sub(Tv),A.cameraScale),h.target.copy(Tv),h.update());let S=A.theme==="light"?"light":"dark";S!==H&&(H=S,n.background=null,M.uniforms.lightMode.value=H==="light"?1:0,o.traverse(w=>{w instanceof We&&(w.visible=H!=="light"||w.material?.blending===ba)})),window.parent!==window&&window.parent.postMessage({type:"tokamak-state-applied"},"*")}};',
    'let H="",C=T=>{let A=T.data;if(A&&A.type==="tokamak-state"){Number.isFinite(A.rotationX)&&(o.rotation.x=A.rotationX);let S=A.theme==="light"?"light":"dark";S!==H&&(H=S,n.background=null,M.uniforms.lightMode.value=H==="light"?1:0,o.traverse(w=>{w instanceof We&&(w.visible=H!=="light"||w.material?.blending===ba)})),window.parent!==window&&window.parent.postMessage({type:"tokamak-state-applied"},"*")}};',
    'let H="dark",C=T=>{let A=T.data;if(A&&A.type==="tokamak-state"){Number.isFinite(A.rotationX)&&(o.rotation.x=A.rotationX);let S=A.theme==="light"?"light":"dark";S!==H&&(H=S,n.background=H==="light"?null:new Bt(0),M.uniforms.lightMode.value=H==="light"?1:0,o.traverse(w=>{w instanceof We&&(w.visible=H!=="light"||w.material?.blending===ba)})),window.parent!==window&&window.parent.postMessage({type:"tokamak-state-applied"},"*")}};',
    'let C=T=>{let A=T.data;A&&A.type==="tokamak-state"&&Number.isFinite(A.rotationX)&&(o.rotation.x=A.rotationX)};',
    'let H="dark",C=T=>{let A=T.data;if(A&&A.type==="tokamak-state"){Number.isFinite(A.rotationX)&&(o.rotation.x=A.rotationX);let S=A.theme==="light"?"light":"dark";S!==H&&(H=S,n.background=new Bt(H==="light"?16775931:0),o.traverse(w=>{w instanceof We&&(w.visible=H!=="light"||w.material?.blending===ba)})),window.parent!==window&&window.parent.postMessage({type:"tokamak-state-applied"},"*")}};',
  ],
);

upgrade(
  "output shader theme uniform",
  'var su={name:"OutputShader",uniforms:{tDiffuse:{value:null},toneMappingExposure:{value:1},lightMode:{value:0}},vertexShader:`',
  [
    'var su={name:"OutputShader",uniforms:{tDiffuse:{value:null},toneMappingExposure:{value:1}},vertexShader:`',
  ],
);

upgrade(
  "output shader uniform declaration",
  `uniform sampler2D tDiffuse;
		uniform float lightMode;

		#include <tonemapping_pars_fragment>`,
  [
    `uniform sampler2D tDiffuse;

		#include <tonemapping_pars_fragment>`,
  ],
);

upgrade(
  "output shader light background composite",
  `			#endif

			if ( lightMode > 0.5 ) {

				vec3 pageBackground = vec3( 1.0, 0.9803921569, 0.9843137255 );
				float energy = max( max( gl_FragColor.r, gl_FragColor.g ), gl_FragColor.b );
				float coverage = clamp( gl_FragColor.a, 0.0, 1.0 );
				vec3 composedColor = mix( pageBackground, gl_FragColor.rgb, coverage );
				float emission = clamp( ( energy - coverage ) * 0.25, 0.0, 0.18 );
				vec3 emissionColor = energy > 0.00001 ? gl_FragColor.rgb / energy : pageBackground;
				gl_FragColor = vec4( mix( composedColor, emissionColor, emission ), 1.0 );

			} else {

				vec3 pageBackground = vec3( 0.0352941176, 0.0235294118, 0.0313725490 );
				float coverage = clamp( gl_FragColor.a, 0.0, 1.0 );
				gl_FragColor = vec4( clamp( pageBackground * ( 1.0 - coverage ) + gl_FragColor.rgb, 0.0, 1.0 ), 1.0 );

			}

		}\`};var sp=class`,
  [
    `			#endif

			if ( lightMode > 0.5 ) {

				vec3 pageBackground = vec3( 1.0, 0.9803921569, 0.9843137255 );
				float energy = max( max( gl_FragColor.r, gl_FragColor.g ), gl_FragColor.b );
				float coverage = clamp( gl_FragColor.a, 0.0, 1.0 );
				vec3 composedColor = mix( pageBackground, gl_FragColor.rgb, coverage );
				float emission = clamp( ( energy - coverage ) * 0.25, 0.0, 0.18 );
				vec3 emissionColor = energy > 0.00001 ? gl_FragColor.rgb / energy : pageBackground;
				gl_FragColor = vec4( mix( composedColor, emissionColor, emission ), 1.0 );

			}

		}\`};var sp=class`,
    `			#endif

			if ( lightMode > 0.5 ) {

				vec3 pageBackground = vec3( 1.0, 0.9803921569, 0.9843137255 );
				float energy = max( max( gl_FragColor.r, gl_FragColor.g ), gl_FragColor.b );
				float coverage = clamp( max( gl_FragColor.a, energy ), 0.0, 1.0 );
				vec3 wireColor = energy > 0.00001 ? gl_FragColor.rgb / energy : pageBackground;
				gl_FragColor = vec4( mix( pageBackground, wireColor, coverage ), 1.0 );

			}

		}\`};var sp=class`,
    `			#endif

			if ( lightMode > 0.5 ) {

				vec3 pageBackground = vec3( 1.0, 0.9803921569, 0.9843137255 );
				float coverage = clamp( gl_FragColor.a, 0.0, 1.0 );
				gl_FragColor = vec4( mix( pageBackground, gl_FragColor.rgb, coverage ), 1.0 );

			}

		}\`};var sp=class`,
    `			#endif

		}\`};var sp=class`,
  ],
);

upgrade(
  "responsive renderer size",
  "let g=()=>{let T=t.clientWidth,A=t.clientHeight;i.aspect=T/A,i.updateProjectionMatrix(),s.setSize(T,A),u.setSize(T,A)}",
  [
    "let g=()=>{let T=t.clientWidth,A=t.clientHeight,S=1/(1+Math.max(0,600-A)*.00125);o.scale.setScalar(S),i.aspect=T/A,i.updateProjectionMatrix(),s.setSize(T,A),u.setSize(T,A)}",
    "let g=()=>{let T=t.clientWidth,A=t.clientHeight,S=1+Math.max(0,600-A)*.00125;i.position.copy(Tv).addScaledVector(D1.clone().sub(Tv),S),i.aspect=T/A,i.updateProjectionMatrix(),s.setSize(T,A),u.setSize(T,A)}",
  ],
);

fs.writeFileSync(filePath, html);
