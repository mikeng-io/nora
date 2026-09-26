import { Config } from "@remotion/cli/config";

// Stills are checked by eye as PNG; video frames go through JPEG at 95.
Config.setVideoImageFormat("jpeg");
Config.setJpegQuality(95);
Config.setConcurrency(6);
Config.setChromiumOpenGlRenderer("angle");
