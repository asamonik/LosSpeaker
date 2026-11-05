// Rendering stays in the viewing browser. No extra work enters the Pi's audio path.
export type VisualFrame = {
  available: boolean;
  spectrum: number[];
  peak: number[];
  reason?: string;
  label?: string;
  output_peak?: number[];
};
export const scenes = ["Bloom", "Warp", "Terrain"] as const;
export const palettes = {
  Aurora: ["#22edcd", "#7540ff", "#ff537d"],
  Ember: ["#ffcb62", "#ff492d", "#b032e8"],
  Glacier: ["#baffef", "#119de9", "#4051ff"],
} as const;
export type Scene = (typeof scenes)[number];
export type Palette = keyof typeof palettes;
export type VisualInput = {
  frame: VisualFrame;
  received: number;
  playing: boolean;
  scene: Scene;
  palette: Palette;
};

const quadVertex = `attribute vec2 position; void main(){gl_Position=vec4(position,0.,1.);}`;
const backgroundFragment = `
precision highp float;
uniform vec2 resolution;
uniform vec2 pointer;
uniform float time,energy,bass,treble,pulse,scene;
uniform vec3 colorA,colorB,colorC;
uniform sampler2D history;
uniform float head;
float band(float x){return texture2D(history,vec2(clamp(x,.008,.992),(head+.5)/96.)).r;}
float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y);}
float fbm(vec2 p){float f=0.;float a=.55;for(int i=0;i<4;i++){f+=a*noise(p);p=mat2(1.6,1.2,-1.2,1.6)*p+4.7;a*=.48;}return f;}
vec3 palette(float v){return mix(mix(colorA,colorB,smoothstep(.05,.65,v)),colorC,smoothstep(.55,1.,v));}
void main(){
 vec2 uv=gl_FragCoord.xy/resolution;
 float aspect=resolution.x/resolution.y;
 vec2 p=(uv-.5)*vec2(aspect,1.);
 float t=time;
 vec3 col=vec3(.003,.004,.012);
 if(scene<.5){
   // Domain-warped colour volumes, with folds, glowing seams and dark cavities.
   vec2 q=p*2.25+pointer*.12;
   q+=vec2(sin(q.y*3.-t*.2),cos(q.x*2.+t*.15))*band(uv.x)*.22;
   q*=1.-pulse*.075;
   q+=vec2(sin(t*.12),cos(t*.09))*.22;
   vec2 warp=vec2(fbm(q+vec2(t*.08,0)),fbm(q+vec2(4.8,-t*.075)));
   vec2 flow=q+warp*3.2;
   float cloud=fbm(flow+vec2(t*.05,-t*.04));
   float curl=sin(flow.x*2.8+flow.y*1.9+cloud*8.+t*.32);
   float fold=pow(1.-abs(curl),7.);
   float volume=smoothstep(.12,.84,cloud);
   float mask=exp(-dot(p*vec2(.8,1.1),p*vec2(.8,1.1))*.70);
   float hue=clamp(cloud*.8+warp.x*.5+sin(flow.x*.6+t*.12)*.2,0.,1.);
   vec3 body=palette(hue);
   col+=body*volume*mask*(.65+energy*1.8);
   col+=mix(colorA,colorC,warp.y)*fold*mask*(.30+energy*.8+pulse*.8);
   col+=colorB*pow(volume,4.)*mask*1.2;
   // Very fine folds give the fluid its silk-like texture rather than flat gradients.
   float filament=pow(.5+.5*sin(curl*13.+cloud*37.),18.);
   col+=body*filament*volume*mask*.20;
   col*=.50+.60*smoothstep(-.8,.6,curl);
   col+=colorA*.04*exp(-length(p+vec2(.3,.1))*3.);
 }else if(scene<1.5){
   // The point-cloud tunnel gets a luminous accretion core and wisps behind it.
   vec2 q=p-pointer*.025;
   float r=length(q),a=atan(q.y,q.x);
   float swirl=fbm(vec2(cos(a),sin(a))*(r*4.+1.)+vec2(t*.15,-t*.22));
   float rim=exp(-abs(r-(.085+bass*.025+swirl*.025))*75.);
   col+=palette(.5+.4*sin(a+t*.1))*rim*(.12+energy*.5);
   col+=colorB*exp(-r*4.)*(.045+energy*.10);
   col+=colorA*exp(-r*r*600.)*(.2+pulse*.6);
   col+=palette(swirl)*pow(swirl,4.)*exp(-r*3.)*.14;
   float arms=pow(.5+.5*sin(a*5.+log(r+.035)*8.-t*.65),10.);
   col+=palette(.5+.5*sin(r*3.-a+t*.1))*arms*exp(-r*1.1)*smoothstep(.06,.22,r)*(.20+energy*.55+band(abs(a)/3.141593)*.6);
   col+=colorB*pow(arms,.25)*exp(-r*1.6)*smoothstep(.08,.3,r)*.07;
 }else{
   float sky=exp(-abs(p.y-.10)*7.);
   col+=colorB*sky*.07+colorC*sky*.022;
   float moon=length((p-vec2(.32,.23))*vec2(1.,1.));
   col+=colorA*exp(-moon*moon*420.)*.04;
   vec2 cells=floor(gl_FragCoord.xy/2.);
   col+=step(.9997,hash(cells))*.20*(.5+treble);
 }
 col*=1.-smoothstep(.28,.95,length(uv-.5))*.65;
 col=1.-exp(-col*1.8);
 col+=(hash(gl_FragCoord.xy)-.5)*.009;
 gl_FragColor=vec4(col,1.);
}`;

