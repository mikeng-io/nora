import * as THREE from "three";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RectAreaLightUniformsLib } from "three/addons/lights/RectAreaLightUniformsLib.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

export type Expression = "Neutral" | "Smile" | "Happy" | "Puzzled";
export type Hand = "Left" | "Right";
export const fingers = ["Thumb", "Index", "Middle", "Ring", "Pinky"] as const;
export type Finger = typeof fingers[number];
export type Motion = "Stand" | "Idle" | "Walk" | "Run";
export async function createStage(host: HTMLElement, onContextLost: () => void) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.VSMShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 50);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.minDistance = 2.5; controls.maxDistance = 8;
  controls.maxPolarAngle = Math.PI * 0.49;
  controls.enablePan = false;
  const reset = () => { camera.position.set(0, 1.15, 4.2); controls.target.set(0, 1, 0); controls.update(); };
  reset();
  const pmrem = new THREE.PMREMGenerator(renderer);
  const room = new RoomEnvironment();
  const environment = pmrem.fromScene(room);
  scene.environment = environment.texture; scene.environmentIntensity = 0.35;
  room.dispose(); pmrem.dispose();
  RectAreaLightUniformsLib.init();
  scene.add(new THREE.HemisphereLight(0xfff7ee, 0x656070, 0.85));
  const key = new THREE.RectAreaLight(0xfff0df, 3, 3, 4);
  key.position.set(-2.5, 3.5, 3); key.lookAt(0, 1, 0); scene.add(key);
  const fill = new THREE.RectAreaLight(0xe6edff, 1.5, 3, 3);
  fill.position.set(2.5, 2, 2); fill.lookAt(0, 1, 0); scene.add(fill);
  const rim = new THREE.DirectionalLight(0xffeedb, 0.8);
  rim.position.set(1, 3, -2); scene.add(rim);
  // One shadow caster grounds the feet; the broad lights shape the face and cloth.
  const shadow = new THREE.DirectionalLight(0xfff0df, 0.7);
  shadow.position.set(-2.5, 6, 3); shadow.castShadow = true;
  shadow.shadow.mapSize.set(512, 512);
  shadow.shadow.camera.left = -1.5; shadow.shadow.camera.right = 1.5;
  shadow.shadow.camera.top = 2.5; shadow.shadow.camera.bottom = -1;
  shadow.shadow.normalBias = 0.02; shadow.shadow.radius = 5; shadow.shadow.blurSamples = 8; scene.add(shadow);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(20, 20), new THREE.ShadowMaterial({ opacity: 0.18 }));
  floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
  let mixer: THREE.AnimationMixer | undefined;
  let model: THREE.Group | undefined;
  let motion: Motion = "Stand", playing = false, head = 0, arm = 0;
  let expression: Expression = "Neutral";
  const curls = { Left: [0, 0, 0, 0, 0], Right: [0, 0, 0, 0, 0] };
  const fingerAxes = new Map<string, { flex: THREE.Vector3; oppose: THREE.Vector3 }>();
  let disposed = false, visible = true, last = 0;
  let transition: { elapsed: number; from: Map<THREE.Object3D, { rotation: THREE.Quaternion; position: THREE.Vector3 }>; to: Map<THREE.Object3D, { rotation: THREE.Quaternion; position: THREE.Vector3 }> } | undefined;
  const rest = new Map<THREE.Object3D, THREE.Quaternion>();
  const actions = new Map<string, THREE.AnimationAction>();
  const disposeModel = (root: THREE.Object3D) => root.traverse((o) => {
    if (o instanceof THREE.Mesh) {
      o.geometry.dispose();
      for (const material of [o.material].flat()) {
        for (const value of Object.values(material)) if (value instanceof THREE.Texture) value.dispose();
        material.dispose();
      }
    }
  });
  function pose() {
    if (!model || motion !== "Stand") return;
    for (const [bone, q] of rest) bone.quaternion.copy(q);
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
        bone.quaternion.copy(rest.get(bone)!);
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
  function render() { if (!disposed) renderer.render(scene, camera); }
  const resize = new ResizeObserver(() => {
    const { width, height } = host.getBoundingClientRect();
    renderer.setSize(width, height); camera.aspect = width / Math.max(height, 1); camera.updateProjectionMatrix(); render();
  });
  resize.observe(host);
  controls.addEventListener("change", render);
  const visibility = () => { visible = !document.hidden; last = 0; };
  document.addEventListener("visibilitychange", visibility);
  const contextLost = (event: Event) => { event.preventDefault(); onContextLost(); dispose(); };
  renderer.domElement.addEventListener("webglcontextlost", contextLost);
  const dispose = () => {
    if (disposed) return;
    disposed = true; resize.disconnect(); document.removeEventListener("visibilitychange", visibility);
    renderer.setAnimationLoop(null); controls.dispose(); mixer?.stopAllAction();
    if (model) { mixer?.uncacheRoot(model); disposeModel(model); }
    floor.geometry.dispose(); floor.material.dispose(); environment.dispose(); shadow.shadow.dispose();
    renderer.domElement.removeEventListener("webglcontextlost", contextLost);
    renderer.dispose(); renderer.domElement.remove();
  };
  host.appendChild(renderer.domElement);
  renderer.setAnimationLoop((time) => {
    const delta = last ? Math.min((time - last) / 1000, 0.05) : 0; last = time;
    if (!visible || (!playing && !transition)) return;
    // The mixer may skip unchanged tracks; restore the unblended target first.
    if (transition) for (const [bone, target] of transition.to) { bone.quaternion.copy(target.rotation); bone.position.copy(target.position); }
    mixer?.update(playing ? delta : 0); pose(); characterPose();
    if (transition) {
      transition.elapsed += delta;
      const weight = THREE.MathUtils.smoothstep(transition.elapsed, 0, 0.25);
      for (const [bone, from] of transition.from) {
        const target = transition.to.get(bone)!;
        target.rotation.copy(bone.quaternion); target.position.copy(bone.position);
        bone.quaternion.slerpQuaternions(from.rotation, bone.quaternion, weight);
        bone.position.lerpVectors(from.position, bone.position, weight);
      }
      if (weight === 1) transition = undefined;
    }
    render();
  });
  const ready = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).loadAsync("/models/mini-nora/character.glb").then((gltf) => {
    if (disposed) { disposeModel(gltf.scene); return; }
    model = gltf.scene;
    const bounds = new THREE.Box3().setFromObject(model);
    const center = bounds.getCenter(new THREE.Vector3());
    model.position.set(-center.x, -bounds.min.y, -center.z);
    model.traverse((o) => { if (o instanceof THREE.Mesh) o.castShadow = true; if (o instanceof THREE.Bone) rest.set(o, o.quaternion.clone()); });
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
    pose(); characterPose(); render();
  });
  return {
    ready, dispose,
    reset() { reset(); render(); },
    orbit(angle: number) { camera.position.sub(controls.target).applyAxisAngle(new THREE.Vector3(0, 1, 0), angle).add(controls.target); controls.update(); render(); },
    zoom(factor: number) { const offset = camera.position.clone().sub(controls.target); offset.setLength(THREE.MathUtils.clamp(offset.length() * factor, controls.minDistance, controls.maxDistance)); camera.position.copy(controls.target).add(offset); controls.update(); render(); },
    setMotion(value: Motion) {
      const from = new Map([...rest.keys()].map(bone => [bone, { rotation: bone.quaternion.clone(), position: bone.position.clone() }]));
      motion = value; mixer?.stopAllAction();
      for (const [bone, q] of rest) bone.quaternion.copy(q);
      if (value === "Stand") pose(); else { actions.get(value)?.reset().play(); mixer?.update(0); }
      characterPose();
      const to = new Map([...rest.keys()].map(bone => [bone, { rotation: bone.quaternion.clone(), position: bone.position.clone() }]));
      transition = { elapsed: 0, from, to }; last = 0;
      for (const [bone, start] of from) { bone.quaternion.copy(start.rotation); bone.position.copy(start.position); }
      render();
    },
    setExpression(value: Expression) { expression = value; characterPose(); render(); },
    setFinger(side: Hand, finger: Finger, value: number) { curls[side][fingers.indexOf(finger)] = THREE.MathUtils.clamp(value, 0, 1); characterPose(); render(); },
    setPlaying(value: boolean) { playing = value; last = 0; },
    setHead(value: number) { head = value; pose(); characterPose(); render(); },
    setArm(value: number) { arm = value; pose(); characterPose(); render(); },
    setLight(value: number) { key.intensity = value; render(); },
  };
}
