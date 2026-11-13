import * as THREE from "three";
import { RectAreaLightUniformsLib } from "three/addons/lights/RectAreaLightUniformsLib.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { HorizontalBlurShader } from "three/addons/shaders/HorizontalBlurShader.js";
import { VerticalBlurShader } from "three/addons/shaders/VerticalBlurShader.js";

export function createStudio(renderer: THREE.WebGLRenderer, scene: THREE.Scene) {
  const paper = new THREE.Color(0xece8e8);
  scene.background = paper; scene.fog = new THREE.Fog(paper, 7, 18);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1;
  const pmrem = new THREE.PMREMGenerator(renderer), room = new RoomEnvironment();
  const environment = pmrem.fromScene(room);
  scene.environment = environment.texture; scene.environmentIntensity = 0.3;
  room.dispose(); pmrem.dispose();
  RectAreaLightUniformsLib.init();
  scene.add(new THREE.HemisphereLight(0xfffaf5, 0x8a8190, 0.15));
  const key = new THREE.RectAreaLight(0xfff3e6, 3, 4, 5);
  key.position.set(-3, 4, 4); key.lookAt(0, 1, 0); scene.add(key);
  const fill = new THREE.RectAreaLight(0xeaf0ff, 0.6, 5, 5);
  fill.position.set(3, 2.5, 1); fill.lookAt(0, 1, 0); scene.add(fill);
  const rim = new THREE.RectAreaLight(0xfff3e6, 2, 2, 4);
  rim.position.set(1, 3, -3); rim.lookAt(0, 1, 0); scene.add(rim);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(40, 40), new THREE.MeshStandardMaterial({ color: paper, roughness: 1 }));
  floor.rotation.x = -Math.PI / 2; floor.position.y = -0.005; scene.add(floor);

  // Height-faded contact projection, following three.js's contact-shadow example.
  const target = new THREE.WebGLRenderTarget(512, 512), scratch = new THREE.WebGLRenderTarget(512, 512);
  target.texture.generateMipmaps = scratch.texture.generateMipmaps = false;
  const geometry = new THREE.PlaneGeometry(3, 3).rotateX(Math.PI / 2);
  const shadow = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ map: target.texture, transparent: true, opacity: 0.3, depthWrite: false, side: THREE.DoubleSide, toneMapped: false }));
  shadow.position.y = 0.001; scene.add(shadow);
  const camera = new THREE.OrthographicCamera(-1.5, 1.5, 1.5, -1.5, 0, 0.6);
  camera.position.y = -0.001; camera.up.set(0, 0, 1); camera.lookAt(0, 1, 0);
  const depth = new THREE.MeshDepthMaterial();
  depth.onBeforeCompile = shader => {
    shader.fragmentShader = shader.fragmentShader.replace(
      "gl_FragColor = vec4( vec3( 1.0 - fragCoordZ ), opacity );",
      "gl_FragColor = vec4( vec3( 0.0 ), pow( 1.0 - fragCoordZ, 2.0 ) );",
    );
  };
  const horizontal = new THREE.ShaderMaterial({ ...HorizontalBlurShader, uniforms: THREE.UniformsUtils.clone(HorizontalBlurShader.uniforms), depthTest: false });
  const vertical = new THREE.ShaderMaterial({ ...VerticalBlurShader, uniforms: THREE.UniformsUtils.clone(VerticalBlurShader.uniforms), depthTest: false });
  const quad = new THREE.Mesh(geometry, horizontal);
  const clear = new THREE.Color();
  function updateShadow() {
    const previousTarget = renderer.getRenderTarget(), background = scene.background, override = scene.overrideMaterial;
    const alpha = renderer.getClearAlpha(); renderer.getClearColor(clear);
    floor.visible = shadow.visible = false; scene.background = null; scene.overrideMaterial = depth;
    renderer.setClearColor(0x000000, 0); renderer.setRenderTarget(target); renderer.render(scene, camera);
    scene.overrideMaterial = override; scene.background = background;
    for (const blur of [3, 1.2]) {
      quad.material = horizontal; horizontal.uniforms.tDiffuse.value = target.texture; horizontal.uniforms.h.value = blur / 512;
      renderer.setRenderTarget(scratch); renderer.render(quad, camera);
      quad.material = vertical; vertical.uniforms.tDiffuse.value = scratch.texture; vertical.uniforms.v.value = blur / 512;
      renderer.setRenderTarget(target); renderer.render(quad, camera);
    }
    floor.visible = shadow.visible = true; renderer.setRenderTarget(previousTarget); renderer.setClearColor(clear, alpha);
  }
  function dispose() {
    environment.dispose(); floor.geometry.dispose(); floor.material.dispose(); geometry.dispose(); shadow.material.dispose();
    depth.dispose(); horizontal.dispose(); vertical.dispose(); target.dispose(); scratch.dispose();
  }
  return { key, updateShadow, dispose };
}
