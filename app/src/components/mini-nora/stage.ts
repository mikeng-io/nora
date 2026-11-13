import * as THREE from "three";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { createStudio } from "./studio";

export type Expression = "Neutral" | "Smile" | "Happy" | "Puzzled";
export type Hand = "Left" | "Right";
export const fingers = ["Thumb", "Index", "Middle", "Ring", "Pinky"] as const;
export type Finger = typeof fingers[number];
export type Motion = "Stand" | "Idle" | "Walk" | "Run";

type Pose = Map<THREE.Object3D, { rotation: THREE.Quaternion; position: THREE.Vector3; scale: THREE.Vector3 }>;
export function capturePose(objects: Iterable<THREE.Object3D>): Pose {
  return new Map([...objects].map(object => [object, { rotation: object.quaternion.clone(), position: object.position.clone(), scale: object.scale.clone() }]));
}
export function restorePose(pose: Pose) {
  for (const [object, value] of pose) { object.quaternion.copy(value.rotation); object.position.copy(value.position); object.scale.copy(value.scale); }
}
export function blendPose(from: Pose, to: Pose, weight: number) {
  for (const [object, start] of from) {
    const end = to.get(object)!;
    object.quaternion.slerpQuaternions(start.rotation, end.rotation, weight);
    object.position.lerpVectors(start.position, end.position, weight);
    object.scale.lerpVectors(start.scale, end.scale, weight);
  }
}

