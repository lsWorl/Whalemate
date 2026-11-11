/* 十姿态分层包渲染器（AICompanionPoseRig v1，见 aicompanion/body/pose_pack.py 和 docs/tasks/T6-rig-studio.md 第 A 节）。
 *
 * 和 rig.js 的单立绘骨骼不同：原鲸娘的十个姿态各自是一组切好的图层，姿态之间用 0.18 秒交叉淡入切换；
 * 姿态内只做原画允许的小幅动作——身体呼吸、头部轻摆（总量 ±2.5°，视线也折算成转头）、头发下段飘动、
 * 有标注支点的鳍/呆毛/尾巴摆动；手臂和腿不转（原画的手脚姿势是画出来的，转了会断）。
 * 眨眼和闭嘴用源包里的替换层（eyes_closed / mouth_closed / eyes_closed_mouth_closed），不是重画。
 * 拖拽、摔落、晕眩等是整体容器变换。对宿主的接口和 rig.js 的引擎一致（tick / set / hop / bounce / express /
 * playMotion / hitmap / probe / diagnostics / busy），另有 dispose 释放贴图。
 */
(function () {
  'use strict';
  const { Spring, readJSON, alphaOf, meshHit, gridMesh, loadOwnTexture } = window.RigRenderer;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
  const rad = d => d * Math.PI / 180;
  const FADE = 0.18;
  // 宿主的姿势 → 源包姿态（源包只有十个；其余用整体变换补）
  const POSE_OF = { idle: 'idle', talking: 'talking', thinking: 'thinking', working: 'working', celebrate: 'celebrate', error: 'error',
    wait: 'wait', sleepy: 'sleepy', grabbed: 'grabbed', poked: 'poked', falling: 'grabbed', dizzy: 'error', patted: 'idle' };
  // 台词标注的表情 → 姿态（只在平常姿势下生效）；love / shy 用脸红，smug 用歪头
  const EXPR_POSE = { happy: 'celebrate', surprised: 'poked', drag: 'grabbed', dizzy: 'error', sleep: 'sleepy' };
  const CALM = ['idle', 'talking', 'thinking', 'wait', 'working'];
  const MOTIONS = { nod: 1.0, shake: 1.1, wave: 1.8, bow: 1.4, jump: 0.5, spin: 1.0, tilt: 1.6, sway: 2.0 };

  /* 2D 仿射 [a, b, c, d, tx, ty]：x' = a·x + c·y + tx，y' = b·x + d·y + ty */
  const IDENT = [1, 0, 0, 1, 0, 0];
  function local(px, py, angle = 0, sx = 1, sy = 1) {
    const c = Math.cos(rad(angle)), s = Math.sin(rad(angle));
    const a = c * sx, b = s * sx, cc = -s * sy, d = c * sy;
    return [a, b, cc, d, px - a * px - cc * py, py - b * px - d * py];
  }
  const mul = (p, l) => [p[0] * l[0] + p[2] * l[1], p[1] * l[0] + p[3] * l[1], p[0] * l[2] + p[2] * l[3], p[1] * l[2] + p[3] * l[3],
    p[0] * l[4] + p[2] * l[5] + p[4], p[1] * l[4] + p[3] * l[5] + p[5]];
  const apply = (m, x, y) => [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]];

  async function createFigure(app, url) {
    const rig = await readJSON(url);
    if (rig.format !== 'AICompanionPoseRig' || rig.version !== 1) throw new Error('不支持的姿态包');
    const base = url.slice(0, url.lastIndexOf('/') + 1);
    const frameBase = rig.frame_actions ? base + rig.frame_actions.slice(0, rig.frame_actions.lastIndexOf('/') + 1) : null;
    const frameConfig = rig.frame_actions ? await readJSON(base + rig.frame_actions) : null;
    if (frameConfig && frameConfig.type !== 'full-frame-sequence') throw new Error('不支持的整帧动作格式');
    const textures = [];
    const figure = new PIXI.Container(); figure.sortableChildren = true;
    const inner = new PIXI.Container(); inner.sortableChildren = true; figure.addChild(inner);   // 跳跃、挤压在这一层
    const poses = {};
    try { await buildPoses(); } catch (error) {
      // 加载中途失败：释放已经加载的纹理再抛出，不留泄漏
      figure.destroy({ children: true }); for (const tex of textures) tex.destroy(true); throw error;
    }
    return { rig, figure, inner, poses, textures, frameConfig, frameBase };

    async function buildPoses() {
    for (const [name, spec] of Object.entries(rig.poses)) {
      const layer = new PIXI.Container(); layer.sortableChildren = true; layer.visible = false; layer.zIndex = 0;
      const meshes = [], byId = {};
      const build = async (part) => {
        const texture = await loadOwnTexture(base + part.tex); texture.alpha = alphaOf(texture); textures.push(texture);   // 每个引擎自己的纹理，不进全局缓存
        const grid = part.hair ? [4, 8] : part.swing ? [3, 3] : [1, 1];
        const mesh = gridMesh(texture, part.rect, grid, null);
        mesh.zIndex = part.z; mesh.part = part; layer.addChild(mesh); meshes.push(mesh);
        return mesh;
      };
      for (const part of spec.parts) byId[part.id] = await build(part);
      const variants = {};
      for (const [vname, list] of Object.entries(spec.variants || {})) {
        variants[vname] = [];
        for (const part of list) { const mesh = await build(part); mesh.visible = false; variants[vname].push(mesh); }
      }
      const blush = new PIXI.Graphics(); blush.zIndex = 8.5; layer.addChild(blush);
      layer.sortChildren(); inner.addChild(layer);
      poses[name] = { name, spec, layer, meshes, byId, variants, blush, shown: null, swingNodes: new Set(spec.swing_nodes || []) };
    }
    }
  }

  function createBaseEngine(app, built) {
    const { rig, figure, inner, poses, textures } = built;
    const S = {
      head: new Spring(70, 10, 6), hair: new Spring(55, 7, 3), hairY: new Spring(50, 8, 2), swing: new Spring(28, 2.6, 55),
      squash: new Spring(260, 14, 0.04), whole: new Spring(60, 9, 8), facing: new Spring(220, 26, 1.2),
      fin: new Spring(90, 9, 6), ahoge: new Spring(140, 6, 9), tail: new Spring(40, 5, 5),
    };
    S.facing.x = 1;
    const state = { pose: 'idle', mood: 'calm', mouth: 0, voice: false, walking: 0, dragging: false, dragVx: 0,
      facing: 1, gazeX: 0, gazeY: 0, blushBoost: 0 };
    let t = 0, frames = 0, nextBlink = 1.4, blinkUntil = -1, flap = true, nextFlap = 0, lookTarget = [0, 0], nextLook = 4;
    let hopStart = -1, hopDur = 0, hopH = 0, wasHop = false, exprOverride = null, motion = null, disposed = false;
    let current = null, previous = null, fadeStart = -1, variantShown = null, lastMats = {};
    let seed = 97; const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const [vx0, vy0, vx1, vy1] = rig.space.view, grab = rig.space.grab;

    function layout() {
      const w = app.renderer.width / app.renderer.resolution, h = app.renderer.height / app.renderer.resolution;
      const scale = Math.min(h * 0.92 / (vy1 - vy0), w * 0.86 / (vx1 - vx0));
      figure.scale.set(scale * S.facing.x, scale);
      figure.pivot.set(128, 256); figure.position.set(w / 2, h - 2);
      return scale;
    }
    const hopping = () => hopStart >= 0 && t - hopStart < hopDur;
    function hop(height = 18, duration = 0.42) { if (hopping()) return; hopStart = t; hopDur = duration; hopH = height; S.squash.kick(-0.25); }
    function bounce(k = 1) { S.squash.kick(-0.3 * k); S.ahoge.kick(60 * k); S.fin.kick(40 * k); S.hair.kick(8 * k); }

    function show(name) {
      const next = poses[name] || poses.idle;
      if (next === current) return;
      if (previous && previous !== next) previous.layer.visible = false;
      previous = current; current = next; fadeStart = t;
      current.layer.visible = true; current.layer.alpha = previous ? 0 : 1; current.layer.zIndex = 1;
      if (previous) previous.layer.zIndex = 0;
      inner.sortChildren(); variantShown = null;
      bounce(name === 'sleepy' || name === 'grabbed' ? 0.3 : 0.8);
    }
    function setPose(pose) {
      if (pose === state.pose) return;
      state.pose = pose;
      if (pose === 'celebrate') hop(14, 0.45);
    }

    // 替换层：闭眼 / 闭嘴（或两者）。没有对应替换的姿态保持原样。
    function setVariant(pose, eyes, mouth) {
      const v = pose.variants;
      let name = null;
      if (eyes && mouth) name = v.eyes_closed_mouth_closed ? 'eyes_closed_mouth_closed' : v.mouth_closed ? 'mouth_closed' : v.eyes_closed ? 'eyes_closed' : null;
      else if (eyes) name = v.eyes_closed ? 'eyes_closed' : null;
      else if (mouth) name = v.mouth_closed ? 'mouth_closed' : null;
      if (pose.shown === name) return name;
      const hidden = new Set(name ? v[name].map(m => m.part.replaces) : []);
      for (const m of pose.meshes) m.visible = !hidden.has(m.part.id);
      for (const [key, list] of Object.entries(v)) for (const m of list) m.visible = key === name;
      pose.shown = name; return name;
    }

    // 节点矩阵：body 呼吸（以脚底为中心纵向缩放），head 和 hair 一起绕脖子转，有标注支点的部件自己摆
    function nodeMatrices(pose, angles) {
      const nodes = pose.spec.nodes, out = {}, motionCfg = pose.spec.motion, swingNodes = pose.swingNodes;
      const headPivot = (nodes.head || nodes.body).pivot;
      const localOf = (name) => {
        const n = nodes[name];
        if (name === 'body') return local(n.pivot[0], n.pivot[1], 0, 1, 1 + angles.breathe);
        if (name === 'head' || name === 'hair') return local(headPivot[0], headPivot[1], angles.head);
        const kind = { fin_left: 'fin', fin_right: 'fin', ahoge: 'ahoge', tail: 'tail' }[name];
        // 只有编译时确认就绪（补画 + 自己的枢轴）的节点才摆；未就绪的节点矩阵就是父节点的（只随身体），相对旋转严格为 0
        if (kind && swingNodes.has(name)) return local(n.pivot[0], n.pivot[1], clamp(angles[kind] * (name === 'fin_right' ? -1 : 1), -motionCfg[kind], motionCfg[kind]));
        return IDENT;
      };
      const resolve = (name, seen = new Set()) => {
        if (out[name]) return out[name];
        if (!nodes[name] || seen.has(name)) return IDENT;
        seen.add(name);
        const parent = nodes[name].parent;
        return (out[name] = mul(parent ? resolve(parent, seen) : IDENT, localOf(name)));
      };
      for (const name of Object.keys(nodes)) resolve(name);
      return out;
    }

    function deform(pose, angles) {
      const mats = nodeMatrices(pose, angles), tip = angles.hairTip;
      for (const mesh of pose.layer.children) {
        if (!mesh.rest || !mesh.visible) continue;
        const part = mesh.part, m = mats[part.node] || IDENT, rest = mesh.rest, out = mesh.vertices;
        const [x0, y0, x1, y1] = part.rect;
        for (let i = 0; i < rest.length; i += 2) {
          let x = rest[i], y = rest[i + 1];
          if (part.hair) {
            // 上 30% 固定，往下渐强；发梢最多 hair_tip_px 源像素
            const w = smooth(0.3, 1, (y - y0) / (y1 - y0));
            x += tip[0] * w; y += tip[1] * w;
          }
          const p = apply(m, x, y); out[i] = p[0]; out[i + 1] = p[1];
        }
        mesh.vertices = out;
      }
      return mats;
    }

    function drawBlush(pose, amount, mats) {
      const g = pose.blush; g.clear();
      if (amount < 0.02) return;
      const [r, gg, b] = rig.blush || [255, 120, 140], color = (r << 16) | (gg << 8) | b, head = mats.head || IDENT;
      for (const [cx, cy] of pose.spec.cheeks || []) {
        const [x, y] = apply(head, cx, cy);
        g.beginFill(color, 0.5 * amount); g.drawEllipse(x, y, 6, 2.6); g.endFill();
      }
    }

    function tick(dtMs) {
      if (disposed) return;
      const dt = Math.min(0.05, dtMs / 1000); t += dt; frames++;
      const pose = state.pose;
      // ---- 选姿态：宿主姿势 → 标注表情 → 说话 ----
      let shown = POSE_OF[pose] || 'idle', head = 1.2 * Math.sin(t * 0.7), whole = 0, blush = state.mood === 'shy' ? 1 : 0;
      if (exprOverride && t > exprOverride.until) exprOverride = null;
      if (exprOverride && CALM.includes(pose)) {
        const e = exprOverride.name;
        if (EXPR_POSE[e] && poses[EXPR_POSE[e]]) shown = EXPR_POSE[e];
        if (e === 'love' || e === 'shy') blush = 1;
        if (e === 'smug') head += 2;
      }
      if (pose === 'patted') { blush = Math.max(blush, 0.9); head += 1.5 * Math.sin(t * 5); }
      // 有声音时：平常姿势切到说话姿态（只有它有闭嘴替换层，按音量开合）
      if (state.voice && CALM.includes(pose) && !(exprOverride && EXPR_POSE[exprOverride.name])) shown = 'talking';
      if (motion && motion.name === 'wave' && CALM.includes(pose)) shown = 'celebrate';   // 挥手：片刻切到举手的姿态
      show(shown);
      // ---- 眨眼、口型、视线 ----
      if (t > nextBlink) { blinkUntil = t + 0.11; nextBlink = t + (random() < 0.15 ? 0.25 : 2.4 + random() * 3.4); }
      if (t > nextLook) { lookTarget = [random() * 2 - 1, random() * 0.8 - 0.4]; nextLook = t + 3 + random() * 5; }
      let open = 0;
      if (shown === 'talking') {
        if (t > nextFlap) { flap = !flap; nextFlap = t + (flap ? 0.09 + random() * 0.12 : 0.07 + random() * 0.08); }
        open = state.voice ? state.mouth : (flap ? 0.75 : 0);
        head += open * 0.8;
      }
      const cursor = Math.hypot(state.gazeX, state.gazeY) > 0.05;
      const look = clamp(cursor ? state.gazeX : lookTarget[0], -1, 1) * current.spec.motion.look;
      // ---- 整体变换：摔落、晕眩、标注动作 ----
      if (pose === 'dizzy') whole = 4 * Math.sin(t * 4.2);
      if (pose === 'falling') whole = 6 * Math.sin(t * 10);
      if (motion) {
        const u = (t - motion.start) / motion.dur;
        if (u >= 1) motion = null;
        else {
          const env = Math.sin(Math.PI * u);
          switch (motion.name) {
            case 'nod': head += 2 * Math.max(0, Math.sin(u * Math.PI * 4)) * env; break;
            case 'shake': head += 2 * Math.sin(u * Math.PI * 6) * env; break;
            case 'tilt': case 'bow': head += 2 * env; break;
            case 'sway': whole += 3 * Math.sin(u * Math.PI * 4) * env; break;
            case 'spin': if (!motion.flipped && u > 0.25) { motion.flipped = 1; state.facing = -state.facing; }
                         if (motion.flipped === 1 && u > 0.7) { motion.flipped = 2; state.facing = -state.facing; } break;
          }
        }
      }
      if (state.walking !== 0 && !state.dragging && !hopping()) hop(5, 0.25);
      // ---- 跳跃 ----
      let hopY = 0;
      if (hopping()) { const u = (t - hopStart) / hopDur; hopY = 4 * hopH * u * (1 - u); }
      if (wasHop && !hopping()) { hopStart = -1; S.squash.kick(-0.3); S.hairY.kick(6); S.ahoge.kick(60); }
      wasHop = hopping();
      // ---- 弹簧（幅度都按姿态上限截断） ----
      const cfg = current.spec.motion, src = rig.source.scale;   // 源像素 → 骨骼单位
      const moveV = state.dragging ? state.dragVx * 0.02 : state.walking * 2.5;
      S.head.step(head + look, dt); S.whole.step(whole, dt); S.squash.step(0, dt);
      S.hair.step(-moveV * 0.8 + Math.sin(t * 0.9) * 0.6 - S.head.x * 0.3, dt); S.hairY.step(0, dt);
      S.fin.step(3 * Math.sin(t * 1.1), dt); S.ahoge.step(5 * Math.sin(t * 2.1) - moveV * 3, dt); S.tail.step(3 * Math.sin(t * 1.3) - moveV, dt);
      S.swing.step(state.dragging ? clamp(-state.dragVx * 0.03, -40, 40) : 0, dt);
      S.facing.step(state.facing >= 0 ? 1 : -1, dt);
      const tipMax = cfg.hair_tip_px * src;
      const angles = {
        head: clamp(S.head.x, -cfg.head, cfg.head),
        breathe: cfg.breathe * Math.sin(t * 1.8) * (pose === 'sleepy' ? 1 : 0.7),
        hairTip: [clamp(S.hair.x * 0.4, -tipMax, tipMax), clamp(S.hairY.x * 0.2, -tipMax, tipMax)],
        fin: S.fin.x, ahoge: S.ahoge.x, tail: S.tail.x,
      };
      // ---- 交叉淡入 ----
      if (previous) {
        const u = smooth(0, 1, (t - fadeStart) / FADE);
        current.layer.alpha = u;
        if (u >= 1) { previous.layer.visible = false; previous = null; }
      }
      const blinking = t < blinkUntil;
      variantShown = setVariant(current, blinking, shown === 'talking' && open < 0.12);
      const mats = deform(current, angles); lastMats = mats;
      if (previous) deform(previous, angles);
      drawBlush(current, clamp(blush + state.blushBoost, 0, 1), mats);
      // ---- 整体 ----
      const scale = layout(), sq = S.squash.x;
      inner.pivot.set(128, 256); inner.position.set(128, 256 - hopY); inner.scale.set(1 - sq * 0.55, 1 + sq);
      inner.rotation = rad(S.whole.x);
      if (state.dragging || shown === 'grabbed') {
        // 拎起时绕后领挂点摆动；位置不变（否则一拎起来整个人会跳一截）
        const h = app.renderer.height / app.renderer.resolution;
        figure.pivot.set(grab[0], grab[1]); figure.position.y = h - 2 - (256 - grab[1]) * scale;
        figure.rotation = rad(S.swing.x + 4 * Math.sin(t * 3.2));
      } else figure.rotation = 0;
    }

    const cell = 4;
    function anchors() {
      const top = figure.toGlobal(new PIXI.Point(128, 30)), feet = figure.toGlobal(new PIXI.Point(128, 256));
      return { head: [top.x, top.y], feet: [feet.x, feet.y] };
    }
    function hitmap() {
      const w = Math.ceil(app.renderer.width / app.renderer.resolution / cell), h = Math.ceil(app.renderer.height / app.renderer.resolution / cell);
      const rt = PIXI.RenderTexture.create({ width: w, height: h, resolution: 1 });
      app.renderer.render(app.stage, { renderTexture: rt, transform: new PIXI.Matrix(1 / cell, 0, 0, 1 / cell, 0, 0), clear: true });
      const pixels = app.renderer.extract.pixels(rt); rt.destroy(true);
      let bits = '';
      for (let i = 3; i < pixels.length; i += 4) bits += pixels[i] > 24 ? '1' : '0';
      return { cell, width: w, height: h, bits, ...anchors() };
    }
    function probe(cx, cy) {
      const live = current ? current.layer.children.filter(m => m.rest && m.visible).sort((a, b) => b.zIndex - a.zIndex) : [];
      let hit = false;
      for (const [ox, oy] of [[0, 0], [3, 0], [-3, 0], [0, 3], [0, -3]]) {
        const p = current.layer.toLocal(new PIXI.Point(cx + ox, cy + oy));
        if (live.some(m => meshHit(m, p.x, p.y))) { hit = true; break; }
      }
      return { hit, ...anchors() };
    }
    const busy = () => state.dragging || state.walking !== 0 || hopping() || !!previous || !!motion || !!exprOverride
      || !['idle', 'sleepy', 'wait'].includes(state.pose) || Math.abs(S.squash.v) > 0.01 || Math.abs(S.swing.x) > 0.5;
    function express(name, seconds = 3) { exprOverride = { name, until: t + seconds }; }
    function playMotion(name) {
      if (!(name in MOTIONS)) return false;
      if (name === 'jump') { hop(16, 0.45); return true; }
      motion = { name, start: t, dur: MOTIONS[name] }; S.squash.kick(-0.15); return true;
    }
    function dispose() {
      disposed = true;
      figure.destroy({ children: true });
      for (const tex of textures) tex.destroy(true);
      textures.length = 0;
    }
    show('idle');
    return {
      tick, hop, bounce, setPose, hitmap, probe, state, express, playMotion, dispose, busy,
      set(values) { const { pose, ...rest } = values; Object.assign(state, rest); if (pose) setPose(pose); },
      diagnostics: () => ({ kind: 'posepack', frames, t, pose: state.pose, shown: current && current.name, variant: variantShown,
        fading: !!previous, head: S.head.x, hair: S.hair.x, swing: S.swing.x, facing: S.facing.x, hopping: hopping(),
        textures: textures.length, poses: Object.keys(poses).length, swinging: current ? [...current.swingNodes] : [],
        // 鳍 / 呆毛 / 尾巴相对父节点的旋转角（度）：未就绪的必须是 0
        relative: Object.fromEntries(['fin_left', 'fin_right', 'ahoge', 'tail'].filter(n => lastMats[n] && current.spec.nodes[n]).map(n => {
          const m = lastMats[n], p = lastMats[current.spec.nodes[n].parent] || IDENT;
          return [n, (Math.atan2(m[1], m[0]) - Math.atan2(p[1], p[0])) * 180 / Math.PI];
        })) }),
    };
  }

  function createEngine(app, built) {
    const base = createBaseEngine(app, built);
    return built.frameConfig ? window.FullFrameActions.wrap(app, built, base) : base;
  }
  window.PosePackRenderer = { createFigure, createEngine };
})();
