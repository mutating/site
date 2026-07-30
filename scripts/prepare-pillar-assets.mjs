import fs from "node:fs/promises";
import path from "node:path";
import sharp from "sharp";

const assetPaths = [
  "public/pillar-verifiability-v1.png",
  "public/pillar-interpretability-v1.png",
  "public/pillar-testing-speed-v1.png",
  "public/pillar-testing-speed-rotor-v2.png",
  "public/pillar-testing-speed-rotor-v4.png",
];
const requestedPaths = process.argv.slice(2);
const pathsToPrepare = requestedPaths.length > 0 ? requestedPaths : assetPaths;

function removeGreenSpill(data, width, height, channels, warmRightEdge = false) {
  for (let pixel = 0; pixel < width * height; pixel += 1) {
    const offset = pixel * channels;
    const x = pixel % width;
    const red = data[offset];
    const green = data[offset + 1];
    const blue = data[offset + 2];
    const alpha = data[offset + 3];

    if (alpha === 0) {
      data[offset] = 0;
      data[offset + 1] = 0;
      data[offset + 2] = 0;
      continue;
    }

    const greenExcess = green - Math.max(red, blue);
    const contaminatedRightEdge = warmRightEdge
      && x > width * 0.68
      && (red - green < 32 || red - blue < 28);

    if (contaminatedRightEdge) {
      const luminance = Math.min(
        255,
        Math.round(red * 0.24 + green * 0.58 + blue * 0.18),
      );
      data[offset] = Math.min(255, Math.round(luminance * 1.18));
      data[offset + 1] = Math.min(255, Math.round(luminance * 0.8));
      data[offset + 2] = Math.min(255, Math.round(luminance * 0.88));
      continue;
    }

    if (greenExcess <= 3 || green < 20) continue;

    const luminance = Math.min(
      255,
      Math.round(red * 0.24 + green * 0.58 + blue * 0.18),
    );

    data[offset] = Math.min(255, Math.max(red, Math.round(luminance * 1.04)));
    data[offset + 1] = Math.min(green, Math.round(luminance * 0.78));
    data[offset + 2] = Math.min(255, Math.max(blue, Math.round(luminance * 0.94)));
  }
}

for (const relativePath of pathsToPrepare) {
  const assetPath = path.resolve(relativePath);
  const decoded = await sharp(assetPath)
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });

  removeGreenSpill(
    decoded.data,
    decoded.info.width,
    decoded.info.height,
    decoded.info.channels,
    relativePath.includes("verifiability"),
  );

  const processed = await sharp(decoded.data, {
    raw: {
      width: decoded.info.width,
      height: decoded.info.height,
      channels: decoded.info.channels,
    },
  })
    .trim({
      background: { r: 0, g: 0, b: 0, alpha: 0 },
      threshold: 8,
    })
    .extend({
      top: 12,
      right: 12,
      bottom: 12,
      left: 12,
      background: { r: 0, g: 0, b: 0, alpha: 0 },
    })
    .png()
    .toBuffer();

  const temporaryPath = `${assetPath}.prepared.png`;
  await fs.writeFile(temporaryPath, processed);
  await fs.rename(temporaryPath, assetPath);
}

console.log(`Prepared ${pathsToPrepare.length} transparent pillar assets.`);