const starsVertex = `
precision highp float;
attribute vec4 seed;
uniform vec2 resolution,pointer;
uniform float time,travel,energy,bass,pulse;
uniform vec3 colorA,colorB,colorC;
varying vec3 tint;
varying float alpha,angle,stretch;
void main(){
 float depth=fract(seed.z-travel*(.085+seed.w*.035));
 float z=.24+depth*18.;
 float a=floor(seed.x*5.)*1.256637+seed.w*.75+z*.48+time*.15;
 float radius=1.05+pow(seed.y,1.7)*5.2;
 radius*=1.+bass*.16+pulse*.13;
 vec2 p=vec2(cos(a),sin(a))*radius;
 p+=vec2(sin(z*.23+time*.08),cos(z*.17+time*.1))*.24;
 p-=pointer*.24;
 vec2 projected=p*1.50/z;
 projected.x/=resolution.x/resolution.y;
 gl_Position=vec4(projected,0.,1.);
 float size=(3.+seed.w*8.)*resolution.y/850./max(.4,z*.22);
 gl_PointSize=clamp(size*(1.+pulse*.8),1.,75.);
 float hue=fract(seed.x+seed.z*.2+time*.014);
 tint=mix(mix(colorA,colorB,smoothstep(0.,.65,hue)),colorC,smoothstep(.55,1.,hue))*1.8;
 alpha=(1.-smoothstep(.65,1.,depth))*smoothstep(0.,.055,depth)*(.65+energy*1.6);
 angle=atan(projected.y,projected.x);
 stretch=1.+(1.-depth)*3.5+pulse*2.;
}`;
const starsFragment = `
precision mediump float;
varying vec3 tint;
varying float alpha,angle,stretch;
void main(){
 vec2 p=gl_PointCoord-.5;
 vec2 q=vec2(dot(p,vec2(cos(angle),sin(angle))),dot(p,vec2(-sin(angle),cos(angle))));
 float d=length(vec2(q.x/stretch,q.y*1.8));
 float glow=exp(-d*18.)*.5+exp(-d*d*450.);
 float core=exp(-d*d*1300.);
 gl_FragColor=vec4(tint*glow+vec3(core*.5),alpha*glow);
}`;

