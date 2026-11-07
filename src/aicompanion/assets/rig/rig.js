/* AICompanion 分层网格骨骼渲染器与动作引擎（本项目自行实现）。
 *
 * 模型：AICompanionRig v1（见 aicompanion/body/rig_import.py）。所有坐标在同一个"骨骼空间"：
 * 角色中线 x=128，地面 y=256。每个部件是一张贴图，铺在一个矩形上并切成网格；每帧把网格的静止顶点
 * 依次送进它所属的变形器链（先子后父）：
 *   rot  ：绕支点旋转 / 缩放 / 平移
 *   warp ：矩形内的位移场，按 fn 分为 sway（头发）、skirt（裙摆）、hinge（腿）、bend（尾巴）、parallax（头部视差）
 * 动作引擎把宿主给的状态（姿势、心情、口型、拖动、走路、视线）转成各变形器参数，大量用阻尼弹簧，
 * 所以动作之间没有"切换"，只有连续的过渡。
 */
(function () {
  'use strict';
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
  const rad = d => d * Math.PI / 180;

  class Spring {
    constructor(k, c, lim = Infinity) { this.k = k; this.c = c; this.lim = lim; this.x = 0; this.v = 0; }
    step(target, dt) {
      if (!Number.isFinite(target)) target = 0;
      const n = Math.max(1, Math.ceil(dt / 0.008)), h = dt / n;
      for (let i = 0; i < n; i++) { this.v += (this.k * (target - this.x) - this.c * this.v) * h; this.x += this.v * h; }
      if (Math.abs(this.x) > this.lim) { this.x = Math.sign(this.x) * this.lim; this.v *= -0.3; }
      return this.x;
    }
    kick(v) { this.v += v; }
  }

  function readJSON(url) {
    // Qt 本地页面用 XHR 读 file URL，避免 fetch 对本地文件的限制。
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest(); request.open('GET', url);
      request.onload = () => {
        try {
          if (request.status !== 0 && request.status !== 200) throw new Error('读取失败 ' + url);
          resolve(JSON.parse(request.responseText));
        } catch (error) { reject(error); }
      };
      request.onerror = () => reject(new Error('读取失败 ' + url)); request.send();
    });
  }

  /* ---------- 变形器 ---------- */
  function makeDeformers(defs) {
    const params = {};
    for (const [name, d] of Object.entries(defs)) {
      params[name] = d.kind === 'rot' ? { a: 0, sx: 1, sy: 1, tx: 0, ty: 0 } : { dx: 0, dy: 0, a: 0, ty: 0, flare: 0 };
    }
    const chains = {};
    for (const name of Object.keys(defs)) {
      const chain = []; let d = name; const seen = new Set();
      while (d && defs[d] && !seen.has(d)) { seen.add(d); chain.push(d); d = defs[d].parent; }
      chains[name] = chain;
    }
    const warp = {
      // 头发：越往下摆得越多，带一点下垂
      sway(p, d, u, v) { const w = Math.pow(smooth(0, 1, v), 1.3); return [p.dx * w, p.dy * w + Math.abs(p.dx) * w * 0.08]; },
      // 裙摆：整体随身体摆动，下沿外扩（跳跃落地时 flare）
      skirt(p, d, u, v) { const w = smooth(0.05, 1, v); return [p.dx * w + p.flare * (u - 0.5) * 2 * w, -Math.abs(p.flare) * 0.25 * w]; },
      // 腿：髋部以下按刚体绕髋转，往上渐弱，灯笼裤顶部留在裙子里
      hinge(p, d, u, v, x, y) {
        const [px, py] = d.pivot, w = smooth(py - 6, py + 14, y);
        const c = Math.cos(rad(p.a)), s = Math.sin(rad(p.a)), lx = x - px, ly = y - py;
        return [(lx * c - ly * s - lx) * w, ((lx * s + ly * c - ly) - p.ty) * w];
      },
      // 尾巴：根在右端（u=1），越往尾梢弯得越多
      bend(p, d, u, v, x, y) {
        const k = Math.pow(1 - u, 1.6), [x0, y0, x1] = d.rect, len = x1 - x0;
        return [-Math.abs(p.a) * 0.004 * len * k, p.a * 0.012 * len * k + p.dy * k];
      },
      // 头部视差：前层（五官、刘海）比后层（后发）移动更多，制造转头的立体感；向矩形边缘衰减
      parallax(p, d, u, v) {
        const edge = smooth(0, 0.18, u) * smooth(0, 0.18, 1 - u) * smooth(0, 0.12, v) * smooth(0, 0.25, 1 - v);
        return [p.dx * d.depth * edge, p.dy * d.depth * edge];
      },
    };
    function apply(name, x, y) {
      for (const key of chains[name]) {
        const d = defs[key], p = params[key];
        if (d.kind === 'rot') {
          const [px, py] = d.pivot, c = Math.cos(rad(p.a)), s = Math.sin(rad(p.a));
          const lx = (x - px) * p.sx, ly = (y - py) * p.sy;
          x = px + p.tx + lx * c - ly * s; y = py + p.ty + lx * s + ly * c;
        } else {
          const [x0, y0, x1, y1] = d.rect;
          const u = clamp((x - x0) / (x1 - x0), 0, 1), v = clamp((y - y0) / (y1 - y0), 0, 1);
          const [dx, dy] = warp[d.fn] ? warp[d.fn](p, d, u, v, x, y) : [0, 0];
          x += dx; y += dy;
        }
      }
      return [x, y];
    }
    return { params, apply };
  }

  /* ---------- 网格部件 ---------- */
  function gridMesh(texture, rect, grid, deformer) {
    const [x0, y0, x1, y1] = rect, [nx, ny] = grid;
    const rest = [], uvs = [], indices = [];
    for (let j = 0; j <= ny; j++) for (let i = 0; i <= nx; i++) {
      rest.push(x0 + (x1 - x0) * i / nx, y0 + (y1 - y0) * j / ny); uvs.push(i / nx, j / ny);
    }
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const a = j * (nx + 1) + i, b = a + 1, c = a + nx + 1, d = c + 1;
      indices.push(a, b, c, b, d, c);
    }
    const mesh = new PIXI.SimpleMesh(texture, new Float32Array(rest), new Float32Array(uvs), new Uint16Array(indices), PIXI.DRAW_MODES.TRIANGLES);
    mesh.rest = new Float32Array(rest); mesh.deformer = deformer; mesh.autoUpdate = true;
    return mesh;
  }

  /* ---------- 命中测试（纯 CPU，不读显卡，避免渲染卡顿） ---------- */
  function alphaOf(texture) {
    // 把贴图的 alpha 通道读到内存；本地文件被视为跨源而读不到时返回 null（退回按网格形状判断）。
    try {
      const img = texture.baseTexture.resource.source, w = img.naturalWidth || img.width, h = img.naturalHeight || img.height;
      const canvas = document.createElement('canvas'); canvas.width = w; canvas.height = h;
      const ctx = canvas.getContext('2d', { willReadFrequently: true }); ctx.drawImage(img, 0, 0);
      const rgba = ctx.getImageData(0, 0, w, h).data, alpha = new Uint8Array(w * h);
      for (let i = 0; i < alpha.length; i++) alpha[i] = rgba[i * 4 + 3];
      canvas.width = canvas.height = 0;   // 立即释放临时画布
      return { w, h, alpha };
    } catch (error) { return null; }
  }
  function meshHit(mesh, x, y) {
    const v = mesh.vertices, uv = mesh.uvBuffer ? mesh.uvBuffer.data : mesh.geometry.getBuffer('aTextureCoord').data, idx = mesh.geometry.getIndex().data;
    const a = mesh.texture.alpha;
    for (let i = 0; i < idx.length; i += 3) {
      const i0 = idx[i] * 2, i1 = idx[i + 1] * 2, i2 = idx[i + 2] * 2;
      const x0 = v[i0], y0 = v[i0 + 1], x1 = v[i1], y1 = v[i1 + 1], x2 = v[i2], y2 = v[i2 + 1];
      const d = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2); if (Math.abs(d) < 1e-9) continue;
      const l0 = ((y1 - y2) * (x - x2) + (x2 - x1) * (y - y2)) / d, l1 = ((y2 - y0) * (x - x2) + (x0 - x2) * (y - y2)) / d, l2 = 1 - l0 - l1;
      if (l0 < -0.001 || l1 < -0.001 || l2 < -0.001) continue;
      if (!a) return true;
      const u = l0 * uv[i0] + l1 * uv[i1] + l2 * uv[i2], w = l0 * uv[i0 + 1] + l1 * uv[i1 + 1] + l2 * uv[i2 + 1];
      const px = clamp(Math.floor(u * a.w), 0, a.w - 1), py = clamp(Math.floor(w * a.h), 0, a.h - 1);
      return a.alpha[py * a.w + px] > 40;
    }
    return false;
  }

  /* ---------- 表情 ---------- */
  const EXPRESSIONS = ['neutral', 'happy', 'love', 'surprised', 'sleep', 'drag', 'dizzy', 'shy', 'smug'];   // shy / smug 只有自己的形象包才有贴片
  const POSE_FACE = { idle: 'neutral', talking: 'neutral', thinking: 'neutral', working: 'neutral', celebrate: 'happy',
    error: 'dizzy', wait: 'neutral', sleepy: 'sleep', grabbed: 'drag', poked: 'surprised', patted: 'love', falling: 'drag', dizzy: 'dizzy' };

  async function createFigure(app, url) {
    const rig = await readJSON(url);
    if (rig.format !== 'AICompanionRig' || rig.version !== 1) throw new Error('不支持的骨骼模型');
    const base = url.slice(0, url.lastIndexOf('/') + 1);
    const tex = {};
    const load = async name => {
      if (!tex[name]) { tex[name] = await PIXI.Texture.fromURL(base + name); tex[name].alpha = alphaOf(tex[name]); }
      return tex[name];
    };
    const def = makeDeformers(rig.deformers);
    const figure = new PIXI.Container(); figure.sortableChildren = true;
    const meshes = [];
    for (const part of rig.parts) {
      const [x, y, w, h] = part.box;
      const mesh = gridMesh(await load(part.tex), [x, y, x + w, y + h], part.grid, part.deformer);
      mesh.zIndex = part.z; mesh.baseZ = part.z; mesh.partId = part.id; mesh.group = part.group;
      if (part.blend === 'multiply') mesh.blendMode = PIXI.BLEND_MODES.MULTIPLY;
      mesh.visible = part.group !== 'sit';
      figure.addChild(mesh); meshes.push(mesh);
    }
    // 五官：中性眼由眼白 / 虹膜 / 高光 / 睫毛四层组成，虹膜和高光可以随视线移动；其余表情用整块贴片。
    const F = rig.features, featDeformer = F.deformer || 'headFeat', featZ = F.z || 8.3;
    const faceSets = {};
    const featMesh = async (item, z) => {
      const mesh = gridMesh(await load(item.tex), item.rect, [2, 2], featDeformer);
      mesh.zIndex = z; mesh.baseRect = item.rect; figure.addChild(mesh); meshes.push(mesh); return mesh;
    };
    faceSets.neutral = { eyes: [], movers: [], mouth: null, brows: null };
    for (const side of ['eyeL', 'eyeR']) {
      const eye = F.eyes[side] || {};
      for (const [layer, dz] of [['ball', 0], ['iris', 0.01], ['hl', 0.02], ['lash', 0.03]]) {
        if (!eye[layer]) continue;
        const mesh = await featMesh(eye[layer], featZ + dz); faceSets.neutral.eyes.push(mesh);
        if (layer === 'iris' || layer === 'hl') { mesh.mover = layer === 'hl' ? 1.25 : 1; faceSets.neutral.movers.push(mesh); }
      }
    }
    for (const name of EXPRESSIONS) {
      faceSets[name] ||= { eyes: [], movers: [], mouth: null, brows: null };
      for (const side of ['eyeL', 'eyeR']) {
        const item = F.sprites[`${name}_${side}`];
        if (item) faceSets[name].eyes.push(await featMesh(item, featZ + 0.05));
      }
      const mouth = F.sprites[`${name}_mouth`];
      if (mouth) faceSets[name].mouth = await featMesh(mouth, featZ + 0.06);
      const brows = F.sprites[`${name}_brows`];   // 眉毛跟着表情走，眨眼时不消失
      if (brows) faceSets[name].brows = await featMesh(brows, featZ + 0.04);
    }
    // 说话时张开的嘴：形象包有专门的张嘴贴片就用它，否则用开心的嘴；按开口程度纵向缩放
    const openMouth = F.sprites.talk_mouth || F.sprites.happy_mouth;
    const talkMouth = openMouth ? await featMesh(openMouth, featZ + 0.07) : null;
    const blush = new PIXI.Graphics(); blush.zIndex = featZ - 0.02; figure.addChild(blush);
    for (const set of Object.values(faceSets)) { for (const m of set.eyes) m.visible = false; if (set.mouth) set.mouth.visible = false; if (set.brows) set.brows.visible = false; }
    if (talkMouth) talkMouth.visible = false;
    figure.sortChildren();
    const arms = { near: meshes.filter(m => m.deformer === 'armNear'), far: meshes.filter(m => m.deformer === 'armFar') };
    return { rig, figure, meshes, def, faceSets, talkMouth, blush, arms };
  }

  /* ---------- 动作引擎 ---------- */
  function createEngine(app, built) {
    const { rig, figure, meshes, def, faceSets, talkMouth, blush, arms } = built;
    const P = def.params;
    const S = {
      hairX: new Spring(55, 7, 9), hairY: new Spring(50, 8, 4), bangs: new Spring(110, 10, 4),
      skirt: new Spring(100, 9, 6), flare: new Spring(90, 10, 5), tail: new Spring(40, 5, 32), finN: new Spring(90, 9, 30),
      finF: new Spring(90, 9, 30), ahoge: new Spring(140, 6, 38), head: new Spring(70, 10, 16), nod: new Spring(90, 12, 8),
      armN: new Spring(60, 9, 150), armF: new Spring(60, 9, 150), legB: new Spring(80, 10, 40), legF: new Spring(80, 10, 40),
      lean: new Spring(60, 10, 20), squash: new Spring(260, 14, 0.4), swing: new Spring(28, 2.6, 55),
      gazeX: new Spring(160, 18, 2), gazeY: new Spring(160, 18, 2), turnX: new Spring(70, 12, 12), turnY: new Spring(70, 12, 8),
      facing: new Spring(220, 26, 1.2),
    };
    S.facing.x = 1;
    const state = { pose: 'idle', mood: 'calm', mouth: 0, voice: false, walking: 0, dragging: false, dragVx: 0,
      facing: 1, gazeX: 0, gazeY: 0, blushBoost: 0 };
    let t = 0, frames = 0, poseSince = 0, face = 'neutral', faceSwapUntil = -1, nextBlink = 1.4, blinkUntil = -1;
    let hopStart = -1, hopDur = 0, hopH = 0, wasHop = false, flap = true, nextFlap = 0, lookAt = 0, lookTarget = [0, 0], nextLook = 4;
    // 台词标注触发的表情（持续几秒）和动作（一段时间线，叠加在当前姿势上）
    let exprOverride = null, motion = null;
    const MOTIONS = { nod: 1.0, shake: 1.1, wave: 1.8, bow: 1.4, jump: 0.5, spin: 1.0, tilt: 1.6, sway: 2.0 };
    let seed = 97; const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const [vx0, vy0, vx1, vy1] = rig.space.view;

    function layout() {
      // 立绘高度占画布 92%，两侧留余量，脚底贴底边
      const w = app.renderer.width / app.renderer.resolution, h = app.renderer.height / app.renderer.resolution;
      const scale = Math.min(h * 0.92 / (vy1 - vy0), w * 0.86 / (vx1 - vx0));
      figure.scale.set(scale * S.facing.x, scale);
      figure.pivot.set(128, 256); figure.position.set(w / 2, h - 2);
      return scale;
    }
    const hopping = () => hopStart >= 0 && t - hopStart < hopDur;
    function hop(height = 18, duration = 0.42) { if (hopping()) return; hopStart = t; hopDur = duration; hopH = height; S.squash.kick(-1.4); }
    function bounce(k = 1) { S.squash.kick(-2.4 * k); S.ahoge.kick(160 * k); S.finN.kick(70 * k); S.finF.kick(-70 * k); }

    function setPose(pose) {
      if (pose === state.pose) return;
      state.pose = pose; poseSince = t;
      bounce(pose === 'sleepy' || pose === 'grabbed' ? 0.3 : 0.8);
      if (pose === 'celebrate') hop(20, 0.45);
      if (pose === 'poked') { S.head.kick(-60); S.armN.kick(-200); S.armF.kick(200); }
    }

    function showFace(name, blinking, open) {
      // 换表情时先闭一下眼（80 ms），比直接替换自然
      if (name !== face) { faceSwapUntil = t + 0.08; face = name; }
      const swapping = t < faceSwapUntil;
      const shown = swapping || (blinking && name === 'neutral') ? 'sleep' : name;
      const browsSet = faceSets[name]?.brows ? name : 'neutral';
      for (const [key, set] of Object.entries(faceSets)) {
        for (const m of set.eyes) m.visible = key === shown;
        if (set.mouth) set.mouth.visible = false;
        if (set.brows) set.brows.visible = key === browsSet;
      }
      const set = faceSets[name] || faceSets.neutral;
      // 嘴：说话时在闭嘴贴片和张嘴贴片之间切换，张嘴按开口程度纵向缩放
      const mouthSet = faceSets[name]?.mouth ? name : 'neutral';
      const closedMouth = faceSets[mouthSet].mouth;
      if (talkMouth && open > 0.12) {
        talkMouth.visible = true; talkMouth.openness = clamp(open, 0.3, 1);
        if (closedMouth) closedMouth.visible = false;
      } else {
        if (talkMouth) talkMouth.visible = false;
        if (closedMouth) closedMouth.visible = true;
      }
      return set;
    }

    function updateMeshes(gx, gy) {
      for (const mesh of meshes) {
        if (!mesh.visible) continue;
        const rest = mesh.rest, out = mesh.vertices;
        let ox = 0, oy = 0, sy = 1, cy = 0;
        const mover = mesh.mover;
        if (mover) { ox = gx * mover; oy = gy * mover; }
        if (mesh === talkMouth) { sy = mesh.openness || 1; cy = mesh.baseRect[1]; }
        for (let i = 0; i < rest.length; i += 2) {
          let x = rest[i] + ox, y = rest[i + 1] + oy;
          if (sy !== 1) y = cy + (y - cy) * sy;
          const p = def.apply(mesh.deformer, x, y);
          out[i] = p[0]; out[i + 1] = p[1];
        }
        mesh.vertices = out;
      }
    }

    function drawBlush(amount) {
      blush.clear();
      if (amount < 0.02 || !rig.features.blush) return;
      const [r, g, b] = rig.features.blush, color = (r << 16) | (g << 8) | b;
      for (const side of ['eyeL', 'eyeR']) {
        const ball = rig.features.eyes[side]?.ball; if (!ball) continue;
        const cx = (ball.rect[0] + ball.rect[2]) / 2, cyy = ball.rect[3] + 3;
        const [x, y] = def.apply('headFeat', cx, cyy);
        blush.beginFill(color, 0.55 * amount); blush.drawEllipse(x, y, 7, 3.2); blush.endFill();
      }
    }

    function tick(dtMs) {
      const dt = Math.min(0.05, dtMs / 1000); t += dt; frames++;
      const pose = state.pose, age = t - poseSince, br = Math.sin(t * 1.8);
      const walking = state.walking !== 0 && !state.dragging;
      // ---- 眨眼、视线 ----
      if (t > nextBlink) { blinkUntil = t + 0.11; nextBlink = t + (random() < 0.15 ? 0.25 : 2.4 + random() * 3.4); }
      if (t > nextLook) { lookTarget = [random() * 2 - 1, random() * 0.8 - 0.4]; lookAt = t; nextLook = t + 3 + random() * 5; }
      const cursorLook = Math.hypot(state.gazeX, state.gazeY) > 0.05;
      let gazeTX = cursorLook ? state.gazeX : lookTarget[0], gazeTY = cursorLook ? state.gazeY : lookTarget[1];
      // ---- 各姿势的目标 ----
      let armN = 6 * Math.sin(t * 0.9), armF = -6 * Math.sin(t * 0.9 + 0.6), legB = 0, legF = 0, lean = 1.2 * Math.sin(t * 0.8);
      let head = 2 * Math.sin(t * 0.7), nod = 0, tail = 7 * Math.sin(t * 1.3), finN = 3 * Math.sin(t * 1.1), finF = -3 * Math.sin(t * 1.1 + 0.4);
      let ahoge = 5 * Math.sin(t * 2.1), breathe = 0.012, flare = 0, liftHead = 0, hairLift = 0;
      let open = 0;
      const talking = pose === 'talking';
      if (talking) {
        if (t > nextFlap) { flap = !flap; nextFlap = t + (flap ? 0.09 + random() * 0.12 : 0.07 + random() * 0.08); }
        open = state.voice ? state.mouth : (flap ? 0.75 : 0);
        nod = open * 2.5; head += 2 * Math.sin(t * 2.2);
      }
      switch (pose) {
        case 'thinking': head = 9; gazeTX = -0.8; gazeTY = -0.9; armN = 38; finN = -6; finF = 6; tail = 4 * Math.sin(t * 0.7); break;
        case 'working': nod = 3 + Math.sin(t * 9) * 0.6; gazeTY = 0.9; gazeTX = 0.2; armN = 22 + 6 * Math.sin(t * 11); armF = -22 - 6 * Math.sin(t * 11 + 1.3); break;
        case 'celebrate': armN = 95 + 12 * Math.sin(t * 9); armF = -95 - 12 * Math.sin(t * 9 + 1); if (!hopping() && age < 1.6) hop(18, 0.4); tail = 20 * Math.sin(t * 8); legB = 8; legF = -8; break;
        case 'error': head = 7 * Math.sin(t * 6); lean = 3 * Math.sin(t * 6 + 1); finN = -10; finF = 10; ahoge = -25; armN = 10; armF = -10; break;
        case 'wait': lean = 3 * Math.sin(t * 1.6); armN = 45 + 18 * Math.sin(t * 4); head = 5; break;
        case 'sleepy': head = 12; nod = 5; lean = 2 * Math.sin(t * 0.5); breathe = 0.022; finN = -12; finF = 12; ahoge = -30; armN = 2; armF = -2; tail = 3 * Math.sin(t * 0.6); gazeTY = 0.5; break;
        case 'grabbed': legB = 25 * Math.sin(t * 9); legF = -25 * Math.sin(t * 9 + 1.2); armN = 60 + 25 * Math.sin(t * 7); armF = -60 - 25 * Math.sin(t * 7 + 1); tail = 25 * Math.sin(t * 6); break;
        case 'poked': armN = 50; armF = -50; head = -6; break;
        case 'falling': armN = 105 + 22 * Math.sin(t * 15); armF = -105 - 22 * Math.sin(t * 15 + 1.3); legB = 22 * Math.sin(t * 12); legF = -22 * Math.sin(t * 12 + 1); hairLift = -3.5; flare = 4; ahoge = 30; tail = 25 * Math.sin(t * 10); finN = 14; finF = -14; break;
        case 'dizzy': head = 8 * Math.sin(t * 4.2); lean = 4 * Math.cos(t * 4.2); finN = -12; finF = 12; ahoge = -22 + 8 * Math.sin(t * 4.2); armN = 14; armF = -14; tail = 5 * Math.sin(t * 2); gazeTX = Math.sin(t * 4.2); gazeTY = Math.cos(t * 4.2); break;
        case 'patted': head = 6 * Math.sin(t * 5); lean = 2 * Math.sin(t * 5); finN = 10 + 5 * Math.sin(t * 10); finF = -10 - 5 * Math.sin(t * 10); tail = 18 * Math.sin(t * 7); gazeTY = -0.3; break;
      }
      // ---- 标注动作：包络 env 从 0 升到 1 再回到 0 ----
      let blushExtra = 0;
      if (motion) {
        const u = (t - motion.start) / motion.dur;
        if (u >= 1) motion = null;
        else {
          const env = Math.sin(Math.PI * u);
          switch (motion.name) {
            case 'nod': nod += 5 * Math.max(0, Math.sin(u * Math.PI * 4)) * env; break;
            case 'shake': head += 7 * Math.sin(u * Math.PI * 6) * env; gazeTX = 0.8 * Math.sin(u * Math.PI * 6); break;
            case 'wave': armF = -112 + 24 * Math.sin(t * 13); head += 4 * env; break;
            case 'bow': lean += 20 * env; nod += 4 * env; gazeTY = 0.8; break;
            case 'tilt': head += 12 * env; break;
            case 'sway': lean += 6 * Math.sin(u * Math.PI * 4) * env; head += 5 * Math.sin(u * Math.PI * 4 + 0.6) * env; tail += 15 * Math.sin(t * 6); break;
            case 'spin': if (!motion.flipped && u > 0.25) { motion.flipped = 1; state.facing = -state.facing; }
                         if (motion.flipped === 1 && u > 0.7) { motion.flipped = 2; state.facing = -state.facing; } break;
          }
        }
      }
      if (exprOverride && t > exprOverride.until) exprOverride = null;
      if (exprOverride && exprOverride.name === 'smug') head += 6;
      if (exprOverride && (exprOverride.name === 'shy' || exprOverride.name === 'love')) blushExtra = 1;
      if (walking) {
        const w = Math.sin(t * 9);
        legB = 16 * w; legF = -16 * w; armN += 14 * -w; armF += 14 * w; lean += 3;
        if (!hopping()) hop(6, 0.25);
      }
      if (state.dragging) {
        legB = 20 * Math.sin(t * 8); legF = -20 * Math.sin(t * 8 + 1.1);
      }
      // ---- 跳跃 ----
      let hopY = 0;
      if (hopping()) { const u = (t - hopStart) / hopDur; hopY = 4 * hopH * u * (1 - u); flare = 3 * Math.sin(u * Math.PI); }
      if (wasHop && !hopping()) { hopStart = -1; S.squash.kick(-1.8); S.flare.kick(30); S.ahoge.kick(120); S.hairY.kick(25); }
      wasHop = hopping();
      // ---- 弹簧 ----
      const moveV = state.dragging ? state.dragVx * 0.02 : state.walking * 2.5;
      S.armN.step(armN, dt); S.armF.step(armF, dt); S.legB.step(legB, dt); S.legF.step(legF, dt);
      S.lean.step(lean, dt); S.head.step(head, dt); S.nod.step(nod, dt); S.tail.step(tail - moveV * 4, dt);
      S.finN.step(finN, dt); S.finF.step(finF, dt); S.ahoge.step(ahoge - moveV * 6, dt); S.squash.step(0, dt);
      S.hairX.step(-moveV * 1.6 + Math.sin(t * 0.9) * 0.6 - S.head.x * 0.18, dt); S.hairY.step(hairLift, dt);
      S.bangs.step(-moveV * 0.6 - S.head.x * 0.05, dt); S.skirt.step(-moveV * 1.2 + S.lean.x * 0.2, dt); S.flare.step(flare, dt);
      S.gazeX.step(clamp(gazeTX, -1, 1) * 1.6, dt); S.gazeY.step(clamp(gazeTY, -1, 1) * 1.2, dt);
      S.turnX.step(clamp(gazeTX, -1, 1) * 3.2, dt); S.turnY.step(clamp(gazeTY, -1, 1) * 1.6 + liftHead, dt);
      S.swing.step(state.dragging ? clamp(-state.dragVx * 0.03, -40, 40) : 0, dt);
      S.facing.step(state.facing >= 0 ? 1 : -1, dt);
      // ---- 写入变形器参数 ----
      const sq = S.squash.x;
      Object.assign(P.body, { a: S.swing.x * 0.15, sx: 1 - sq * 0.55, sy: 1 + sq + breathe * br, tx: 0, ty: -hopY });
      P.waist.a = S.lean.x; P.neck.a = S.head.x; P.neck.ty = S.nod.x;
      P.armNear.a = S.armN.x; P.armFar.a = S.armF.x;
      // 手臂抬过肩时会被侧发和脸挡住：临时提到刘海下面一层
      let resort = false;
      for (const [list, angle] of [[arms.near, S.armN.x], [arms.far, -S.armF.x]]) {
        for (const m of list) { const z = angle > 60 ? 12.6 + (m.baseZ - 6) * 0.01 : m.baseZ; if (m.zIndex !== z) { m.zIndex = z; resort = true; } }
      }
      if (resort) figure.sortChildren(); P.tail.a = S.tail.x * 0.3;
      P.tailBend.a = S.tail.x; P.legBack.a = S.legB.x; P.legFront.a = S.legF.x;
      P.legBack.ty = Math.max(0, S.legB.x) * 0.05; P.legFront.ty = Math.max(0, -S.legF.x) * 0.05;
      for (const k of ['headBack', 'headMid', 'headFeat', 'headFront']) { P[k].dx = S.turnX.x; P[k].dy = S.turnY.x; }
      P.hairSway.dx = S.hairX.x; P.hairSway.dy = S.hairY.x; P.bangsSway.dx = S.bangs.x;
      P.skirt.dx = S.skirt.x; P.skirt.flare = S.flare.x; P.skirtSit.dx = S.skirt.x;
      P.finNear.a = S.finN.x; P.finFar.a = S.finF.x; P.ahoge.a = S.ahoge.x;
      // ---- 表情 ----
      let expr = POSE_FACE[pose] || 'neutral';
      if (pose === 'idle' && state.mood === 'happy') expr = 'happy';
      // 标注表情只在"平常"姿势下生效，被拎起、摔晕等强烈状态优先
      if (exprOverride && ['idle', 'talking', 'thinking', 'wait', 'working'].includes(pose)) {
        const name = exprOverride.name;
        // 形象包有 shy / smug 贴片时直接用；没有时（大肥鱼）用开心 / 平静代替
        expr = faceSets[name]?.eyes.length ? name : name === 'smug' ? 'happy' : name === 'shy' ? 'neutral' : name;
        if (!faceSets[expr] || !faceSets[expr].eyes.length) expr = 'neutral';
      }
      if (pose === 'talking' && state.mood === 'happy' && open < 0.12) expr = 'happy';
      const set = showFace(expr, t < blinkUntil && !['sleepy', 'celebrate', 'poked', 'patted', 'error', 'dizzy', 'falling', 'grabbed'].includes(pose), open);
      drawBlush(clamp((state.mood === 'shy' ? 1 : 0) + (pose === 'patted' ? 0.9 : 0) + (expr === 'love' ? 0.6 : 0) + blushExtra, 0, 1));
      // ---- 整体：拖动时绕头顶摆，朝向翻转 ----
      const scale = layout();
      if (state.dragging || pose === 'grabbed') {
        // 只把旋转中心换到头顶，位置保持不变（否则一拎起来整个人会往上跳一截）
        const h = app.renderer.height / app.renderer.resolution;
        figure.pivot.set(128, 40); figure.position.y = h - 2 - (256 - 40) * scale; figure.rotation = rad(S.swing.x + 6 * Math.sin(t * 3.2));
      } else { figure.rotation = 0; }
      updateMeshes(S.gazeX.x, S.gazeY.x);
    }

    // 命中图：低分辨率渲染一份，按 alpha 生成 0/1 网格交给宿主做鼠标穿透遮罩
    const cell = 4;
    function hitmap() {
      const w = Math.ceil(app.renderer.width / app.renderer.resolution / cell), h = Math.ceil(app.renderer.height / app.renderer.resolution / cell);
      const rt = PIXI.RenderTexture.create({ width: w, height: h, resolution: 1 });
      app.renderer.render(app.stage, { renderTexture: rt, transform: new PIXI.Matrix(1 / cell, 0, 0, 1 / cell, 0, 0), clear: true });
      const pixels = app.renderer.extract.pixels(rt); rt.destroy(true);
      let bits = '';
      for (let i = 3; i < pixels.length; i += 4) bits += pixels[i] > 24 ? '1' : '0';
      // 头顶位置（窗口坐标）：气泡锚点
      const top = figure.toGlobal(new PIXI.Point(128, 20)), feet = figure.toGlobal(new PIXI.Point(128, 256));
      return { cell, width: w, height: h, bits, head: [top.x, top.y], feet: [feet.x, feet.y] };
    }

    // 光标探测：canvas 坐标 → 骨骼坐标，逐个可见部件（从上层到下层）判断是否点在不透明像素上
    const ordered = () => meshes.filter(m => m.visible && m.worldVisible).sort((a, b) => b.zIndex - a.zIndex);
    function probe(cx, cy) {
      let hit = false;
      for (const [ox, oy] of [[0, 0], [3, 0], [-3, 0], [0, 3], [0, -3]]) {
        const p = figure.toLocal(new PIXI.Point(cx + ox, cy + oy));
        if (ordered().some(m => meshHit(m, p.x, p.y))) { hit = true; break; }
      }
      const top = figure.toGlobal(new PIXI.Point(128, 20)), feet = figure.toGlobal(new PIXI.Point(128, 256));
      return { hit, head: [top.x, top.y], feet: [feet.x, feet.y] };
    }

    // 有动作时 60 帧，安静待机时 24 帧（省 CPU 和合成开销）
    const busy = () => state.dragging || state.walking !== 0 || hopping() || state.pose !== 'idle' && state.pose !== 'sleepy' && state.pose !== 'wait'
      || Math.abs(S.squash.v) > 0.05 || Math.abs(S.swing.x) > 0.5;
    function express(name, seconds = 3) { exprOverride = { name, until: t + seconds }; }
    function playMotion(name) {
      if (!(name in MOTIONS)) return false;
      if (name === 'jump') { hop(22, 0.45); return true; }
      motion = { name, start: t, dur: MOTIONS[name] }; S.squash.kick(-0.8); return true;
    }
    return {
      tick, hop, bounce, setPose, hitmap, probe, state, express, playMotion,
      busy: () => busy() || !!motion || !!exprOverride,
      set(values) { const { pose, ...rest } = values; Object.assign(state, rest); if (pose) setPose(pose); },
      diagnostics: () => ({ frames, t, pose: state.pose, face, hopping: hopping(), armNear: P.armNear.a, neck: P.neck.a,
        hair: P.hairSway.dx, swing: S.swing.x, legBack: P.legBack.a, facing: S.facing.x }),
    };
  }

  window.RigRenderer = { createFigure, createEngine, Spring };
})();
