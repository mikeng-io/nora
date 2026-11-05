import { readFileSync } from "node:fs";
import { describe, it, expect } from "vitest";
const bytes = readFileSync(new URL("../../../public/models/mini-nora/character.glb", import.meta.url));
const gltf = JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)).toString());
describe("shipped Mini Nora asset", () => {
  it("has named walk/run clips with valid sampler and bone targets", () => {
    expect(bytes.readUInt32LE(0)).toBe(0x46546c67);
    expect(bytes.readUInt32LE(8)).toBe(bytes.length);
    expect(gltf.animations.map((a: {name: string}) => a.name).sort()).toEqual(["Run", "Walk"]);
    for (const animation of gltf.animations) {
      for (const channel of animation.channels) {
        expect(gltf.nodes[channel.target.node]).toBeDefined();
        expect(animation.samplers[channel.sampler]).toBeDefined();
      }
      for (const sampler of animation.samplers) {
        expect(gltf.accessors[sampler.input].count).toBeGreaterThan(1);
        expect(gltf.accessors[sampler.output].count).toBeGreaterThan(1);
      }
    }
    expect(gltf.skins.some((skin: { joints: number[] }) => skin.joints.length === 55)).toBe(true);
    expect(gltf.meshes[0].extras.targetNames).toEqual(["Smile", "Happy", "Puzzled"]);
    for (const side of ["Left", "Right"]) for (const finger of ["Thumb", "Index", "Middle", "Ring", "Pinky"]) for (let joint = 1; joint <= 3; joint++) {
      expect(gltf.nodes.some((node: { name: string }) => node.name === `${side}${finger}${joint}`)).toBe(true);
    }
    expect(gltf.extensionsRequired).toContain("EXT_meshopt_compression");
    expect(bytes.length).toBeLessThan(14_000_000);
    expect(gltf.nodes.some((n: {name: string}) => n.name === "HairTipFollow")).toBe(true);
  });
  it("keeps all buffers and images embedded, with declared PNG payloads", () => {
    for (const buffer of gltf.buffers) expect(buffer.uri).toBeUndefined();
    for (const image of gltf.images) {
      expect(image.uri).toBeUndefined();
      expect(image.mimeType).toBe("image/png");
      const view = gltf.bufferViews[image.bufferView];
      const start = 28 + bytes.readUInt32LE(12) + view.byteOffset;
      expect(bytes.subarray(start, start + 8).toString("hex")).toBe("89504e470d0a1a0a");
    }
  });
});