const terrainVertex = `
precision highp float;
attribute vec2 grid;
uniform sampler2D history;
uniform float head,historyFraction,energy,bass,pulse,time;
uniform vec2 resolution,pointer;
uniform vec3 colorA,colorB,colorC;
varying vec3 tint;
varying float fog,height;
varying vec2 coordinates;
void main(){
 float age=max(0.,grid.y*95.-historyFraction);
 float row=mod(head-age+192.,96.);
 float level=texture2D(history,vec2(grid.x,(row+.5)/96.)).r;
 height=pow(level,2.)*4.8;
 coordinates=grid*vec2(63.,95.);
 float x=(grid.x-.5)*13.;
 float z=grid.y*23.+2.;
 float y=height-1.65;
 x+=sin(grid.y*2.5+time*.08)*.3+pointer.x*.15;
 float viewY=y*.93+z*.367;
 float depth=z*.93-y*.367;
 float aspect=resolution.x/resolution.y;
 gl_Position=vec4(x*1.5/aspect,viewY*1.5-depth*.38,depth*(depth/30.*2.-1.),depth);
 tint=mix(mix(colorA,colorB,smoothstep(.05,.7,grid.x)),colorC,smoothstep(.5,1.,grid.x));
 tint=mix(tint,vec3(.8,.95,1.),smoothstep(1.8,4.0,height)*.6);
 fog=exp(-grid.y*3.);
 gl_PointSize=2.;
}`;
const terrainFragment = `
#extension GL_OES_standard_derivatives : enable
precision highp float;
uniform float wire;
varying vec3 tint;
varying float fog,height;
varying vec2 coordinates;
void main(){
 vec3 color=mix(vec3(.006,.009,.023),tint*(.04+height*.025),fog);
 vec2 gridDistance=abs(fract(coordinates-.5)-.5)/max(fwidth(coordinates),vec2(.001));
 float line=1.-min(min(gridDistance.x,gridDistance.y),1.);
 float glow=1.-min(min(gridDistance.x,gridDistance.y)*.18,1.);
 color+=tint*(line*(.65+height*.65)+glow*.10)*fog;
 gl_FragColor=vec4(1.-exp(-color*1.5),1.);
}`;