export async function createStage(host: HTMLElement, onContextLost: () => void) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 50);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.minDistance = 2.5; controls.maxDistance = 8;
  controls.maxPolarAngle = Math.PI * 0.49;
  controls.enablePan = false;
  const reset = () => { camera.position.set(-1.25, 1.35, 4.3); controls.target.set(0, 1, 0); controls.update(); };
  reset();
  const studio = createStudio(renderer, scene);
  const key = studio.key;
  let mixer: THREE.AnimationMixer | undefined;
  let model: THREE.Group | undefined;
  let motion: Motion = "Stand", playing = false, head = 0, arm = 0;
  let expression: Expression = "Neutral";
  const curls = { Left: [0, 0, 0, 0, 0], Right: [0, 0, 0, 0, 0] };
  const fingerAxes = new Map<string, { flex: THREE.Vector3; oppose: THREE.Vector3 }>();
  let disposed = false, visible = true, last = 0;
  let transition: { elapsed: number; from: Pose; to: Pose } | undefined;
  let rest: Pose = new Map();
  const actions = new Map<string, THREE.AnimationAction>();
  const disposeModel = (root: THREE.Object3D) => {
    const bitmaps = new Set<ImageBitmap>();
    root.traverse((o) => {
    if (o instanceof THREE.Mesh) {
      o.geometry.dispose();
      for (const material of [o.material].flat()) {
        for (const value of Object.values(material)) if (value instanceof THREE.Texture) {
          if (typeof ImageBitmap !== "undefined" && value.image instanceof ImageBitmap) bitmaps.add(value.image);
          value.dispose();
        }
        material.dispose();
      }
    }
    });
    for (const bitmap of bitmaps) bitmap.close();
  };
  function pose() {
    if (!model || motion !== "Stand") return;
    restorePose(rest);
    model.updateMatrixWorld(true);
    function rotate(name: string, axis: THREE.Vector3, angle: number) {
      const bone = model!.getObjectByName(name);
      if (!bone?.parent) return;
      const parent = bone.parent.getWorldQuaternion(new THREE.Quaternion());
      const localAxis = axis.applyQuaternion(parent.invert());
      bone.quaternion.premultiply(new THREE.Quaternion().setFromAxisAngle(localAxis, angle));
      model!.updateMatrixWorld(true);
    }
    rotate("LeftArm", new THREE.Vector3(0, 0, 1), -1.15 + arm * 1.15);
    rotate("RightArm", new THREE.Vector3(0, 0, 1), 1.15);
    rotate("Head", new THREE.Vector3(0, 1, 0), head);
  }
  function characterPose() {
    if (!model) return;
    for (const side of ["Left", "Right"] as const) for (const [index, finger] of fingers.entries()) {
      const amount = curls[side][index] * 1.15, sign = side === "Left" ? 1 : -1;
      for (let joint = 1; joint <= 3; joint++) {
        const name = `${side}${finger}${joint}`, bone = model.getObjectByName(name), axes = fingerAxes.get(name);
        if (!bone || !axes) continue;
        bone.quaternion.copy(rest.get(bone)!.rotation);
        if (finger === "Thumb" && joint === 1) bone.quaternion.multiply(new THREE.Quaternion().setFromAxisAngle(axes.oppose, 1.7 * curls[side][index] * sign));
        const factor = finger === "Thumb" ? (joint === 1 ? 0.45 : 0.5) : (joint === 1 ? 0.75 : 1);
        bone.quaternion.multiply(new THREE.Quaternion().setFromAxisAngle(axes.flex, amount * factor * sign));
      }
    }
    model.traverse(o => {
      if (!(o instanceof THREE.Mesh) || !o.morphTargetDictionary || !o.morphTargetInfluences) return;
      for (const name of ["Smile", "Happy", "Puzzled"]) {
        const index = o.morphTargetDictionary[name];
        if (index !== undefined) o.morphTargetInfluences[index] = name === expression ? 1 : 0;
      }
    });
    model.updateMatrixWorld(true);
  }
  let shadowDirty = true;
  function render(updateShadow = false) {
    if (disposed) return;
    shadowDirty ||= updateShadow;
    if (shadowDirty && model) { studio.updateShadow(); shadowDirty = false; }
    renderer.render(scene, camera);
  }
  const resize = new ResizeObserver(() => {
    const { width, height } = host.getBoundingClientRect();
    renderer.setSize(width, height); camera.aspect = width / Math.max(height, 1); camera.updateProjectionMatrix(); render();
  });
  resize.observe(host);
  const orbitRender = () => render();
  controls.addEventListener("change", orbitRender);
  const visibility = () => { visible = !document.hidden; last = 0; };
  document.addEventListener("visibilitychange", visibility);
  const contextLost = (event: Event) => { event.preventDefault(); onContextLost(); dispose(); };
  renderer.domElement.addEventListener("webglcontextlost", contextLost);
  const dispose = () => {
    if (disposed) return;
    disposed = true; resize.disconnect(); document.removeEventListener("visibilitychange", visibility);
    renderer.setAnimationLoop(null); controls.removeEventListener("change", orbitRender); controls.dispose(); mixer?.stopAllAction();
    if (model) { mixer?.uncacheRoot(model); disposeModel(model); }
    studio.dispose();
    renderer.domElement.removeEventListener("webglcontextlost", contextLost);
    renderer.dispose(); renderer.domElement.remove();
  };
  host.appendChild(renderer.domElement);
  renderer.setAnimationLoop((time) => {
    const delta = last ? Math.min((time - last) / 1000, 0.05) : 0; last = time;
    if (!visible || (!playing && !transition)) return;
    // The mixer may skip unchanged tracks; restore the unblended target first.
    if (transition) restorePose(transition.to);
    mixer?.update(playing ? delta : 0); pose(); characterPose();
    if (transition) {
      transition.elapsed += delta;
      const weight = THREE.MathUtils.smoothstep(transition.elapsed, 0, 0.25);
      transition.to = capturePose(rest.keys());
      blendPose(transition.from, transition.to, weight);
      if (weight === 1) transition = undefined;
    }
    render(true);
  });
  const ready = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).loadAsync("/models/mini-nora/character.glb").then((gltf) => {
    if (disposed) { disposeModel(gltf.scene); return; }
    model = gltf.scene;
    const bounds = new THREE.Box3().setFromObject(model);
    const center = bounds.getCenter(new THREE.Vector3());
    model.position.set(-center.x, -bounds.min.y, -center.z);
    const bones: THREE.Bone[] = [];
    model.traverse((o) => { if (o instanceof THREE.Mesh) o.castShadow = true; if (o instanceof THREE.Bone) bones.push(o); });
    rest = capturePose(bones);
    model.updateMatrixWorld(true);
    for (const side of ["Left", "Right"]) for (const finger of fingers) for (let joint = 1; joint <= 3; joint++) {
      const name = `${side}${finger}${joint}`, bone = model.getObjectByName(name);
      if (!bone) continue;
      const inverse = bone.getWorldQuaternion(new THREE.Quaternion()).invert();
      fingerAxes.set(name, {
        flex: new THREE.Vector3(finger === "Thumb" ? 0.707 : 0, 0, finger === "Thumb" ? -0.707 : -1).applyQuaternion(inverse).normalize(),
        oppose: new THREE.Vector3(0, 1, 0).applyQuaternion(inverse).normalize(),
      });
    }
    scene.add(model); mixer = new THREE.AnimationMixer(model);
    for (const clip of gltf.animations) actions.set(clip.name === "IdlePilotCalibrated" ? "Idle" : clip.name, mixer.clipAction(clip));
    pose(); characterPose(); render(true);
  });
  return {
    ready, dispose,
    reset() { reset(); render(); },
    orbit(angle: number) { camera.position.sub(controls.target).applyAxisAngle(new THREE.Vector3(0, 1, 0), angle).add(controls.target); controls.update(); render(); },
    zoom(factor: number) { const offset = camera.position.clone().sub(controls.target); offset.setLength(THREE.MathUtils.clamp(offset.length() * factor, controls.minDistance, controls.maxDistance)); camera.position.copy(controls.target).add(offset); controls.update(); render(); },
    setMotion(value: Motion) {
      const from = capturePose(rest.keys());
      motion = value; mixer?.stopAllAction();
      restorePose(rest);
      if (value === "Stand") pose(); else { actions.get(value)?.reset().play(); mixer?.update(0); }
      characterPose();
      const to = capturePose(rest.keys());
      transition = { elapsed: 0, from, to }; last = 0;
      restorePose(from);
      render(true);
    },
    setExpression(value: Expression) { expression = value; characterPose(); render(true); },
    setFinger(side: Hand, finger: Finger, value: number) { curls[side][fingers.indexOf(finger)] = THREE.MathUtils.clamp(value, 0, 1); characterPose(); render(true); },
    setPlaying(value: boolean) { playing = value; last = 0; },
    setHead(value: number) { head = value; pose(); characterPose(); render(true); },
    setArm(value: number) { arm = value; pose(); characterPose(); render(true); },
    setLight(value: number) { key.intensity = value; render(); },
  };
}
