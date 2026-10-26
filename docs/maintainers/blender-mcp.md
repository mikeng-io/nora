# Blender MCP for Mini Nora postproduction

The project `.mcp.json` and `.codex/config.toml` launch the same pinned stdio
server, separate from Nora's runtime. Requires `uvx` on the client's PATH:

```sh
BLENDER_HOST=127.0.0.1 BLENDER_PORT=9876 DISABLE_TELEMETRY=true uvx mcp-for-blender==2.2.4
```

The MCP client normally starts this command; it does not start Blender. `uvx`
resolves the package into its external cache, with no repo dependency added.
If a GUI client cannot find `uvx`, use the path returned by `command -v uvx`
in that client's launch setting. Telemetry is disabled by the server's supported
environment switch. No API key is needed for local Blender work.

## Start and read back

1. Use the existing Blender GUI; if none is running, launch
   `/opt/homebrew/bin/blender`. Do not start a second instance on the same port.
2. In Preferences → Add-ons, enable the existing **DSH Blender Bridge**.
   Its installed file is
   `~/Library/Application Support/Blender/5.2/scripts/addons/dsh_blender_bridge.py`.
   In the 3D Viewport press N, open DSH Blender Bridge, set port 9876 and click
   **Connect to MCP server** if it has not auto-started. The installed bridge
   binds `localhost`; keep access on loopback, matching `127.0.0.1` above.
3. Reload the project's MCP client/session so it discovers `blender`. Complete
   MCP `initialize`, then `tools/list`. Call `get_addon_status` and
   `get_scene_info`; a real scene summary is the positive connection check.
4. The agent can import an agreed working copy via `execute_blender_code`,
   then save a separate `.blend` outside the checkout. Call `get_scene_info`
   again and `look` for a viewport image. Use `execute_blender_code` only for
   agreed inspection or editing. It maps to the bridge's `execute_code` command;
   `look` uses `get_viewport_screenshot`. Do not invoke generators or paid tasks.

Do **not** run upstream `setup`, `install-addon` or `update` here: preserve the
installed DSH bridge and unrelated client configs. Stop the bridge with
**Disconnect** when finished. The socket allows Python execution in Blender;
do not expose it beyond localhost.

## Compatibility and measured limit

On 2026-10-26, package metadata confirmed `mcp-for-blender` **2.2.4**. One
stdio smoke session passed initialize, listed nine tools, and called
`get_addon_status` and `get_scene_info` against the live GUI. Status reported
DSH addon **1.10**, protocol **11**, Blender **5.2.2 LTS**; scene readback
returned the default cube, camera and light. The package's MCP initialize
response reports SDK version **1.30.0**, independently of the package pin.

The parent subsequently verified the same pinned server end to end:
`execute_blender_code` imported the original GLB as `MiniNora_Source` and saved
`mini-nora-work.blend` outside Git. Import reported **888,105 vertices**,
**1,646,720 triangles** and no armatures. A separate `get_scene_info` confirmed
the imported mesh selected and the preserved default Cube hidden.
`look(mode="viewport", target=["MiniNora_Source"], shading="material", max_size=1000)`
returned a valid image, which the parent opened and confirmed as Mini Nora.
The original GLB's SHA256 still matched the trial receipt. This covered scene
import/save and viewport readback, with no geometry edits or paid tasks.
Receipts remain outside Git in the local artifact directory's `blender-mcp/`:
`import-response.json`, `scene-after.json`, `look-response.json`, `viewport.png`.

The server expects upstream protocol **15**, so status says `up_to_date=false`
and recommends an addon replacement. Leave that recommendation unapplied.
Source inspection confirms matching JSON `type`/`params` requests and
`status`/`result` responses for the core commands. Scene readback exercised
the server's observation script through `execute_code`. Core readback, model
import/save and viewport capture are verified; other protocol-15-specific
features and character deformation remain unverified.

References: [upstream setup and telemetry](https://github.com/ahujasid/mcp-for-blender),
[pinned package](https://pypi.org/project/mcp-for-blender/2.2.4/),
[DSH bridge documentation](https://github.com/sixtysevenlf/dsh-blender-plugin).

## Mini Nora working sequence

1. Preserve the original GLB outside Git and save a separate `.blend` working
   copy outside the repo. Set a local artifact path, for example
   `MINI_NORA_ASSET_DIR="$HOME/nora-artifacts/mini-nora"`, containing
   `mini-nora.glb`; save the working file as `mini-nora-work.blend` there.
   The inspection brief records about 49 MiB, one mesh, a 2K base-color texture
   and no shape keys. MCP import confirmed 888,105 vertices, 1,646,720 triangles
   and no armatures.
2. Clean the unintended thin surface strand between chin and collar, raised
   hair geometry, hair texture color contamination, and excessive stiff sleeve
   and trouser folds. Inspect from multiple angles; retain intended details.
3. Establish deformable topology, usable UVs and sensible mesh density before
   binding. Simplification reduces polygon count; it does not create the joint
   edge flow that retopology provides. Retopologize deforming areas as needed,
   preserving or transferring textures and baking detail from the source.
4. Build the skeleton/armature and place joints anatomically, with useful axes,
   hierarchy and rest pose: head/neck, shoulders/elbows/wrists, hips/knees/ankles.
   Joint placement defines the skeleton; it does not assign mesh influence.
5. Skin/bind the mesh to deform bones, then correct weights around joints.
   Automatic weights are a starting point; inspect unweighted vertices,
   unwanted cross-body influence and collapsing shoulders or knees.
6. Test head turns, shoulder lifts and knee bends through their intended range;
   establish and test facial controls/shape keys as required. Correct topology,
   joints or weights where the deformation reveals a defect.
7. Export GLB for the intended renderer; use a suitable VRM exporter and humanoid
   mapping/expressions if VRM is required. Reimport into a fresh scene or the
   target viewer and check scale, textures, skeleton and animation. Export is
   not acceptance until this readback passes.

This procedure authorizes no source edits or further paid generation by itself.