function rgb(hex: string) {
  return [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
}
function random(seed: number) {
  let value = seed;
  return () => { value = (Math.imul(value, 1664525) + 1013904223) >>> 0; return value / 4294967296; };
}

export function startVisualizer(canvas: HTMLCanvasElement, input: () => VisualInput) {
  const gl = canvas.getContext("webgl", { alpha: false, antialias: true, depth: true, powerPreference: "high-performance" });
  if (!gl) return startFallback(canvas, input);
  const shaders: WebGLShader[] = [], programs: WebGLProgram[] = [], buffers: WebGLBuffer[] = [], textures: WebGLTexture[] = [];
  const dispose = () => {
    buffers.forEach(buffer => gl.deleteBuffer(buffer)); textures.forEach(texture => gl.deleteTexture(texture));
    programs.forEach(program => gl.deleteProgram(program)); shaders.forEach(shader => gl.deleteShader(shader));
  };
  function program(vertex: string, fragment: string) {
    const p = gl!.createProgram()!; programs.push(p);
    for (const [type, source] of [[gl!.VERTEX_SHADER, vertex], [gl!.FRAGMENT_SHADER, fragment]] as const) {
      const shader = gl!.createShader(type)!; shaders.push(shader); gl!.shaderSource(shader, source); gl!.compileShader(shader);
      if (!gl!.getShaderParameter(shader, gl!.COMPILE_STATUS)) throw Error(gl!.getShaderInfoLog(shader) || "Shader unavailable");
      gl!.attachShader(p, shader);
    }
    gl!.linkProgram(p);
    if (!gl!.getProgramParameter(p, gl!.LINK_STATUS)) throw Error("Visualizer program unavailable");
    const names = ["resolution", "pointer", "time", "travel", "energy", "bass", "treble", "pulse", "scene", "colorA", "colorB", "colorC", "history", "head", "historyFraction", "wire"];
    return { p, u: Object.fromEntries(names.map(name => [name, gl!.getUniformLocation(p, name)])) };
  }
  let background: ReturnType<typeof program>, stars: ReturnType<typeof program>, terrain: ReturnType<typeof program>;
  try {
    if (!gl.getExtension("OES_standard_derivatives")) throw Error("Surface derivatives unavailable");
    background = program(quadVertex, backgroundFragment);
    stars = program(starsVertex, starsFragment);
    terrain = program(terrainVertex, terrainFragment);
    if (gl.getParameter(gl.MAX_VERTEX_TEXTURE_IMAGE_UNITS) < 1) throw Error("Vertex textures unavailable");
  } catch {
    dispose();
    const replacement = canvas.cloneNode(false) as HTMLCanvasElement;
    canvas.insertAdjacentElement("afterend", replacement); canvas.hidden = true;
    const stop = startFallback(replacement, input);
    return () => { stop(); replacement.remove(); canvas.hidden = false; };
  }
  function buffer(values: Float32Array | Uint16Array, index = false) {
    const b = gl!.createBuffer()!; buffers.push(b);
    gl!.bindBuffer(index ? gl!.ELEMENT_ARRAY_BUFFER : gl!.ARRAY_BUFFER, b);
    gl!.bufferData(index ? gl!.ELEMENT_ARRAY_BUFFER : gl!.ARRAY_BUFFER, values, gl!.STATIC_DRAW);
    return b;
  }
  const quad = buffer(new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]));
  const rand = random(71993), count = 28000;
  const seeds = new Float32Array(count * 4);
  for (let i = 0; i < seeds.length; i++) seeds[i] = rand();
  const particles = buffer(seeds);
  const grid = new Float32Array(64 * 96 * 2), triangles: number[] = [];
  for (let y = 0; y < 96; y++) for (let x = 0; x < 64; x++) {
    const i = y * 64 + x; grid[i * 2] = x / 63; grid[i * 2 + 1] = y / 95;
    if (x < 63 && y < 95) triangles.push(i, i + 64, i + 1, i + 1, i + 64, i + 65);
  }
  const terrainGrid = buffer(grid), terrainFaces = buffer(new Uint16Array(triangles), true);
  const history = gl.createTexture()!; textures.push(history);
  gl.bindTexture(gl.TEXTURE_2D, history);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, 64, 96, 0, gl.LUMINANCE, gl.UNSIGNED_BYTE, new Uint8Array(64 * 96));
  const bytes = new Uint8Array(64), smooth = new Float32Array(64);
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  let request = 0, last = 0, elapsed = 0, travel = 0, slow = 0, lost = false;
  let head = 0, lastHistory = 0, beat = 0, average = 0, quality = Math.min(devicePixelRatio || 1, 1.25);
  let pointerX = 0, pointerY = 0, targetX = 0, targetY = 0;
  const pointer = (event: PointerEvent) => {
    targetX = event.clientX / Math.max(1, canvas.clientWidth) * 2 - 1;
    targetY = 1 - event.clientY / Math.max(1, canvas.clientHeight) * 2;
  };
  const attributes = (p: WebGLProgram, b: WebGLBuffer, name: string, size: number) => {
    // Each program has one attribute. Disable leftovers when changing programs.
    for (let i = 0; i < 4; i++) gl.disableVertexAttribArray(i);
    gl.bindBuffer(gl.ARRAY_BUFFER, b);
    const location = gl.getAttribLocation(p, name);
    gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, size, gl.FLOAT, false, 0, 0);
  };
  const draw = (now: number) => {
    request = 0;
    if (document.hidden || lost) return;
    const dt = Math.min((now - (last || now)) / 1000, .1); last = now;
    const data = input();
    const live = data.frame.available && data.playing && now - data.received < 3000;
    let energy = 0, bass = 0, treble = 0;
    for (let i = 0; i < 64; i++) {
      const target = live ? Math.max(0, Math.min(1, data.frame.spectrum[i] || 0)) : 0;
      smooth[i] += (target - smooth[i]) * (1 - Math.exp(-dt * (target > smooth[i] ? 140 : 16)));
      bytes[i] = Math.round(smooth[i] * 255); energy += smooth[i] / 64;
      if (i < 18) bass += smooth[i] / 18;
      if (i > 47) treble += smooth[i] / 16;
    }
    average += (bass - average) * (1 - Math.exp(-dt * 1.6));
    beat = Math.max(Math.min(1, Math.max(0, bass - average - .018) * 7), beat * Math.exp(-dt * 4.8));
    if (!motion.matches) { elapsed += dt * (.7 + energy * .4); travel += dt * (.7 + energy * .65 + beat * 2.5); }
    const pulse = motion.matches ? 0 : beat;
    pointerX += ((motion.matches ? 0 : targetX) - pointerX) * (1 - Math.exp(-dt * 3));
    pointerY += ((motion.matches ? 0 : targetY) - pointerY) * (1 - Math.exp(-dt * 3));
    const budget = data.scene === "Bloom" ? 1_100_000 : 1_900_000;
    const scale = Math.min(quality, Math.sqrt(budget / Math.max(1, canvas.clientWidth * canvas.clientHeight)));
    const width = Math.max(1, Math.round(canvas.clientWidth * scale)), height = Math.max(1, Math.round(canvas.clientHeight * scale));
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    gl.viewport(0, 0, width, height);
    gl.bindTexture(gl.TEXTURE_2D, history);
    if (now - lastHistory >= 1000 / 30) {
      head = (head + 1) % 96; lastHistory = now;
    }
    // Upload the live row every draw; history scrolling must never delay the beat.
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, head, 64, 1, gl.LUMINANCE, gl.UNSIGNED_BYTE, bytes);
    const use = ({ p, u }: ReturnType<typeof program>) => {
      gl.useProgram(p); gl.uniform2f(u.resolution, width, height); gl.uniform2f(u.pointer, pointerX, pointerY);
      gl.uniform1f(u.time, elapsed); gl.uniform1f(u.travel, travel); gl.uniform1f(u.energy, energy);
      gl.uniform1f(u.bass, bass); gl.uniform1f(u.treble, treble); gl.uniform1f(u.pulse, pulse);
      gl.uniform1f(u.scene, scenes.indexOf(data.scene));
      gl.uniform1i(u.history, 0); gl.uniform1f(u.head, head);
      palettes[data.palette].forEach((color, i) => gl.uniform3fv(u[["colorA", "colorB", "colorC"][i]], rgb(color)));
    };
    gl.disable(gl.DEPTH_TEST); gl.disable(gl.BLEND);
    gl.clearColor(0,0,0,1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    use(background); attributes(background.p, quad, "position", 2); gl.drawArrays(gl.TRIANGLES, 0, 6);
    if (data.scene === "Warp") {
      use(stars); attributes(stars.p, particles, "seed", 4);
      gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
      gl.drawArrays(gl.POINTS, 0, count); gl.disable(gl.BLEND);
    } else if (data.scene === "Terrain") {
      use(terrain); attributes(terrain.p, terrainGrid, "grid", 2);
      gl.uniform1i(terrain.u.history, 0); gl.uniform1f(terrain.u.head, head);
      gl.uniform1f(terrain.u.historyFraction, Math.min(1, (now - lastHistory) / (1000 / 30)));
      gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL);
      gl.uniform1f(terrain.u.wire, 0); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, terrainFaces);
      gl.drawElements(gl.TRIANGLES, triangles.length, gl.UNSIGNED_SHORT, 0);
      gl.disable(gl.DEPTH_TEST);
    }
    if (dt > .030) slow++; else slow = Math.max(0, slow - 1);
    if (slow > 45 && quality > .4) { quality = Math.max(.4, quality * .78); slow = 0; }
    request = requestAnimationFrame(draw);
  };
  const resume = () => { if (!document.hidden && !lost && !request) { last = 0; request = requestAnimationFrame(draw); } };
  const contextLost = (event: Event) => { event.preventDefault(); lost = true; cancelAnimationFrame(request); request = 0; };
  const restored = () => canvas.dispatchEvent(new Event("visualizer-restore"));
  canvas.addEventListener("webglcontextlost", contextLost); canvas.addEventListener("webglcontextrestored", restored);
  canvas.parentElement?.addEventListener("pointermove", pointer);
  document.addEventListener("visibilitychange", resume); resume();
  return () => {
    cancelAnimationFrame(request); dispose();
    canvas.removeEventListener("webglcontextlost", contextLost); canvas.removeEventListener("webglcontextrestored", restored);
    canvas.parentElement?.removeEventListener("pointermove", pointer); document.removeEventListener("visibilitychange", resume);
  };
}

