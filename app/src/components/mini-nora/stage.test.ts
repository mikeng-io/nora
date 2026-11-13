import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { capturePose, restorePose, blendPose } from "./stage";

describe("Mini Nora motion transitions", () => {
  it("restores reference translation, rotation and scale after animation", () => {
    const bone = new THREE.Bone();
    bone.name = "Hips"; bone.position.set(0, 1, 0);
    const rest = capturePose([bone]);
    const turn = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 2);
    const clip = new THREE.AnimationClip("Run", 1, [
      new THREE.VectorKeyframeTrack("Hips.position", [0, 1], [0, 1, 0, 0.3, 0.7, 0.1]),
      new THREE.VectorKeyframeTrack("Hips.scale", [0, 1], [1, 1, 1, 0.8, 1.2, 1]),
      new THREE.QuaternionKeyframeTrack("Hips.quaternion", [0, 1], [0, 0, 0, 1, ...turn.toArray()]),
    ]);
    const mixer = new THREE.AnimationMixer(bone);
    mixer.clipAction(clip).play(); mixer.update(0.5);
    expect(bone.position.distanceTo(rest.get(bone)!.position)).toBeGreaterThan(0.1);
    expect(bone.scale.equals(rest.get(bone)!.scale)).toBe(false);
    expect(bone.quaternion.angleTo(rest.get(bone)!.rotation)).toBeGreaterThan(0.1);
    restorePose(rest);
    expect(bone.position.toArray()).toEqual([0, 1, 0]);
    expect(bone.scale.toArray()).toEqual([1, 1, 1]);
    expect(bone.quaternion.toArray()).toEqual([0, 0, 0, 1]);
  });

  it("blends to the complete reference pose without changing its snapshots", () => {
    const bone = new THREE.Bone();
    const standing = capturePose([bone]);
    bone.position.set(2, 0, 0); bone.scale.setScalar(2); bone.rotation.y = Math.PI / 2;
    const running = capturePose([bone]);
    blendPose(running, standing, 0.5);
    expect(bone.position.x).toBeCloseTo(1);
    expect(bone.scale.x).toBeCloseTo(1.5);
    expect(bone.quaternion.angleTo(standing.get(bone)!.rotation)).toBeCloseTo(Math.PI / 4);
    blendPose(running, standing, 1);
    expect(bone.position.toArray()).toEqual([0, 0, 0]);
    expect(bone.scale.toArray()).toEqual([1, 1, 1]);
    expect(running.get(bone)!.position.x).toBe(2);
  });
});
