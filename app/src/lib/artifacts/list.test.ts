import { describe, expect, it } from "vitest";
import { previewOf, typeLabel } from "./list";

describe("previewOf", () => {
  it("previews raster pictures, audio and video only", () => {
    expect(previewOf("image/png")).toBe("image");
    expect(previewOf("image/webp")).toBe("image");
    expect(previewOf("audio/mpeg")).toBe("audio");
    expect(previewOf("video/mp4")).toBe("video");
  });

  it("never draws an SVG, a PDF or anything else in place", () => {
    for (const type of ["image/svg+xml", "application/pdf", "text/csv", "application/octet-stream", ""]) {
      expect(previewOf(type), type).toBe("none");
    }
  });
});

describe("typeLabel", () => {
  it("names a file by its subtype", () => {
    expect(typeLabel("application/pdf")).toBe("PDF");
    expect(typeLabel("image/svg+xml")).toBe("SVG");
    expect(typeLabel("audio/mpeg")).toBe("MP3");
    expect(typeLabel("application/octet-stream")).toBe("FILE");
    expect(typeLabel("application/x-7z-compressed")).toBe("7Z-COMPR");
  });
});