function startFallback(canvas: HTMLCanvasElement, input: () => VisualInput) {
  const ctx = canvas.getContext("2d", { alpha: false });
  if (!ctx) return () => {};
  const rand = random(337), particles = Array.from({ length: 700 }, () => [rand(), rand(), rand(), rand()]);
  const smooth = new Float32Array(64), history: Float32Array[] = [];
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  let request = 0, last = 0, elapsed = 0, lastHistory = 0;
  const draw = (now: number) => {
    request = 0; if (document.hidden) return;
    const dt = Math.min((now - (last || now)) / 1000, .1); last = now;
    const data = input(), live = data.frame.available && data.playing && now - data.received < 3000;
    for (let i = 0; i < 64; i++) smooth[i] += ((live ? data.frame.spectrum[i] || 0 : 0) - smooth[i]) * (1 - Math.exp(-dt * ((live ? data.frame.spectrum[i] || 0 : 0) > smooth[i] ? 140 : 16)));
    const energy = smooth.reduce((a, b) => a + b, 0) / 64;
    if (!motion.matches) elapsed += dt * (.7 + energy);
    const w = Math.round(canvas.clientWidth), h = Math.round(canvas.clientHeight);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    const colors = palettes[data.palette];
    ctx.globalCompositeOperation = "source-over"; ctx.fillStyle = "#03050b"; ctx.fillRect(0, 0, w, h);
    ctx.globalCompositeOperation = "screen";
    if (data.scene === "Bloom") {
      for (let i = 0; i < 13; i++) {
        const x = w * (.5 + Math.sin(i * 2.4 + elapsed * .13) * .32);
        const y = h * (.48 + Math.cos(i * 1.3 - elapsed * .12) * .26);
        const radius = Math.min(w, h) * (.2 + energy * .2);
        const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
        gradient.addColorStop(0, colors[i % 3] + "55"); gradient.addColorStop(.45, colors[i % 3] + "25"); gradient.addColorStop(1, colors[i % 3] + "00");
        ctx.fillStyle = gradient; ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2);
      }
    } else if (data.scene === "Warp") {
      for (const [a, radius, z, color] of particles) {
        const depth = ((z - elapsed * .10) % 1 + 1) % 1;
        const angle = a * Math.PI * 2 + depth * 5 + elapsed * .08;
        const r = (.08 + radius * .3) * Math.min(w, h) / Math.max(.07, depth);
        const x = w / 2 + Math.cos(angle) * r, y = h / 2 + Math.sin(angle) * r;
        const length = 2 + (1 - depth) * (4 + energy * 12);
        ctx.strokeStyle = colors[Math.floor(color * 3)] + "bb"; ctx.lineWidth = .7 + (1 - depth) * 1.5;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + Math.cos(angle) * length, y + Math.sin(angle) * length); ctx.stroke();
      }
    } else {
      if (now - lastHistory > 1000 / 30) { history.unshift(smooth.slice()); if (history.length > 45) history.pop(); lastHistory = now; }
      for (let y = history.length - 1; y >= 0; y--) {
        const depth = 1 + y * .12, width = w * 1.5 / depth;
        ctx.strokeStyle = colors[y % 3]; ctx.globalAlpha = Math.exp(-y * .055); ctx.lineWidth = 1;
        ctx.beginPath();
        for (let i = 0; i < 64; i++) {
          const x = w / 2 + (i / 63 - .5) * width;
          const py = h * .4 + h * .42 / depth - history[y][i] ** 2 * h * .4 / depth;
          if (i === 0) ctx.moveTo(x, py); else ctx.lineTo(x, py);
        }
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1; ctx.globalCompositeOperation = "source-over";
    request = requestAnimationFrame(draw);
  };
  const resume = () => { if (!document.hidden && !request) { last = 0; request = requestAnimationFrame(draw); } };
  document.addEventListener("visibilitychange", resume); resume();
  return () => { cancelAnimationFrame(request); document.removeEventListener("visibilitychange", resume); };
}
