// The banks of cloud and fog the camera flies through between stations. The
// planes ride on the camera and rush past the lens; at the densest point a
// wash covers the cut, so the change of painting is never seen.
import * as THREE from "three";

const IMG = 16 / 9;

export class Veil {
  private readonly group = new THREE.Group();
  private readonly planes: THREE.Mesh[];
  private readonly wash: THREE.Mesh;

  constructor(
    private readonly camera: THREE.PerspectiveCamera,
    private readonly maps: { warm: (THREE.Texture | null)[]; cool: (THREE.Texture | null)[] },
  ) {
    this.group.renderOrder = 10000; // groups sort by their own order first
    camera.add(this.group);
    this.planes = [0, 1, 2, 3].map((i) => {
      const m = new THREE.Mesh(
        new THREE.PlaneGeometry(1, 1),
        new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false, depthTest: false, opacity: 0 }),
      );
      m.renderOrder = 5000 + i;
      this.group.add(m);
      return m;
    });
    this.wash = new THREE.Mesh(
      new THREE.PlaneGeometry(4, 4),
      new THREE.MeshBasicMaterial({ color: "#eef0f6", transparent: true, depthTest: false, depthWrite: false, opacity: 0 }),
    );
    this.wash.position.z = -0.3;
    this.wash.renderOrder = 6000;
    this.group.add(this.wash);
  }

  /** k: 0..1 through the bank, or below 0 when there is none. */
  set(k: number, warm: boolean): void {
    this.group.visible = k >= 0;
    if (k < 0) return;
    const cover = Math.sin(Math.PI * Math.min(1, Math.max(0, k)));
    const tan = Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2));
    const set = warm ? this.maps.warm : this.maps.cool;
    this.planes.forEach((m, i) => {
      const mat = m.material as THREE.MeshBasicMaterial;
      mat.map = set[i % 2] ?? null;
      mat.needsUpdate = true;
      const d = THREE.MathUtils.lerp(9 - i * 1.6, 0.35, Math.min(1, Math.max(0, k * 1.15 - i * 0.08)));
      const s = 2 * d * tan * 1.9;
      m.scale.set(s * Math.max(IMG, this.camera.aspect), s, 1);
      m.position.set((i % 2 ? 1 : -1) * s * 0.12, (i - 1.5) * s * 0.05, -d);
      mat.opacity = cover * 0.95;
    });
    (this.wash.material as THREE.MeshBasicMaterial).color.set(warm ? "#fff1e2" : "#c9d2e4");
    (this.wash.material as THREE.MeshBasicMaterial).opacity = Math.pow(cover, 3) * 0.92;
  }
}
