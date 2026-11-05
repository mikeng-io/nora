import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

export type Motion = "Stand" | "Walk" | "Run";
export async function createStage(host: HTMLElement, onContextLost: () => void) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
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
  scene.environment = environment.texture; scene.environmentIntensity = 0.6;
  room.dispose(); pmrem.dispose();
  scene.add(new THREE.HemisphereLight(0xfff7ee, 0x656070, 2));
  const key = new THREE.DirectionalLight(0xfff0df, 3);
  key.position.set(2, 4, 3); key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.camera.left = -2; key.shadow.camera.right = 2;
  key.shadow.camera.top = 3; key.shadow.camera.bottom = -1;
  key.shadow.normalBias = 0.02; scene.add(key);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(20, 20), new THREE.ShadowMaterial({ opacity: 0.18 }));
  floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
  let mixer: THREE.AnimationMixer | undefined;
  let model: THREE.Group | undefined;
  let motion: Motion = "Stand", playing = false, head = 0, arm = 0;
  let disposed = false, visible = true, last = 0;
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
    floor.geometry.dispose(); floor.material.dispose(); environment.dispose(); key.shadow.dispose();
    renderer.domElement.removeEventListener("webglcontextlost", contextLost);
    renderer.dispose(); renderer.domElement.remove();
  };
  host.appendChild(renderer.domElement);
  renderer.setAnimationLoop((time) => {
    const delta = last ? Math.min((time - last) / 1000, 0.05) : 0; last = time;
    if (!visible || !playing) return;
    mixer?.update(delta); render();
  });
  const ready = new GLTFLoader().loadAsync("/models/mini-nora/character.glb").then((gltf) => {
    if (disposed) { disposeModel(gltf.scene); return; }
    model = gltf.scene;
    const bounds = new THREE.Box3().setFromObject(model);
    const center = bounds.getCenter(new THREE.Vector3());
    model.position.set(-center.x, -bounds.min.y, -center.z);
    model.traverse((o) => { if (o instanceof THREE.Mesh) o.castShadow = true; if (o instanceof THREE.Bone) rest.set(o, o.quaternion.clone()); });
    scene.add(model); mixer = new THREE.AnimationMixer(model);
    for (const clip of gltf.animations) actions.set(clip.name, mixer.clipAction(clip));
    pose(); render();
  });
  return {
    ready, dispose,
    reset() { reset(); render(); },
    orbit(angle: number) { camera.position.sub(controls.target).applyAxisAngle(new THREE.Vector3(0, 1, 0), angle).add(controls.target); controls.update(); render(); },
    zoom(factor: number) { const offset = camera.position.clone().sub(controls.target); offset.setLength(THREE.MathUtils.clamp(offset.length() * factor, controls.minDistance, controls.maxDistance)); camera.position.copy(controls.target).add(offset); controls.update(); render(); },
    setMotion(value: Motion) {
      motion = value; mixer?.stopAllAction();
      for (const [bone, q] of rest) bone.quaternion.copy(q);
      if (value === "Stand") pose(); else { actions.get(value)?.reset().play(); mixer?.update(0); }
      render();
    },
    setPlaying(value: boolean) { playing = value; last = 0; },
    setHead(value: number) { head = value; pose(); render(); },
    setArm(value: number) { arm = value; pose(); render(); },
    setLight(value: number) { key.intensity = value; render(); },
  };
}
