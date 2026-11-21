// Nora on the stage: her layered rig (src/assets/journey/nora.livliv, made by
// scripts/make_nora.py) played by the LivLiv player into its own canvas, which
// the stage reads as a texture every frame. She stands in a station like any
// painted thing, lit by its light: a tint for the hour, a rim from the lantern.
import * as THREE from "three";
import { Player } from "@livliv-dev/player";
import { radial } from "./stage";

const VERT = "varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.); }";
const FRAG = `varying vec2 vUv; uniform sampler2D map; uniform float uOpacity,uRimAmt; uniform vec3 uTint,uRim; uniform vec2 uTexel,uDir;
void main(){ vec4 c=texture2D(map,vUv);
  float rim=c.a*(1.-texture2D(map,vUv+normalize(uDir)*uTexel*6.).a);
  gl_FragColor=vec4(c.rgb*uTint+uRim*rim*uRimAmt,c.a*uOpacity);
  #include <colorspace_fragment>
}`;

export class Nora {
  readonly player: Player;
  readonly mesh: THREE.Mesh;
  private readonly shadow: THREE.Mesh;
  private readonly tex: THREE.CanvasTexture;
  private readonly mat: THREE.ShaderMaterial;
  private where: THREE.Object3D | null = null;

  private constructor(canvas: HTMLCanvasElement) {
    this.player = new Player(canvas, { renderWhenHidden: true, followPointer: "page", maxPixelRatio: 2 });
    this.tex = new THREE.CanvasTexture(canvas);
    this.tex.colorSpace = THREE.SRGBColorSpace;
    this.player.onFrame = () => {
      this.tex.needsUpdate = true;
    };
    this.mat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      vertexShader: VERT,
      fragmentShader: FRAG,
      uniforms: {
        map: { value: this.tex },
        uOpacity: { value: 0 },
        uTint: { value: new THREE.Color(1, 1, 1) },
        uRim: { value: new THREE.Color("#ffb36e") },
        uRimAmt: { value: 0 },
        uTexel: { value: new THREE.Vector2(1 / 600, 1 / 1200) },
        uDir: { value: new THREE.Vector2(-1, 0.25) },
      },
    });
    this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(0.5, 1), this.mat);
    this.shadow = new THREE.Mesh(
      new THREE.PlaneGeometry(0.32, 0.06),
      new THREE.MeshBasicMaterial({ map: radial("10,14,30"), transparent: true, depthWrite: false, opacity: 0 }),
    );
  }

  static async create(canvas: HTMLCanvasElement, url: string): Promise<Nora> {
    const n = new Nora(canvas);
    const info = await n.player.load(url);
    if (info.fullBody) n.player.view.fit = "whole";
    return n;
  }

  /** Stand her in a station: her feet at a point, her canvas `height` tall. */
  place(group: THREE.Object3D, feet: THREE.Vector3, height: number, hop = true): void {
    if (this.where === group) return;
    this.where = group;
    this.mesh.scale.set(height, height, 1);
    this.mesh.position.set(feet.x, feet.y + height / 2, feet.z);
    this.shadow.scale.set(height, height, 1);
    this.shadow.position.set(feet.x, feet.y + height * 0.012, feet.z - 0.005);
    group.add(this.shadow, this.mesh);
    if (hop) this.player.act("hop");
  }

  leave(): void {
    this.where = null;
    this.mesh.removeFromParent();
    this.shadow.removeFromParent();
  }

  get group(): THREE.Object3D | null {
    return this.where;
  }

  light(opacity: number, tint: THREE.Color, rim: number): void {
    this.mat.uniforms.uOpacity.value = opacity;
    (this.mat.uniforms.uTint.value as THREE.Color).copy(tint);
    this.mat.uniforms.uRimAmt.value = rim;
    (this.shadow.material as THREE.MeshBasicMaterial).opacity = 0.5 * opacity;
  }
}
